# app/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from app.config import settings

bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise _unauthorized("Missing bearer token")
    token = creds.credentials

    # Local-dev shortcut. Only active when DEV_TOKEN is set in .env — leave it
    # unset in production and this branch is dead.
    if settings.DEV_TOKEN and token == settings.DEV_TOKEN:
        return {"id": "dev-user-1"}

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise _unauthorized("Invalid or expired token")
    sub = payload.get("sub")
    if not sub:
        raise _unauthorized("Token has no subject")
    return {"id": sub}
