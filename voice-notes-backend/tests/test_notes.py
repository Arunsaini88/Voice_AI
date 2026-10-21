# tests/test_notes.py
from tests.conftest import FAKE_SUMMARY

NOTE = {"transcript": "  Remind me to call the dentist tomorrow.  ", "language": "en"}


async def test_create_list_get_delete(client, user):
    r = await client.post("/notes", json=NOTE, headers=user.headers)
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["raw_transcript"] == NOTE["transcript"].strip()
    assert note["title"] == FAKE_SUMMARY["title"]
    assert note["key_points"] == FAKE_SUMMARY["key_points"]
    assert note["language"] == "en"
    assert "raw_transcript" in note and "created_at" in note

    listed = await client.get("/notes", headers=user.headers)
    assert listed.status_code == 200
    assert [n["id"] for n in listed.json()] == [note["id"]]

    single = await client.get(f"/notes/{note['id']}", headers=user.headers)
    assert single.status_code == 200 and single.json()["id"] == note["id"]

    assert (await client.delete(f"/notes/{note['id']}", headers=user.headers)).status_code == 204
    assert (await client.get(f"/notes/{note['id']}", headers=user.headers)).status_code == 404
    assert (await client.get("/notes", headers=user.headers)).json() == []


async def test_list_is_newest_first(client, user):
    ids = []
    for i in range(3):
        r = await client.post("/notes", json={**NOTE, "transcript": f"note {i}"}, headers=user.headers)
        ids.append(r.json()["id"])
    listed = [n["id"] for n in (await client.get("/notes", headers=user.headers)).json()]
    assert listed == list(reversed(ids))


async def test_notes_are_isolated_between_users(client, user, other_user):
    mine = (await client.post("/notes", json=NOTE, headers=user.headers)).json()

    assert (await client.get("/notes", headers=other_user.headers)).json() == []
    # Someone else's note reads as "not found", not "forbidden", so ids don't leak.
    assert (await client.get(f"/notes/{mine['id']}", headers=other_user.headers)).status_code == 404
    assert (await client.delete(f"/notes/{mine['id']}", headers=other_user.headers)).status_code == 404
    # ...and it is still there for the owner.
    assert (await client.get(f"/notes/{mine['id']}", headers=user.headers)).status_code == 200


async def test_validation(client, user):
    cases = [
        ({"transcript": "   ", "language": "en"}, "blank"),
        ({"transcript": "x" * 20_001, "language": "en"}, "too long"),
        ({"transcript": "hello", "language": "klingon"}, "bad language"),
        ({"language": "en"}, "missing transcript"),
    ]
    for payload, why in cases:
        r = await client.post("/notes", json=payload, headers=user.headers)
        assert r.status_code == 422, why


async def test_note_saved_without_summary_when_llm_fails(client, user, monkeypatch):
    async def boom(transcript, language="en"):
        raise RuntimeError("groq is down")
    monkeypatch.setattr("app.routes.notes.summarize_transcript", boom)

    r = await client.post("/notes", json=NOTE, headers=user.headers)
    assert r.status_code == 201
    body = r.json()
    assert body["raw_transcript"] == NOTE["transcript"].strip()
    assert body["title"] is None and body["summary"] is None and body["key_points"] is None


async def test_stateless_summarize(client, user, monkeypatch):
    ok = await client.post("/llm/summarize", json=NOTE, headers=user.headers)
    assert ok.status_code == 200 and ok.json() == FAKE_SUMMARY
    assert (await client.post("/llm/summarize", json=NOTE)).status_code == 401

    async def boom(transcript, language="en"):
        raise RuntimeError("groq is down")
    monkeypatch.setattr("app.routes.summarizer.summarize_transcript", boom)
    r = await client.post("/llm/summarize", json=NOTE, headers=user.headers)
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()
