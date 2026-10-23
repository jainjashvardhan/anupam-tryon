"""The read endpoint's request handling (ADR-120): one structured-output call to Gemini.

A thin pipe. The shop system builds the prompt and checks the answer; this only validates the
shape of the request, forwards it once, and returns the JSON with model, tokens and latency.
Nothing about the input is ever logged, echoed in an error, or retried: a read that fails
gives its slot back (app.py) and the person presses the button again.
"""

import base64
import binascii
import json
import time
from dataclasses import dataclass

from google.genai import errors as genai_errors
from google.genai import types

# Gemini's inline-data limit for one request.
MAX_BODY_BYTES = 20 * 1024 * 1024

# What the shop system may send inline: its own text, a recorded clip, a bill photo.
INLINE_MIMES = frozenset({"audio/webm", "image/jpeg"})

RESOLUTIONS = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}


class ExtractError(Exception):
    """A refusal or failure, as the status and the plain sentence the person sees."""

    def __init__(self, status: int, sentence: str) -> None:
        super().__init__(sentence)
        self.status = status
        self.sentence = sentence


@dataclass(frozen=True)
class Request:
    model: str
    prompt: str
    parts: tuple[str | types.Part, ...]
    schema: dict
    media_resolution: types.MediaResolution | None


@dataclass(frozen=True)
class Answer:
    result: object
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


def check_size(n_bytes: int) -> None:
    if n_bytes > MAX_BODY_BYTES:
        raise ExtractError(413, "That is too much to read at once. Send fewer or smaller pages.")


def _bad(field: str, why: str) -> ExtractError:
    return ExtractError(400, f"The read request is wrong: {field} {why}.")


def _part(raw: object) -> str | types.Part:
    if not isinstance(raw, dict):
        raise _bad("parts", "must each be an object")
    if "text" in raw:
        if not isinstance(raw["text"], str) or not raw["text"].strip():
            raise _bad("parts", "text must not be empty")
        return raw["text"]
    mime = raw.get("mime")
    if mime not in INLINE_MIMES:
        raise _bad("parts", f"mime {mime!r} is not allowed (audio/webm or image/jpeg)")
    try:
        data = base64.b64decode(str(raw.get("data", "")), validate=True)
    except (binascii.Error, ValueError):
        raise _bad("parts", "data must be base64") from None
    if not data:
        raise _bad("parts", "data must not be empty")
    return types.Part.from_bytes(data=data, mime_type=mime)


def parse(body: object, allowed_models: set[str] | frozenset[str]) -> Request:
    if not isinstance(body, dict):
        raise _bad("the body", "must be a JSON object")
    model = body.get("model")
    if model not in allowed_models:
        raise _bad("model", "is not one this service reads with")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _bad("prompt", "must not be empty")
    raw_parts = body.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise _bad("parts", "must be a list with at least one item")
    schema = body.get("schema")
    if not isinstance(schema, dict):
        raise _bad("schema", "must be a JSON schema object")
    res = body.get("media_resolution")
    if res is not None and res not in RESOLUTIONS:
        raise _bad("media_resolution", "must be low, medium or high")
    return Request(
        model=model,
        prompt=prompt,
        parts=tuple(_part(p) for p in raw_parts),
        schema=schema,
        media_resolution=RESOLUTIONS.get(res),
    )


def _failure(exc: Exception) -> ExtractError:
    """One sentence per kind of failure. Only the exception's class is looked at: its message
    may quote the request."""
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return ExtractError(504, "Reading took too long. Try again.")
    if isinstance(exc, genai_errors.ClientError) and getattr(exc, "code", None) == 429:
        return ExtractError(429, "Too many reads just now. Wait a moment and try again.")
    return ExtractError(502, "The reading service is unavailable. Try again.")


def run(client, req: Request, *, timeout_ms: int) -> Answer:
    """Call Gemini once. No retry: the timeout is the SDK's own HTTP timeout."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=req.schema,
        media_resolution=req.media_resolution,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )
    started = time.monotonic()
    try:
        response = client.models.generate_content(
            model=req.model, contents=[req.prompt, *req.parts], config=config
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a sentence; the message may quote the input
        raise _failure(exc) from None
    latency_ms = round((time.monotonic() - started) * 1000)

    try:
        result = json.loads(response.text or "")
    except (TypeError, ValueError):
        raise ExtractError(502, "The reading service gave no usable answer. Try again.") from None
    usage = getattr(response, "usage_metadata", None)
    return Answer(
        result=result,
        model=req.model,
        tokens_in=getattr(usage, "prompt_token_count", 0) or 0,
        tokens_out=getattr(usage, "candidates_token_count", 0) or 0,
        latency_ms=latency_ms,
    )
