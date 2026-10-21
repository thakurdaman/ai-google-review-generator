import base64
import hmac
import hashlib
"""
Google Business Profile API & Review Auto-Reply Management Service.
Handles:
1. Google OAuth 2.0 with Business Profile API (`business.manage` scope).
2. Encrypted token storage (AES-256-GCM) & automatic refresh token rotation.
3. Review Fetching & Sync (via Google My Business API / Business Profile API and webhook).
4. Auto-Reply Decision Engine:
   - Mode per star rating: 'Auto-post', 'Draft for my approval', 'Off'
   - Tone, signature, required phrases, banned phrases
   - Multi-language generation (English, Hindi, Hinglish)
   - Quota check per subscription plan (Monthly: 150, Yearly: 1000, Free: 15)
   - WhatsApp alert to owner on new 1 and 2 star reviews with drafted reply
5. Mock & Sandbox Mode for testing without live Google Cloud credentials.
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from .database import (
    get_setting,
    get_business_google_connection_status,
    get_google_business_profile_tokens,
    save_google_business_profile_tokens,
    disconnect_google_business_profile,
    get_business_autoreply_settings,
    save_business_autoreply_settings,
    get_business_reply_usage,
    increment_business_reply_usage,
    get_business_google_reviews,
    save_google_review,
    update_google_review_reply
)
from .security import (
    SECRET_KEY,
    encrypt_secret,
    decrypt_secret
)
from .ai_service import generate_google_review_reply, generate_multi_reply_options, detect_review_language, sanitize_review_input
from .whatsapp_service import send_low_star_review_alert_to_owner, send_multi_option_review_alert_to_owner


# ── Google OAuth Configurations ───────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_BUSINESS_SCOPE = "https://www.googleapis.com/auth/business.manage"

# Plan limits for auto-replies
PLAN_REPLY_LIMITS = {
    "free": 15,
    "trial": 15,
    "monthly": 150,
    "yearly": 1000
}


def get_plan_reply_limit(plan_tier: str) -> int:
    """Returns monthly allowed review replies for a given plan tier."""
    clean = (plan_tier or "monthly").lower().strip()
    return PLAN_REPLY_LIMITS.get(clean, 150)


def build_google_oauth_url(business_id: int, owner_id: int, redirect_uri: str) -> str:
    """
    Constructs Google OAuth 2.0 authorization URL for Google Business Profile API.
    """
    client_id = get_setting("google_client_id", GOOGLE_CLIENT_ID).strip()
    state_payload = json.dumps({"business_id": business_id, "owner_id": owner_id, "ts": int(time.time())})
    state_b64 = requests.utils.quote(state_payload)

    if not client_id:
        # Development / Sandbox OAuth fallback
        return f"{redirect_uri}?mock_connect=1&business_id={business_id}&state={state_b64}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_BUSINESS_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state_b64
    }
    encoded = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{encoded}"


def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    business_id: int
) -> Tuple[bool, str]:
    """
    Exchanges authorization code for Google OAuth tokens and saves them encrypted.
    """
    client_id = get_setting("google_client_id", GOOGLE_CLIENT_ID).strip()
    client_secret = get_setting("google_client_secret", GOOGLE_CLIENT_SECRET).strip()

    if not client_id or not client_secret or code.startswith("mock_"):
        # Save mock connection for seamless testing & demonstration
        save_google_business_profile_tokens(
            business_id=business_id,
            access_token="mock_access_token_google_reviewboost_dev",
            refresh_token="mock_refresh_token_google_reviewboost_dev",
            expires_in=3600,
            account_id="accounts/109283746501928374",
            location_id="locations/8920192837465019",
            location_name="Verified Google Location"
        )
        return True, "Google Business Profile connected successfully (Sandbox Mode)!"

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=10)
        if resp.status_code != 200:
            return False, f"Failed to retrieve Google tokens: {resp.text}"

        tokens = resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)

        # Retrieve accounts and primary location
        acc_id, loc_id, loc_name = _fetch_google_account_locations(access_token)

        save_google_business_profile_tokens(
            business_id=business_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            account_id=acc_id,
            location_id=loc_id,
            location_name=loc_name
        )
        return True, "Google Business Profile connected successfully!"
    except Exception as e:
        return False, f"Google OAuth exchange error: {str(e)}"


def _fetch_google_account_locations(access_token: str) -> Tuple[str, str, str]:
    """Queries Google Business Profile API for Account and primary Location IDs."""
    acc_id = "accounts/primary"
    loc_id = "locations/primary"
    loc_name = "Primary Business Location"

    try:
        # 1. Fetch Accounts
        acc_resp = requests.get(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8
        )
        if acc_resp.status_code == 200:
            acc_data = acc_resp.json()
            accounts = acc_data.get("accounts", [])
            if accounts:
                acc_id = accounts[0].get("name", acc_id)

        # 2. Fetch Locations under that account
        loc_resp = requests.get(
            f"https://mybusinessbusinessinformation.googleapis.com/v1/{acc_id}/locations?readMask=name,title,storefrontAddress",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8
        )
        if loc_resp.status_code == 200:
            loc_data = loc_resp.json()
            locations = loc_data.get("locations", [])
            if locations:
                loc_id = locations[0].get("name", loc_id)
                loc_name = locations[0].get("title", loc_name)
    except Exception as e:
        print(f"[Google API] Note on fetching locations: {e}")

    return acc_id, loc_id, loc_name


def refresh_google_access_token(business_id: int) -> Optional[str]:
    """Refreshes expired Google access token using stored encrypted refresh token."""
    token_data = get_google_business_profile_tokens(business_id)
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return None

    client_id = get_setting("google_client_id", GOOGLE_CLIENT_ID).strip()
    client_secret = get_setting("google_client_secret", GOOGLE_CLIENT_SECRET).strip()

    if not client_id or not client_secret:
        return "mock_access_token_refreshed"

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            new_access = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            save_google_business_profile_tokens(
                business_id=business_id,
                access_token=new_access,
                refresh_token=refresh_token,
                expires_in=expires_in,
                account_id=token_data.get("account_id", ""),
                location_id=token_data.get("location_id", ""),
                location_name=token_data.get("location_name", "")
            )
            return new_access
    except Exception as e:
        print(f"[Google Token Refresh] Error: {e}")
    return None


# ── Review Sync & Auto-Reply Engine ───────────────────────────────────────────

def sync_and_process_google_reviews(
    business_id: int,
    business_name: str,
    plan_tier: str = "monthly",
    base_url: str = "http://localhost:8000"
) -> Dict[str, Any]:
    """
    Polls/Syncs Google Business reviews, processes new reviews through the AI reply engine,
    auto-posts or drafts according to owner settings, and alerts owner on 1-2 star reviews.
    """
    status = get_business_google_connection_status(business_id)
    if not status.get("connected"):
        return {"success": False, "message": "Google Business Profile is not connected"}

    tokens = get_google_business_profile_tokens(business_id)
    access_token = tokens.get("access_token") or refresh_google_access_token(business_id)

    # 1. Fetch reviews from Google or generate realistic multi-lingual reviews in test mode
    fetched_reviews = []
    if access_token and not access_token.startswith("mock_"):
        fetched_reviews = _fetch_live_google_reviews(tokens)
    
    if not fetched_reviews:
        # In mock mode or if no new live reviews found, generate high-quality demo set
        # if store is empty
        existing = get_business_google_reviews(business_id)
        if len(existing) == 0:
            fetched_reviews = _generate_initial_sandbox_reviews(business_name)

    # 2. Retrieve owner auto-reply settings
    config = get_business_autoreply_settings(business_id)
    tone = config.get("tone", "friendly")
    signature = config.get("signature", "")
    req_phrases = config.get("required_phrases", "")
    banned_phrases = config.get("banned_phrases", "")

    # Mode per star rating
    mode_map = {
        5: config.get("mode_5_star", "Auto-post"),
        4: config.get("mode_4_star", "Auto-post"),
        3: config.get("mode_3_star", "Draft for my approval"),
        2: config.get("mode_2_star", "Draft for my approval"),
        1: config.get("mode_1_star", "Draft for my approval"),
    }

    # 3. Monthly Reply Quota Check
    limit = get_plan_reply_limit(plan_tier)
    usage = get_business_reply_usage(business_id)
    current_month_replies = usage.get("month_count", 0)

    processed_count = 0
    new_reviews_count = 0
    auto_posted_count = 0
    drafted_count = 0
    alerts_dispatched = 0

    existing_reviews = get_business_google_reviews(business_id, limit=200)
    existing_map = {r["google_review_id"]: r for r in existing_reviews}
    previous_replies = [r.get("posted_reply") or r.get("drafted_reply") for r in existing_reviews if r.get("posted_reply") or r.get("drafted_reply")]

    for rev in fetched_reviews:
        rev_id = rev["google_review_id"]
        rating = int(rev["star_rating"])
        review_text = rev.get("review_text", "")
        reviewer = rev.get("reviewer_name", "Google User")

        # Avoid duplicate replies if review is edited or webhook fires twice
        if rev_id in existing_map and existing_map[rev_id].get("reply_status") in ("posted", "draft"):
            continue

        new_reviews_count += 1
        mode = mode_map.get(rating, "Draft for my approval")

        if mode == "Off":
            rev["reply_status"] = "off"
            rev["drafted_reply"] = ""
            rev["posted_reply"] = ""
            save_google_review(business_id, rev)
            continue

        # Check quota
        if current_month_replies >= limit:
            rev["reply_status"] = "limit_reached"
            rev["drafted_reply"] = "Monthly reply limit reached for your plan. Please upgrade to post more replies."
            save_google_review(business_id, rev)
            continue

        # Generate AI reply
        ai_reply = generate_google_review_reply(
            review_text=review_text,
            rating=rating,
            reviewer_name=reviewer,
            tone=tone,
            business_name=business_name,
            signature=signature,
            required_phrases=req_phrases,
            banned_phrases=banned_phrases,
            previous_replies=previous_replies
        )
        previous_replies.append(ai_reply)

        if mode == "Auto-post":
            # Post directly to Google
            post_ok = _post_reply_to_google_api(tokens, rev_id, ai_reply)
            rev["reply_status"] = "posted"
            rev["posted_reply"] = ai_reply
            rev["drafted_reply"] = ""
            rev["reply_posted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            increment_business_reply_usage(business_id)
            current_month_replies += 1
            auto_posted_count += 1
        else:
            # Generate 3 distinct AI reply options (Warm, Professional, Casual)
            multi_options = generate_multi_reply_options(
                review_text=review_text,
                rating=rating,
                reviewer_name=reviewer,
                business_name=business_name,
                signature=signature,
                required_phrases=req_phrases,
                banned_phrases=banned_phrases,
                previous_replies=previous_replies
            )
            opt_texts = [o["text"] for o in multi_options]
            rev["reply_status"] = "draft"
            rev["drafted_reply"] = opt_texts[0] if opt_texts else ai_reply
            rev["draft_options"] = opt_texts
            rev["posted_reply"] = ""
            drafted_count += 1

            # Build 1-tap signed URLs for each option
            dash_url = f"{base_url}/dashboard?tab=reviews"
            opt_links = []
            for i, opt in enumerate(multi_options):
                tok = create_quick_reply_token(business_id, rev_id, i)
                opt_links.append({
                    "label": opt["label"],
                    "text": opt["text"],
                    "url": f"{base_url}/quick-reply?token={tok}"
                })

            # Send multi-option alert to owner on WhatsApp / Telegram
            wa_ok, _ = send_multi_option_review_alert_to_owner(
                business_id=business_id,
                business_name=business_name,
                rating=rating,
                reviewer_name=reviewer,
                review_text=review_text,
                options=opt_links,
                dashboard_url=dash_url
            )
            if wa_ok:
                alerts_dispatched += 1
                rev["whatsapp_alert_sent"] = True

        save_google_review(business_id, rev)
        processed_count += 1

    return {
        "success": True,
        "synced_count": len(fetched_reviews),
        "new_reviews": new_reviews_count,
        "auto_posted": auto_posted_count,
        "drafted": drafted_count,
        "alerts_dispatched": alerts_dispatched,
        "current_month_usage": current_month_replies,
        "monthly_limit": limit
    }


def approve_and_post_reply(
    business_id: int,
    google_review_id: str,
    reply_text: str
) -> Tuple[bool, str]:
    """
    Approves a drafted or edited AI reply and posts it to Google Business Profile.
    """
    clean_reply = sanitize_review_input(reply_text, max_len=1000)
    if not clean_reply:
        return False, "Reply text cannot be empty."

    tokens = get_google_business_profile_tokens(business_id)
    post_ok = _post_reply_to_google_api(tokens, google_review_id, clean_reply)

    update_google_review_reply(
        business_id=business_id,
        google_review_id=google_review_id,
        reply_text=clean_reply,
        status="posted"
    )
    increment_business_reply_usage(business_id)
    return True, "Reply posted successfully to Google Business Profile!"


def regenerate_ai_reply(
    business_id: int,
    business_name: str,
    google_review_id: str
) -> Tuple[bool, str, str]:
    """
    Regenerates a fresh AI reply draft with a new perspective and distinct phrasing.
    """
    reviews = get_business_google_reviews(business_id)
    target = next((r for r in reviews if r.get("google_review_id") == google_review_id), None)
    if not target:
        return False, "Review not found", ""

    config = get_business_autoreply_settings(business_id)
    tone = config.get("tone", "friendly")
    signature = config.get("signature", "")
    req_phrases = config.get("required_phrases", "")
    banned_phrases = config.get("banned_phrases", "")

    previous_replies = [target.get("drafted_reply", ""), target.get("posted_reply", "")]

    new_reply = generate_google_review_reply(
        review_text=target.get("review_text", ""),
        rating=int(target.get("star_rating", 5)),
        reviewer_name=target.get("reviewer_name", ""),
        tone=tone,
        business_name=business_name,
        signature=signature,
        required_phrases=req_phrases,
        banned_phrases=banned_phrases,
        previous_replies=previous_replies
    )

    update_google_review_reply(
        business_id=business_id,
        google_review_id=google_review_id,
        reply_text=new_reply,
        status="draft"
    )
    return True, "Reply regenerated successfully!", new_reply


def _post_reply_to_google_api(tokens: dict, google_review_id: str, reply_text: str) -> bool:
    """Posts a reply to Google My Business API reviews endpoint."""
    access_token = tokens.get("access_token", "")
    if not access_token or access_token.startswith("mock_"):
        # Simulated live Google posting
        safe_preview = reply_text[:60].encode("ascii", "replace").decode("ascii")
        print(f"[GOOGLE API MOCK] Posted reply to review {google_review_id}: '{safe_preview}...'")
        return True

    account_id = tokens.get("account_id", "")
    location_id = tokens.get("location_id", "")
    url = f"https://mybusiness.googleapis.com/v4/{location_id}/reviews/{google_review_id}/reply"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"comment": reply_text}
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=8)
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"[Google Review Reply] API error: {e}")
        return False


def _fetch_live_google_reviews(tokens: dict) -> List[Dict[str, Any]]:
    """Fetches real reviews from Google My Business v4 endpoint."""
    access_token = tokens.get("access_token", "")
    location_id = tokens.get("location_id", "")
    url = f"https://mybusiness.googleapis.com/v4/{location_id}/reviews"
    headers = {"Authorization": f"Bearer {access_token}"}
    results = []
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            for r in data.get("reviews", []):
                rating_str = r.get("starRating", "FIVE")
                star_val = {"FIVE": 5, "FOUR": 4, "THREE": 3, "TWO": 2, "ONE": 1}.get(rating_str, 5)
                results.append({
                    "google_review_id": r.get("reviewId", ""),
                    "reviewer_name": r.get("reviewer", {}).get("displayName", "Google User"),
                    "star_rating": star_val,
                    "review_text": r.get("comment", ""),
                    "review_language": detect_review_language(r.get("comment", "")),
                    "created_at": r.get("createTime", "")
                })
    except Exception as e:
        print(f"[Google Fetch Reviews] Error: {e}")
    return results


def _generate_initial_sandbox_reviews(business_name: str) -> List[Dict[str, Any]]:
    """Creates a realistic, multi-lingual initial test set of reviews (English, Hindi, Hinglish)."""
    return [
        {
            "google_review_id": "google_rev_001_en",
            "reviewer_name": "Aarav Patel",
            "star_rating": 5,
            "review_text": f"Outstanding experience at {business_name}! The cold brew was perfection and the staff made us feel right at home.",
            "review_language": "en",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        {
            "google_review_id": "google_rev_002_hinglish",
            "reviewer_name": "Simran Kaur",
            "star_rating": 4,
            "review_text": f"Bohot accha ambience aur staff ka behavior ekdum badhiya tha. Highly recommended!",
            "review_language": "hinglish",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        {
            "google_review_id": "google_rev_003_hi",
            "reviewer_name": "विक्रम शर्मा",
            "star_rating": 5,
            "review_text": "बहुत ही बेहतरीन सेवा और शांतिपूर्ण वातावरण। हमारी पूरी फैमिली को यहाँ आकर बहुत अच्छा लगा।",
            "review_language": "hi",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        {
            "google_review_id": "google_rev_004_three_star",
            "reviewer_name": "Kavita Verma",
            "star_rating": 3,
            "review_text": f"The food was good at {business_name}, but service took longer than expected during the lunch rush.",
            "review_language": "en",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        {
            "google_review_id": "google_rev_005_low_star",
            "reviewer_name": "Aditya Roy",
            "star_rating": 2,
            "review_text": "Waited 25 minutes for my takeaway order and the counter staff seemed distracted.",
            "review_language": "en",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S UTC")
        },
        {
            "google_review_id": "google_rev_006_no_text",
            "reviewer_name": "Pooja Mehta",
            "star_rating": 5,
            "review_text": "",
            "review_language": "en",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S UTC")
        }
    ]


def create_quick_reply_token(business_id: int, google_review_id: str, option_idx: int) -> str:
    """Creates a tamper-proof signed token for 1-tap WhatsApp reply approval (valid for 7 days)."""
    data = {
        "b": business_id,
        "r": google_review_id,
        "o": option_idx,
        "t": int(time.time())
    }
    raw = base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).decode("ascii")
    sig = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_quick_reply_token(token: str, max_age: int = 604800) -> Optional[Tuple[int, str, int]]:
    """Verifies 1-tap WhatsApp token. Returns (business_id, google_review_id, option_idx) or None."""
    if not token or "." not in token:
        return None
    try:
        raw, sig = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        if time.time() - data.get("t", 0) > max_age:
            return None
        return int(data["b"]), str(data["r"]), int(data["o"])
    except Exception:
        return None
