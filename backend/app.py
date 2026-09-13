"""
Try-on proxy backend.

Every request goes through Gemini 2.5 Flash Image ("nano banana") on Vertex AI, so
usage draws on the project's Google Cloud credits rather than an AI Studio key (the
$300 trial credit explicitly does not cover AI Studio). On Cloud Run this authenticates
as the service account via ADC, so there is no API key to leak.

Set GEMINI_API_KEY instead to fall back to AI Studio (useful for local runs without ADC).
"""
import asyncio
import io
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, ImageChops, ImageOps

load_dotenv()

# Vertex AI (preferred - draws on Cloud credits, authenticates via ADC).
GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
# AI Studio fallback, used only when GCP_PROJECT is unset.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Shared PIN for shop staff. The service URL is public, so without this anyone who finds
# it could run generations on our account.
SHOP_PIN = os.environ.get("SHOP_PIN")
# Where generations are archived for later review. Unset = archiving off.
STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET")

app = FastAPI(title="Anupam AI")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Aspect ratios gemini-2.5-flash-image can emit; we snap the customer photo to the
# closest one so the result isn't reframed to a square.
SUPPORTED_ASPECT_RATIOS = {
    "1:1": 1 / 1,
    "2:3": 2 / 3,
    "3:2": 3 / 2,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "4:5": 4 / 5,
    "5:4": 5 / 4,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}


def closest_aspect_ratio(img: Image.Image) -> str:
    ratio = img.width / img.height
    return min(SUPPORTED_ASPECT_RATIOS, key=lambda k: abs(SUPPORTED_ASPECT_RATIOS[k] - ratio))


def looks_unchanged(original: Image.Image, result: Image.Image, threshold: float = 6.0) -> bool:
    """True if the model echoed the input photo back instead of applying the garment.

    Gemini intermittently no-ops on this task, returning the customer photo essentially
    untouched. Comparing downscaled greyscale versions catches that cheaply - a real
    try-on changes a large area of the torso, well above this threshold.
    """
    size = (64, 64)
    a = original.convert("L").resize(size)
    b = result.convert("L").resize(size)
    diff = ImageChops.difference(a, b)
    mean_diff = sum(i * n for i, n in enumerate(diff.histogram())) / (size[0] * size[1])
    return mean_diff < threshold


# Appended to every prompt. Deliberately permissive about skin that a garment normally
# shows (a saree shows midriff, a sleeveless kurta shows shoulders) - the aim is a
# result that is decent to show a customer in the shop, not a covered-up one that
# misrepresents what they are buying.
MODESTY_RULES = (
    "\n\nThis is an ordinary retail clothing try-on, like a customer using a fitting "
    "room mirror. Render the garment faithfully and tastefully:\n"
    "- Reproduce the garment's own neckline, hemline and cut exactly as designed. "
    "Everyday necklines - V-neck, scoop, boat, sweetheart, a normal amount of "
    "décolletage - are perfectly fine and should be rendered as they are. Likewise "
    "bare arms and shoulders on sleeveless items, legs on shorts, skirts and dresses, "
    "and the midriff on a saree or lehenga. Do not add extra covering, and do not "
    "raise a neckline or lengthen a hem that the garment does not have.\n"
    "- Do not go further than the garment does: never make it tighter, shorter, "
    "lower-cut or more sheer than in Image 2, and do not change the person's body "
    "shape or pose to be suggestive. A neutral, natural standing pose.\n"
    "- The garment should be worn properly - sitting correctly on the body, not "
    "slipping off, falling open or mid-removal.\n"
    "- The only hard limit: no nudity or exposed private parts. The garment itself, "
    "as designed, always stays on."
)


# Garments that are a complete outfit rather than just a top, so the whole of the
# customer's existing clothing gets replaced instead of only the upper body.
FULL_OUTFIT_CATEGORIES = {
    "saree", "salwar_suit", "anarkali", "lehenga", "churidar", "palazzo_suit",
    "kurta_pyjama", "sherwani", "suit", "dress", "gown", "co-ord_set",
    "jumpsuit", "abaya",
}

# Garments worn on the lower body only.
BOTTOM_CATEGORIES = {
    "trousers", "jeans", "cargo_pants", "shorts", "skirt", "palazzo", "leggings",
    "salwar", "pyjama", "track_pants", "dhoti",
}


# How these garments are actually constructed. Without this the model treats them as
# upper-body layers and leaves the customer's own trousers showing underneath.
GARMENT_NOTES = {
    "saree": (
        "A saree is draped over a fitted blouse: pleats tucked at the waist falling "
        "straight down to the ankles, and the pallu draped over one shoulder. The drape "
        "covers both legs completely, all the way down."
    ),
    "lehenga": (
        "A lehenga is a fitted choli (short blouse) with a separate long flared skirt "
        "reaching the ankles, plus a dupatta. The skirt covers the legs entirely."
    ),
    "salwar_suit": (
        "A salwar suit is a long kameez (tunic, at least knee length) worn over loose "
        "salwar trousers that reach the ankles, usually with a dupatta. Render every "
        "piece - the trousers are part of the outfit, not the customer's own."
    ),
    "churidar": (
        "A churidar suit is a long kameez over tight-fitting churidar trousers gathered "
        "at the ankles, usually with a dupatta. Render all pieces."
    ),
    "anarkali": (
        "An Anarkali is a long flared kurta, fitted to the bust and flaring out below "
        "it down to the ankles, usually with churidar and a dupatta."
    ),
    "palazzo_suit": (
        "A palazzo suit is a kurti worn over wide-legged palazzo trousers reaching the "
        "ankles. Render both pieces."
    ),
    "sherwani": (
        "A sherwani is a long fitted coat falling below the knees, worn over churidar "
        "or fitted trousers."
    ),
    "kurta_pyjama": (
        "A kurta (around knee length) with matching pyjama trousers to the ankles. "
        "Render both pieces."
    ),
    "gown": "A floor-length gown that covers the legs entirely.",
    "jumpsuit": "A one-piece jumpsuit covering torso and legs to the ankles.",
}


def replacement_rule(category: str) -> str:
    """How much of the customer's existing clothing this garment should replace."""
    if category in FULL_OUTFIT_CATEGORIES:
        rule = (
            "- THIS IS A FULL-BODY OUTFIT, NOT A TOP. It must clothe the person from "
            "shoulders to ankles. Every piece of what they are currently wearing is "
            "replaced: their existing trousers, jeans, leggings or skirt must be "
            "completely gone and must not remain visible anywhere - not at the sides, "
            "not below the hem, not through gaps in the drape, not behind their legs. "
            "Render the outfit at its natural full length, and show the person's whole "
            "figure down to their feet wherever the photo allows.\n"
        )
        note = GARMENT_NOTES.get(category)
        return rule + (f"- {note}\n" if note else "")
    if category in BOTTOM_CATEGORIES:
        return (
            "- This is a lower-body garment. Replace the trousers/skirt the person is "
            "currently wearing with it, and leave whatever they are wearing on their "
            "upper body completely unchanged.\n"
        )
    return (
        "- The person must end up wearing ONLY this garment on their upper body. "
        "Completely remove any shirt, t-shirt, polo or other top they are currently "
        "wearing underneath it. If the garment is sleeveless, their bare arms and "
        "shoulders must be visible - no sleeves from another top showing. Leave their "
        "lower-body clothing unchanged.\n"
    )


def build_client() -> genai.Client:
    """Vertex AI when a project is configured, otherwise an AI Studio key."""
    if GCP_PROJECT:
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    if GEMINI_API_KEY:
        return genai.Client(api_key=GEMINI_API_KEY)
    raise HTTPException(500, "Neither GCP_PROJECT nor GEMINI_API_KEY is configured")


def archive_generation(
    customer_bytes: bytes,
    garment_bytes: bytes,
    result_bytes: bytes | None,
    meta: dict,
) -> None:
    """Save one generation to GCS for later review and prompt tuning.

    Best-effort: archiving must never fail or slow down a customer-facing request, so
    every error here is swallowed. Images plus a small JSON sidecar per generation,
    partitioned by date - cheap to store, and BigQuery can read the JSON as an external
    table if we ever want to query it with SQL.
    """
    if not STORAGE_BUCKET:
        return
    try:
        from google.cloud import storage

        client = storage.Client(project=GCP_PROJECT) if GCP_PROJECT else storage.Client()
        bucket = client.bucket(STORAGE_BUCKET)
        prefix = f"{datetime.now(timezone.utc):%Y/%m/%d}/{meta['id']}"

        bucket.blob(f"{prefix}/customer.jpg").upload_from_string(
            customer_bytes, content_type="image/jpeg")
        bucket.blob(f"{prefix}/garment.jpg").upload_from_string(
            garment_bytes, content_type="image/jpeg")
        if result_bytes:
            bucket.blob(f"{prefix}/result.jpg").upload_from_string(
                result_bytes, content_type="image/jpeg")
        bucket.blob(f"{prefix}/meta.json").upload_from_string(
            json.dumps(meta), content_type="application/json")
    except Exception as e:  # noqa: BLE001 - never break a request over archiving
        print(f"archive failed for {meta.get('id')}: {e}", flush=True)


# Styling the face the way a customer would style it for the outfit. The hard line is
# cosmetics only: makeup is something she puts on, facial structure and complexion are
# who she is. Models drift towards lightening skin when asked to make a face "prettier",
# which is both wrong and self-defeating - she has to recognise herself for the preview
# to sell her the outfit.
IDENTITY_LOCK = (
    "Do not change her facial structure or proportions - same nose, jawline, cheeks, "
    "eye shape and spacing, face width, hairline and apparent age. Above all, keep her "
    "natural skin tone and complexion exactly as it is: do not lighten, whiten, smooth "
    "away her natural colour or change her undertone. She must be instantly "
    "recognisable as herself, simply well made up."
)

MAKEUP_FEMALE = (
    "\n\nStyle her face as if she had done her makeup to wear this outfit for a special "
    "occasion - a wedding, a festival, a family function:\n"
    "- Evenly applied base in her OWN skin tone, softening shine and minor blemishes "
    "but keeping her natural texture and any freckles or moles.\n"
    "- Defined, natural brows; kajal or soft liner and neutral eyeshadow; subtle blush "
    "and highlight on the cheekbones; a lip colour that complements the garment.\n"
    "- Neatly styled hair, in the same haircut and colour she already has.\n"
    "- Tasteful and wearable, not heavy or theatrical. " + IDENTITY_LOCK
)

MAKEUP_MALE = (
    "\n\nGive him a lightly groomed, photographed-well look - no visible cosmetics of "
    "any kind:\n"
    "- Tidy hair in his existing style, neatened beard or stubble as he already wears "
    "it, slightly reduced skin shine, softened minor blemishes and under-eye tiredness.\n"
    "- Natural and masculine, as if simply well rested and well lit. Nothing that reads "
    "as makeup.\n"
    "- Do not change his facial structure or proportions - same nose, jawline, eye "
    "shape, face width, hairline and apparent age - and keep his natural skin tone and "
    "complexion exactly as it is, with no lightening or whitening. He must be instantly "
    "recognisable as himself."
)


def build_prompt(gender: str, category: str, makeup: bool = True) -> str:
    """Assemble the instruction for one try-on.

    Full outfits get an explicit erase-then-repaint framing. Asking the model to
    "replace" clothing while also preserving the photo made it treat a saree or lehenga
    as an upper-body layer and leave the customer's own trousers showing; telling it to
    clear the area below the neck first and then paint the outfit in fixed that.
    """
    label = category.replace("_", " ")
    note = GARMENT_NOTES.get(category)

    if category in FULL_OUTFIT_CATEGORIES:
        task = (
            f"Dress the {gender} person in Image 1 in the {label} shown in Image 2.\n\n"
            "Step 1: Erase every piece of clothing they are currently wearing. Treat the "
            "whole area below the neck as empty and repaint it - their existing top, "
            "trousers, jeans, leggings or skirt must be completely gone.\n"
            f"Step 2: Paint them wearing the {label} from Image 2. "
            + (note + " " if note else "")
            + "It clothes them from shoulders to ankles, and none of their original "
            "clothing is visible anywhere - not at the sides, below the hem, or through "
            "gaps in the drape.\n\n"
            "Changing their clothes is the whole point of this edit: where preserving "
            "the original photo conflicts with fully replacing the outfit, replace it.\n"
        )
    elif category in BOTTOM_CATEGORIES:
        task = (
            f"Dress the {gender} person in Image 1 in the {label} shown in Image 2.\n\n"
            f"Replace the trousers/skirt they are currently wearing with this {label}, "
            + (note + " " if note else "")
            + "so none of their original lower-body clothing remains visible. Leave "
            "whatever they are wearing on their upper body completely untouched.\n"
        )
    else:
        task = (
            f"Dress the {gender} person in Image 1 in the {label} shown in Image 2.\n\n"
            "Remove the top they are currently wearing completely and paint them "
            f"wearing this {label} instead - no collar, sleeves or hem of their old top "
            "showing underneath or at the edges. If it is sleeveless, their bare arms "
            "and shoulders show. Leave their lower-body clothing unchanged.\n"
        )

    # Body, pose and background are always preserved; the face is only ever restyled
    # (never restructured), so what we promise to keep identical differs slightly.
    keep = (
        "\nKeep identical: their body shape, proportions, posture and pose, and the "
        "background behind them. Keep their face their own - see the styling note "
        "below.\n"
        if makeup else
        "\nKeep identical: their face, hair, skin tone, body shape, proportions, "
        "posture and pose, and the background behind them.\n"
    )
    return (
        task
        + keep
        + "Reproduce the garment exactly as in Image 2 - same colour, fabric, pattern, "
        "embroidery and its placement, sleeve length, neckline and hem length. Do not "
        "substitute a similar-looking garment. Scale it to fit their body naturally.\n"
        "Image 2 may show the garment several times (angles, a collage, a mirror) or on "
        "a mannequin or hanger, and may be a screenshot with watermarks, logos, price "
        "tags or app buttons. Treat it as one garment, use the clearest view, and never "
        "draw any watermark or text onto the result.\n"
        "Photorealistic, same aspect ratio and orientation as Image 1."
        + MODESTY_RULES
        + ((MAKEUP_MALE if gender.lower().startswith("m") else MAKEUP_FEMALE)
           if makeup else "")
    )


def call_model(client, prompt, customer_img, garment_img, config, tries: int = 4):
    """generate_content with backoff on quota errors.

    The project's per-minute image quota is small, and the UI fires both garments at
    once, so short bursts hit 429 even though overall volume is low. Waiting briefly
    clears it; only give up if it persists.
    """
    delay = 2.0
    for i in range(tries):
        try:
            return client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[prompt, customer_img, garment_img],
                config=config,
            )
        except genai_errors.ClientError as e:
            quota_hit = getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e)
            if quota_hit and i < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            if quota_hit:
                raise HTTPException(
                    429,
                    "Too many requests just now - the daily/per-minute image quota is "
                    "full. Wait a moment and try again.",
                ) from e
            raise HTTPException(502, f"Image service error: {str(e)[:200]}") from e
        except genai_errors.ServerError as e:
            if i < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise HTTPException(502, "The image service is unavailable. Try again.") from e


def run_gemini_tryon(
    customer_path: str,
    garment_path: str,
    gender: str,
    category: str,
    makeup: bool = True,
    attempts: int = 2,
) -> Image.Image:
    client = build_client()
    # Phone photos carry EXIF orientation rather than being stored upright. Without
    # applying it, a portrait photo reads as landscape - so we'd send it sideways and
    # ask for a landscape aspect ratio back.
    customer_img = ImageOps.exif_transpose(Image.open(customer_path))
    garment_img = ImageOps.exif_transpose(Image.open(garment_path))

    prompt = build_prompt(gender, category, makeup)

    config = types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio=closest_aspect_ratio(customer_img)),
    )

    last_result: Image.Image | None = None
    refusal: str | None = None
    for attempt in range(attempts):
        response = call_model(client, prompt, customer_img, garment_img, config)

        # On a hard safety block there may be no candidates at all, or a candidate whose
        # content/parts are None - so nothing here can be indexed or iterated blindly.
        candidate = (response.candidates or [None])[0]
        if candidate is None:
            blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
            refusal = refusal or (f"blocked ({blocked})" if blocked else None)
            continue

        finish = getattr(candidate, "finish_reason", None)
        if finish is not None and str(finish).endswith("SAFETY"):
            refusal = refusal or "the safety filter blocked this combination"

        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        result = None
        for part in parts:
            if part.inline_data is not None:
                result = Image.open(io.BytesIO(part.inline_data.data))
            elif part.text and part.text.strip(" `\n"):
                # Gemini explains itself in text when it declines to edit the photo.
                refusal = part.text.strip()
        if result is None:
            continue

        last_result = result
        if not looks_unchanged(customer_img, result):
            return result
        # Otherwise the model echoed the photo back - try once more.

    if last_result is not None:
        return last_result
    if refusal:
        # Usually a safety decline (e.g. photos of children) - tell staff that plainly
        # rather than showing them a bare server error.
        raise HTTPException(422, f"Couldn't generate this look: {refusal[:300]}")
    raise HTTPException(502, "The image service didn't return an image. Please try again.")


@app.post("/generate")
async def generate(
    customer_image: UploadFile = File(...),
    garment_image: UploadFile = File(...),
    gender: str = Form(...),
    category: str = Form(...),
    makeup: bool = Form(True),
    x_shop_pin: str | None = Header(default=None),
):
    if SHOP_PIN and x_shop_pin != SHOP_PIN:
        raise HTTPException(401, "Wrong shop PIN")

    category = category.lower().strip()
    # A private directory per request: the UI submits several garments against the same
    # customer photo at once, so filename-based paths would collide between them and one
    # request's cleanup would delete a file another was still reading.
    tmp_dir = tempfile.mkdtemp(prefix="tryon-")
    customer_path = os.path.join(tmp_dir, "customer")
    garment_path = os.path.join(tmp_dir, "garment")

    meta = {
        "id": uuid.uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "gender": gender,
        "model": "gemini-2.5-flash-image",
        "backend": "vertex" if GCP_PROJECT else "ai_studio",
        "makeup": makeup,
    }
    started = time.monotonic()

    try:
        customer_bytes = await customer_image.read()
        garment_bytes = await garment_image.read()
        with open(customer_path, "wb") as f:
            f.write(customer_bytes)
        with open(garment_path, "wb") as f:
            f.write(garment_bytes)

        # The Gemini SDK call is blocking; off-loading it keeps the event loop free so
        # the several garments the UI submits at once actually run in parallel.
        try:
            result_img = await asyncio.to_thread(
                run_gemini_tryon, customer_path, garment_path, gender, category, makeup
            )
        except HTTPException as e:
            # Failures are the most useful thing to study later, so archive them too.
            meta |= {"ok": False, "status": e.status_code, "error": str(e.detail)[:500],
                     "seconds": round(time.monotonic() - started, 1)}
            asyncio.create_task(
                asyncio.to_thread(archive_generation, customer_bytes, garment_bytes, None, meta)
            )
            raise

        buf = io.BytesIO()
        result_img.convert("RGB").save(buf, format="JPEG", quality=92)
        result_bytes = buf.getvalue()

        meta |= {"ok": True, "seconds": round(time.monotonic() - started, 1),
                 "result_size": list(result_img.size)}
        # Fire-and-forget so the associate gets their image without waiting on uploads.
        asyncio.create_task(
            asyncio.to_thread(archive_generation, customer_bytes, garment_bytes, result_bytes, meta)
        )
        return Response(content=result_bytes, media_type="image/jpeg")
    finally:
        # The working copies go immediately; the archive (if enabled) is the retained one.
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/health")
async def health():
    return {"status": "ok"}
