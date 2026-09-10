# app/routes/summarize.py
from fastapi import APIRouter
from groq import AsyncGroq
from app.config import settings
import json

router = APIRouter()
groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

SUMMARY_LANGUAGE_INSTRUCTIONS = {
    "en": "Summarize this transcript in English.",
    "hi": "Summarize this transcript in Hindi (Devanagari script).",
    "hinglish": (
        "Summarize this transcript in casual romanized Hinglish — the way "
        "people naturally text (e.g. 'kal milte hain'), not formal Hindi and "
        "not pure English. Keep English words as English."
    ),
}

@router.post("/llm/summarize")
async def summarize(payload: dict):
    lang = payload.get("language", "en")
    system_prompt = f"""You summarize a spoken transcript into a concise summary.
Do not invent content that wasn't said.
{SUMMARY_LANGUAGE_INSTRUCTIONS.get(lang, SUMMARY_LANGUAGE_INSTRUCTIONS["en"])}

Output as JSON: {{"title": "...", "summary": "...", "key_points": ["...", "..."]}}"""

    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload["transcript"]},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)