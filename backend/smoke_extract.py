"""Smoke test for POST /extract: one synthetic bill photo and, if given, one recorded clip, read by
each candidate model. Prints only status, model, tokens and latency - never what was read.

  python smoke_extract.py --url https://<service>.run.app --pin <SHOP_PIN> [--audio clip.webm]
  GCP_PROJECT=... python smoke_extract.py --direct [--audio clip.webm]   # straight to Vertex, no counter

Each call costs a fraction of a rupee. Via --url it also takes a read slot from the day's 50.
A clip: `say -v Lekha "Sarthi se shirt aaye, M L XL" -o c.aiff && ffmpeg -i c.aiff -c:a libopus c.webm`.
"""

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw

MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")
SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "qty": {"type": "number"}},
                "required": ["name", "qty"],
            },
        },
    },
    "required": ["vendor", "lines"],
}
PROMPT = "Read the vendor and the item lines (name, quantity) from what is given. Answer in the schema."


def synthetic_bill() -> bytes:
    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(
        ["SARTHI TEXTILES  GSTIN 27AAAAA0000A1Z5", "Invoice 0042", "Cotton Shirt  M  12", "Cotton Shirt  L  18", "Kurta Set  XL  6"]
    ):
        d.text((40, 40 + i * 60), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def part(mime: str, raw: bytes) -> dict:
    return {"mime": mime, "data": base64.b64encode(raw).decode()}


def valid(result: object) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("vendor"), str)
        and isinstance(result.get("lines"), list)
        and all(isinstance(x, dict) and "name" in x and "qty" in x for x in result["lines"])
    )


def post(url: str, pin: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{url}/extract", json.dumps(body).encode(), {"Content-Type": "application/json", "X-Shop-Pin": pin}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def direct(body: dict) -> tuple[int, dict]:
    import app  # reads GCP_PROJECT / GCP_LOCATION from the environment

    import extract

    try:
        a = extract.run(app.build_client(), extract.parse(body, set(MODELS)), timeout_ms=30_000)
    except extract.ExtractError as e:
        return e.status, {"detail": e.sentence}
    return 200, {"result": a.result, "model": a.model, "tokens_in": a.tokens_in,
                 "tokens_out": a.tokens_out, "latency_ms": a.latency_ms}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--pin", default=os.environ.get("SHOP_PIN", ""))
    ap.add_argument("--audio")
    ap.add_argument("--direct", action="store_true")
    a = ap.parse_args()
    if not a.direct and not a.url:
        ap.error("give --url (and --pin) or --direct")

    inputs = {"bill photo": [part("image/jpeg", synthetic_bill())]}
    if a.audio:
        inputs["clip"] = [part("audio/webm", open(a.audio, "rb").read())]
    failed = 0
    for model in MODELS:
        for label, parts in inputs.items():
            body = {"model": model, "prompt": PROMPT, "parts": parts, "schema": SCHEMA, "media_resolution": "high"}
            t = time.monotonic()
            status, out = direct(body) if a.direct else post(a.url.rstrip("/"), a.pin, body)
            ok = status == 200 and valid(out.get("result"))
            failed += not ok
            note = (f"tokens {out.get('tokens_in')}/{out.get('tokens_out')}, {out.get('latency_ms')} ms"
                    if status == 200 else out.get("detail", ""))
            print(f"{'OK  ' if ok else 'FAIL'} {model:24} {label:10} HTTP {status}  {note}  ({time.monotonic() - t:.1f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
