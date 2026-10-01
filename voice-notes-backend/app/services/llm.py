# app/services/llm.py
from groq import AsyncGroq
from app.config import settings

# One client shared by every Groq caller (romanize, summarize).
groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
