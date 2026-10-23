"""POST /extract end to end through the app, with a memory counter and a fake Gemini client.

Run with `python -m pytest backend/test_extract_api.py`. Nothing here reaches Google.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import extract
from test_usage import MemoryStore

PIN = {"X-Shop-Pin": "1234"}
SECRET_WORDS = "Sarthi se shirt aaye"


def payload(**over) -> dict:
    base = {
        "model": "gemini-3.8-flash",
        "prompt": "Read it.",
        "parts": [{"text": SECRET_WORDS}],
        "schema": {"type": "object"},
    }
    return base | over


class FakeGemini:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"upstream said: {contents}")
        meta = SimpleNamespace(prompt_token_count=900, candidates_token_count=40)
        return SimpleNamespace(text='{"cards": []}', usage_metadata=meta)


@pytest.fixture
def shop(monkeypatch):
    store, gemini = MemoryStore(), FakeGemini()
    monkeypatch.setattr(app_module, "SHOP_PIN", "1234")
    monkeypatch.setattr(app_module, "_usage_store", store)
    monkeypatch.setattr(app_module, "build_client", lambda: gemini)
    return SimpleNamespace(client=TestClient(app_module.app), store=store, gemini=gemini)


def used_today(shop) -> dict:
    return shop.client.get("/usage", headers=PIN).json()


def test_a_read_returns_the_json_model_tokens_and_latency(shop) -> None:
    r = shop.client.post("/extract", json=payload(), headers=PIN)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == {"cards": []}
    assert (body["model"], body["tokens_in"], body["tokens_out"]) == ("gemini-3.8-flash", 900, 40)
    assert body["latency_ms"] >= 0
    assert used_today(shop)["reads_used_today"] == 1


def test_the_51st_read_in_a_day_is_refused_and_pictures_are_unaffected(shop) -> None:
    for _ in range(50):
        assert shop.client.post("/extract", json=payload(), headers=PIN).status_code == 200
    r = shop.client.post("/extract", json=payload(), headers=PIN)
    assert (r.status_code, r.json()["detail"]) == (429, "Today's 50 reads are used up. More tomorrow.")
    assert shop.gemini.calls == 50  # the refused read never reached Gemini
    u = used_today(shop)
    assert (u["reads_left_today"], u["used_today"], u["left_today"]) == (0, 0, 20)


def test_a_failed_read_gives_its_slot_back_and_is_not_retried(shop) -> None:
    shop.gemini.fail = True
    r = shop.client.post("/extract", json=payload(), headers=PIN)
    assert r.status_code == 502
    assert shop.gemini.calls == 1
    assert used_today(shop)["reads_used_today"] == 0
    assert SECRET_WORDS not in r.text


def test_a_bad_request_costs_no_slot_and_never_calls_gemini(shop) -> None:
    r = shop.client.post("/extract", json=payload(model="gemini-2.5-flash-image"), headers=PIN)
    assert r.status_code == 400
    assert shop.gemini.calls == 0
    assert used_today(shop)["reads_used_today"] == 0
    assert shop.client.post("/extract", content=b"not json", headers=PIN).status_code == 400


def test_the_shop_pin_is_required(shop) -> None:
    assert shop.client.post("/extract", json=payload()).status_code == 401
    assert shop.client.post("/extract", json=payload(), headers={"X-Shop-Pin": "0000"}).status_code == 401
    assert shop.gemini.calls == 0


def test_a_body_over_20_mb_is_refused_without_a_slot(shop, monkeypatch) -> None:
    monkeypatch.setattr(extract, "MAX_BODY_BYTES", 200)
    r = shop.client.post("/extract", content=json.dumps(payload(prompt="x" * 500)), headers=PIN)
    assert r.status_code == 413
    assert used_today(shop)["reads_used_today"] == 0


def test_nothing_about_the_input_is_printed(shop, capsys) -> None:
    shop.gemini.fail = True
    shop.client.post("/extract", json=payload(), headers=PIN)
    out = capsys.readouterr()
    assert SECRET_WORDS not in out.out + out.err
