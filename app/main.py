# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import dictate, summarize

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(dictate.router)
app.include_router(summarize.router)

@app.get("/health")
def health():
    return {"status": "ok"}