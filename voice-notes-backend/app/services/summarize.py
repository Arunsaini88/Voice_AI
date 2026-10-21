# app/services/summarize.py
import json, logging
from app.config import settings
from app.services.llm import groq_client

log = logging.getLogger("app.summarize")

SUMMARY_LANGUAGE_INSTRUCTIONS = {
    "en": "Summarize this transcript in English.",
    "hi": "Summarize this transcript in Hindi (Devanagari script).",
    "hinglish": (
        "Summarize this transcript in casual romanized Hinglish — the way "
        "people naturally text (e.g. 'kal milte hain'), not formal Hindi and "
        "not pure English. Keep English words as English."
    ),
}


def _coerce(raw: dict, transcript: str) -> dict:
    """Force the model's JSON into the shape we store, so a creative reply
    can't put a dict in `title` or a string in `key_points`."""
    title = raw.get("title")
    summary = raw.get("summary")
    points = raw.get("key_points")
    if not isinstance(title, str) or not title.strip():
        title = transcript[:60].rstrip() + ("…" if len(transcript) > 60 else "")
    if not isinstance(summary, str):
        summary = None if summary is None else str(summary)
    if isinstance(points, str):
        points = [points]
    if not isinstance(points, list):
        points = []
    points = [p.strip() for p in points if isinstance(p, str) and p.strip()][:20]
    return {"title": title.strip()[:200], "summary": summary, "key_points": points}


async def summarize_transcript(transcript: str, language: str = "en") -> dict:
    """Returns {"title", "summary", "key_points"} for a spoken transcript.
    Raises on a Groq failure; callers decide whether that's fatal."""
    system_prompt = f"""You summarize a spoken transcript into a concise summary.
Do not invent content that wasn't said.
{SUMMARY_LANGUAGE_INSTRUCTIONS.get(language, SUMMARY_LANGUAGE_INSTRUCTIONS["en"])}

Output as JSON: {{"title": "...", "summary": "...", "key_points": ["...", "..."]}}"""

    response = await groq_client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        log.warning("summary was not valid JSON; storing transcript without summary. head=%r", (content or "")[:120])
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return _coerce(raw, transcript)
