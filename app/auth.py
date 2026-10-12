import os
import hmac
import hashlib
import time
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from .database import get_db, hash_password

SECRET_KEY = os.getenv("SECRET_KEY", "google-review-saas-secret-key-2026-secure-jwt")

def create_session_token(username: str) -> str:
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def verify_session_token(token: str, max_age: int = 86400 * 7) -> Optional[str]:
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        username, timestamp_str, signature = parts
        expected_sig = hmac.new(SECRET_KEY.encode(), f"{username}:{timestamp_str}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        timestamp = int(timestamp_str)
        if time.time() - timestamp > max_age:
            return None
        return username
    except Exception:
        return None

def authenticate_reseller(username: str, password: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM resellers WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return hmac.compare_digest(row["password_hash"], hash_password(password))

def get_current_reseller(request: Request) -> Optional[str]:
    token = request.cookies.get("reseller_session")
    if not token:
        return None
    return verify_session_token(token)

def require_reseller(request: Request):
    user = get_current_reseller(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/admin/login"}
        )
    return user
