"""The read endpoint's request handling (extract.py): run with `python -m pytest backend/test_extract.py`.

No Gemini here: a fake client stands in, so these cost nothing and send nothing anywhere.
"""

import base64
from types import SimpleNamespace

import pytest

import extract
from extract import ExtractError

MODELS = {"gemini-3.8-flash", "gemini-3.5-flash-lite"}
SCHEMA = {"type": "object", "properties": {"cards": {"type": "array"}}}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def body(**over) -> dict:
    base = {
        "model": "gemini-3.8-flash",
        "prompt": "Read the bill.",
        "parts": [{"text": "Sarthi se shirt aaye"}],
        "schema": SCHEMA,
        "media_resolution": "high",
    }
    return base | over


class FakeClient:
    def __init__(self, text: str | None = '{"cards": []}', raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._text, self._raises = text, raises
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._raises:
            raise self._raises
        usage = SimpleNamespace(prompt_token_count=1200, candidates_token_count=80)
        return SimpleNamespace(text=self._text, usage_metadata=usage)


def test_a_good_request_is_forwarded_once_with_structured_output() -> None:
    client = FakeClient()
    req = extract.parse(body(parts=[{"text": "hello"}, {"mime": "image/jpeg", "data": b64(b"jpg")}]), MODELS)
    answer = extract.run(client, req, timeout_ms=30_000)

    assert answer.result == {"cards": []}
    assert (answer.model, answer.tokens_in, answer.tokens_out) == ("gemini-3.8-flash", 1200, 80)
    assert answer.latency_ms >= 0
    (call,) = client.calls
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"][0] == "Read the bill."
    assert len(call["contents"]) == 3  # the prompt, the text, the photo
    cfg = call["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == SCHEMA
    assert cfg.http_options.timeout == 30_000
    assert str(cfg.media_resolution).endswith("HIGH")


def test_audio_and_photo_bytes_arrive_as_sent() -> None:
    req = extract.parse(
        body(parts=[{"mime": "audio/webm", "data": b64(b"clip")}, {"mime": "image/jpeg", "data": b64(b"jpg")}]),
        MODELS,
    )
    client = FakeClient()
    extract.run(client, req, timeout_ms=1000)
    audio, photo = client.calls[0]["contents"][1:]
    assert (audio.inline_data.mime_type, audio.inline_data.data) == ("audio/webm", b"clip")
    assert (photo.inline_data.mime_type, photo.inline_data.data) == ("image/jpeg", b"jpg")


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"model": "gemini-2.5-flash-image"}, "model"),
        ({"model": ""}, "model"),
        ({"prompt": ""}, "prompt"),
        ({"parts": []}, "parts"),
        ({"parts": [{"mime": "video/mp4", "data": b64(b"x")}]}, "video/mp4"),
        ({"parts": [{"mime": "image/jpeg", "data": "!!not base64!!"}]}, "base64"),
        ({"parts": [{"text": ""}]}, "parts"),
        ({"parts": ["just a string"]}, "parts"),
        ({"schema": "nope"}, "schema"),
        ({"media_resolution": "ultra"}, "media_resolution"),
    ],
)
def test_a_bad_request_is_refused_with_a_sentence_naming_the_field(over, words) -> None:
    with pytest.raises(ExtractError) as err:
        extract.parse(body(**over), MODELS)
    assert err.value.status == 400
    assert words in err.value.sentence


def test_a_body_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ExtractError) as err:
        extract.parse(["not", "an", "object"], MODELS)
    assert err.value.status == 400


def test_media_resolution_is_optional() -> None:
    req = extract.parse({k: v for k, v in body().items() if k != "media_resolution"}, MODELS)
    client = FakeClient()
    extract.run(client, req, timeout_ms=1000)
    assert client.calls[0]["config"].media_resolution is None


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (TimeoutError("deadline"), 504),
        (RuntimeError("boom"), 502),
    ],
)
def test_a_failed_call_is_one_sentence_and_not_retried(raised, status) -> None:
    client = FakeClient(raises=raised)
    req = extract.parse(body(), MODELS)
    with pytest.raises(ExtractError) as err:
        extract.run(client, req, timeout_ms=1000)
    assert err.value.status == status
    assert len(client.calls) == 1
    assert "Sarthi" not in err.value.sentence  # nothing about the input is echoed back


@pytest.mark.parametrize("text", [None, "", "not json at all"])
def test_an_answer_that_is_not_json_is_a_failure(text) -> None:
    req = extract.parse(body(), MODELS)
    with pytest.raises(ExtractError) as err:
        extract.run(FakeClient(text=text), req, timeout_ms=1000)
    assert err.value.status == 502


def test_a_body_over_the_limit_is_refused_before_it_is_read() -> None:
    with pytest.raises(ExtractError) as err:
        extract.check_size(extract.MAX_BODY_BYTES + 1)
    assert err.value.status == 413
    extract.check_size(extract.MAX_BODY_BYTES)
