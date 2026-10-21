# app/deps.py
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from app.config import settings

bearer = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def user_from_token(token: str) -> dict | None:
    """Decode a login token. Returns {"id": ...} or None if invalid/expired.
    Shared by the HTTP dependency below and the WebSocket route, which can't
    use Authorization headers from a browser and takes the token as a query param."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None
    sub = payload.get("sub")
    return {"id": sub} if sub else None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise _unauthorized("Missing bearer token")
    user = user_from_token(creds.credentials)
    if user is None:
        raise _unauthorized("Invalid or expired token")
    return user
