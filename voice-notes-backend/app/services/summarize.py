# app/services/summarize.py
import json
from app.config import settings
from app.services.llm import groq_client

SUMMARY_LANGUAGE_INSTRUCTIONS = {
    "en": "Summarize this transcript in English.",
    "hi": "Summarize this transcript in Hindi (Devanagari script).",
    "hinglish": (
        "Summarize this transcript in casual romanized Hinglish — the way "
        "people naturally text (e.g. 'kal milte hain'), not formal Hindi and "
        "not pure English. Keep English words as English."
    ),
}

async def summarize_transcript(transcript: str, language: str = "en") -> dict:
    """Returns {"title", "summary", "key_points"} for a spoken transcript."""
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
    return json.loads(response.choices[0].message.content)
