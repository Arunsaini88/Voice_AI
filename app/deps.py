# app/deps.py
async def get_current_user(token: str) -> dict:
    if token != "dev-token-123":
        raise ValueError("unauthorized")
    return {"id": "dev-user-1"}