# app/services/romanize.py
from app.config import settings
from app.services.llm import groq_client

ROMANIZE_PROMPT = """Convert this mixed Hindi-English transcript into casual
romanized Hinglish, the way people naturally type it in WhatsApp/texting
(e.g. "kal milte hain", not formal transliteration standards). Keep English
words exactly as they are, including their capitalization (Friday, Rahul).
Output ONLY the romanized text, nothing else."""

async def romanize_if_needed(text: str, lang: str) -> str:
    if lang != "hinglish":
        return text
    response = await groq_client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=[
            {"role": "system", "content": ROMANIZE_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content.strip()
