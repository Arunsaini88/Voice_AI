# tests/test_summarize.py — shape-hardening of whatever Groq returns.
import json
from types import SimpleNamespace
import pytest
from app.services import summarize as svc


def _fake_groq(monkeypatch, content):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(svc.groq_client.chat.completions, "create", create)
    return calls


async def test_well_formed_reply(monkeypatch):
    calls = _fake_groq(monkeypatch, json.dumps({"title": " Dentist ", "summary": "Call them.", "key_points": ["a", " b "]}))
    out = await svc.summarize_transcript("call the dentist", "en")
    assert out == {"title": "Dentist", "summary": "Call them.", "key_points": ["a", "b"]}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "English" in calls[0]["messages"][0]["content"]


async def test_language_instruction_selected(monkeypatch):
    calls = _fake_groq(monkeypatch, "{}")
    await svc.summarize_transcript("x", "hinglish")
    assert "Hinglish" in calls[0]["messages"][0]["content"]
    await svc.summarize_transcript("x", "unknown-lang")
    assert "English" in calls[1]["messages"][0]["content"]


@pytest.mark.parametrize("content", ["not json at all", "", None, "[1, 2, 3]", '"just a string"'])
async def test_garbage_reply_still_yields_usable_record(monkeypatch, content):
    _fake_groq(monkeypatch, content)
    out = await svc.summarize_transcript("Remind me to call the dentist tomorrow at ten.", "en")
    assert out["title"].startswith("Remind me to call")
    assert out["summary"] is None
    assert out["key_points"] == []


async def test_wrong_types_are_coerced(monkeypatch):
    _fake_groq(monkeypatch, json.dumps({"title": {"nested": 1}, "summary": 42, "key_points": "single point"}))
    out = await svc.summarize_transcript("short transcript", "en")
    assert out["title"] == "short transcript"
    assert out["summary"] == "42"
    assert out["key_points"] == ["single point"]


async def test_key_points_filtered_and_capped(monkeypatch):
    _fake_groq(monkeypatch, json.dumps({"title": "t", "summary": "s", "key_points": [1, "", " ok ", None] + ["p"] * 30}))
    out = await svc.summarize_transcript("x", "en")
    assert out["key_points"][0] == "ok"
    assert len(out["key_points"]) == 20


async def test_long_transcript_title_is_truncated(monkeypatch):
    _fake_groq(monkeypatch, "{}")
    out = await svc.summarize_transcript("word " * 50, "en")
    assert len(out["title"]) <= 61 and out["title"].endswith("…")


async def test_groq_exception_propagates(monkeypatch):
    async def create(**kwargs):
        raise RuntimeError("model retired")
    monkeypatch.setattr(svc.groq_client.chat.completions, "create", create)
    with pytest.raises(RuntimeError):
        await svc.summarize_transcript("x", "en")
