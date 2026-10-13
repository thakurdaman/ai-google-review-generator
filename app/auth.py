import os
import hmac
import hashlib
import time
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from .database import get_db, hash_password

SECRET_KEY = os.getenv("SECRET_KEY", "google-review-saas-secret-key-2026-secure-jwt")

# --- Reseller Admin Auth ---

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


# --- Business Owner Auth ---

def create_owner_token(owner_id: int, email: str, business_id: int) -> str:
    timestamp = str(int(time.time()))
    payload = f"{owner_id}:{email}:{business_id}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"

def verify_owner_token(token: str, max_age: int = 86400 * 14) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 5:
            return None
        owner_id_str, email, business_id_str, timestamp_str, signature = parts
        payload = f"{owner_id_str}:{email}:{business_id_str}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        timestamp = int(timestamp_str)
        if time.time() - timestamp > max_age:
            return None
        return {
            "owner_id": int(owner_id_str),
            "email": email,
            "business_id": int(business_id_str)
        }
    except Exception:
        return None

def authenticate_owner(email: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bo.*, b.name as business_name, b.slug as business_slug, b.subscription_status
        FROM business_owners bo
        LEFT JOIN businesses b ON bo.business_id = b.id
        WHERE bo.email = ?
    """, (email.strip().lower(),))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    if not hmac.compare_digest(row["password_hash"], hash_password(password)):
        return None
    return dict(row)

def get_current_owner(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("owner_session")
    if not token:
        return None
    data = verify_owner_token(token)
    if not data:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bo.*, b.name as business_name, b.slug as business_slug, b.google_review_url,
               b.subscription_status, b.plan_tier, b.payment_method, b.subscription_cycle_end,
               b.logo_url, b.accent_color, b.category, b.tags_preset, b.active
        FROM business_owners bo
        JOIN businesses b ON bo.business_id = b.id
        WHERE bo.id = ?
    """, (data["owner_id"],))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def require_owner(request: Request) -> Dict[str, Any]:
    owner = get_current_owner(request)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return owner


# --- Google OAuth Utilities ---

def get_owner_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Look up an existing business owner by email address."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bo.*, b.name as business_name, b.slug as business_slug, b.google_review_url,
               b.subscription_status, b.plan_tier, b.payment_method, b.subscription_cycle_end,
               b.logo_url, b.accent_color, b.category, b.tags_preset, b.active
        FROM business_owners bo
        LEFT JOIN businesses b ON bo.business_id = b.id
        WHERE bo.email = ?
    """, (email.strip().lower(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_oauth_state_token(action: str = "login") -> str:
    """Generate CSRF state token for Google OAuth flow."""
    timestamp = str(int(time.time()))
    payload = f"{action}:{timestamp}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_oauth_state_token(token: str, max_age: int = 1800) -> Optional[str]:
    """Verify CSRF state token from OAuth callback and return action ('login' or 'signup')."""
    if not token:
        return None
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        action, timestamp_str, signature = parts
        payload = f"{action}:{timestamp_str}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        if time.time() - int(timestamp_str) > max_age:
            return None
        return action
    except Exception:
        return None


def create_google_signup_token(email: str, name: str, picture: str = "") -> str:
    """Create a secure, tamper-proof token containing verified Google user profile for completing signup."""
    import base64
    timestamp = str(int(time.time()))
    data_str = f"{email}|{name}|{picture}|{timestamp}"
    encoded_data = base64.urlsafe_b64encode(data_str.encode()).decode()
    signature = hmac.new(SECRET_KEY.encode(), encoded_data.encode(), hashlib.sha256).hexdigest()
    return f"{encoded_data}.{signature}"


def verify_google_signup_token(token: str, max_age: int = 3600) -> Optional[Dict[str, str]]:
    """Verify and decode Google signup token."""
    import base64
    if not token or "." not in token:
        return None
    try:
        encoded_data, signature = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode(), encoded_data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        decoded = base64.urlsafe_b64decode(encoded_data.encode()).decode()
        parts = decoded.split("|")
        if len(parts) != 4:
            return None
        email, name, picture, timestamp_str = parts
        if time.time() - int(timestamp_str) > max_age:
            return None
        return {
            "email": email,
            "name": name,
            "picture": picture
        }
    except Exception:
        return None
