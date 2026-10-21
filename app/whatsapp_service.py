"""
WhatsApp Alert and Notification Service for ReviewBoost.
Supports:
1. Meta WhatsApp Business Cloud API (Official Graph API)
2. Twilio WhatsApp API fallback
3. Safe Mock / Local Mode (for testing without live WhatsApp API credentials)

CRITICAL PRIVACY RULE:
Customer feedback text submitted for 1-2 star alerts is transmitted directly
to the business owner's verified WhatsApp and immediately released from memory.
NO feedback message content or customer contact information is stored in any database.
Only transmission metadata (status, timestamp, error code) is recorded in audit logs.
"""

import os
import re
import time
import json
import hmac
import hashlib
import secrets
import requests
from datetime import datetime, timezone, timedelta
from typing import Tuple, Dict, Any, Optional, List

from .database import (
    get_setting,
    get_business_whatsapp_settings,
    save_business_whatsapp_settings,
    log_whatsapp_alert_status
)

# ── WhatsApp API Configuration ────────────────────────────────────────────────
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip()

# Approved Template Names for WhatsApp Business Cloud API
FEEDBACK_ALERT_TEMPLATE = os.getenv("WHATSAPP_FEEDBACK_TEMPLATE", "customer_feedback_alert")
LOW_REVIEW_ALERT_TEMPLATE = os.getenv("WHATSAPP_LOW_REVIEW_TEMPLATE", "low_star_review_alert")
OTP_VERIFY_TEMPLATE = os.getenv("WHATSAPP_OTP_TEMPLATE", "owner_phone_verification_otp")

# Twilio Configuration Fallback
TWILIO_ACCOUNT_SID = (os.getenv("TWILIO_ACCOUNT_SID") or get_setting("twilio_account_sid", "")).strip()
TWILIO_AUTH_TOKEN = (os.getenv("TWILIO_AUTH_TOKEN") or get_setting("twilio_auth_token", "")).strip()
TWILIO_WHATSAPP_NUMBER = (os.getenv("TWILIO_WHATSAPP_NUMBER") or get_setting("twilio_whatsapp_number", "+14155238886")).strip()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or get_setting("telegram_bot_token", "")).strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or get_setting("telegram_chat_id", "")).strip()


def send_telegram_alert(text: str) -> Tuple[bool, str]:
    """Dispatches instant notification to business owner via Telegram Bot."""
    token = TELEGRAM_BOT_TOKEN or get_setting("telegram_bot_token", "")
    chat_id = TELEGRAM_CHAT_ID or get_setting("telegram_chat_id", "")
    if not token or not chat_id:
        return False, "telegram_not_configured"
    try:
        url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id.strip(), "text": text}, timeout=8)
        if resp.status_code in (200, 201):
            return True, "delivered_telegram"
        return False, f"Telegram HTTP {resp.status_code}"
    except Exception as e:
        return False, f"Telegram error: {str(e)}"



def normalize_phone_number(phone: str) -> str:
    """
    Normalizes phone numbers to standard E.164 format (+91XXXXXXXXXX).
    Assumes Indian (+91) country code by default if 10 digits provided.
    """
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone.strip())
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return f"+{digits}"


def mask_phone_number(phone: str) -> str:
    """Masks phone number for privacy-compliant logging (e.g. +91 98*** **210)."""
    norm = normalize_phone_number(phone)
    if len(norm) >= 8:
        return f"{norm[:5]}****{norm[-3:]}"
    return "****"


def _send_meta_cloud_api(recipient_phone: str, text: str, template_name: Optional[str] = None, template_params: Optional[list] = None) -> Tuple[bool, str]:
    """
    Dispatches WhatsApp message via Meta WhatsApp Business Cloud API.
    Uses approved template if available, falls back to direct text payload.
    """
    clean_to = normalize_phone_number(recipient_phone).replace("+", "")
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    if template_name:
        components = []
        if template_params:
            parameters = [{"type": "text", "text": str(p)} for p in template_params]
            components.append({"type": "body", "parameters": parameters})
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en"},
                "components": components
            }
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "text",
            "text": {"preview_url": False, "body": text}
        }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
        if resp.status_code in (200, 201):
            return True, "delivered"
        return False, f"Meta API HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return False, f"Meta API Network Error: {str(e)}"


def _send_twilio_api(recipient_phone: str, text: str) -> Tuple[bool, str]:
    """Dispatches WhatsApp message via Twilio WhatsApp API."""
    to_formatted = f"whatsapp:{normalize_phone_number(recipient_phone)}"
    from_formatted = f"whatsapp:{normalize_phone_number(TWILIO_WHATSAPP_NUMBER)}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "To": to_formatted,
        "From": from_formatted,
        "Body": text
    }
    try:
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=8)
        if resp.status_code in (200, 201):
            return True, "delivered"
        return False, f"Twilio API HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return False, f"Twilio API Network Error: {str(e)}"


def send_whatsapp_message_with_retry(
    recipient_phone: str,
    text: str,
    template_name: Optional[str] = None,
    template_params: Optional[list] = None,
    max_retries: int = 2
) -> Tuple[bool, str]:
    """
    Sends WhatsApp message with automatic exponential backoff retry on transient failure.
    Falls back to mock mode if credentials are not configured.
    """
    norm_phone = normalize_phone_number(recipient_phone)

    # 1. Instant Telegram Delivery (Free, no templates or sandboxes required)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        tel_ok, tel_res = send_telegram_alert(text)
        if tel_ok:
            return True, "delivered_telegram"

    if not norm_phone or len(norm_phone) < 10:
        return False, "invalid_phone_number"

    # Use Meta Cloud API if configured
    if WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID:
        for attempt in range(max_retries + 1):
            success, err_msg = _send_meta_cloud_api(norm_phone, text, template_name, template_params)
            if success:
                return True, "delivered_meta"
            if attempt < max_retries:
                time.sleep(1.0 * (2 ** attempt))
        return False, err_msg

    # Use Twilio API if configured
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER:
        for attempt in range(max_retries + 1):
            success, err_msg = _send_twilio_api(norm_phone, text)
            if success:
                return True, "delivered_twilio"
            if attempt < max_retries:
                time.sleep(1.0 * (2 ** attempt))
        return False, err_msg



    # Development / Test Mock Dispatcher
    try:
        print(f"\n[WHATSAPP DISPATCH SIMULATOR]")
        print(f"  To: {mask_phone_number(norm_phone)}")
        safe_body = text.encode("ascii", "replace").decode("ascii")
        print(f"  Message:\n{safe_body}\n")
    except Exception:
        pass
    return True, "delivered_mock_sandbox"


# ── Owner WhatsApp Number Verification via OTP ────────────────────────────────

def generate_and_send_owner_otp(business_id: int, phone_number: str) -> Tuple[bool, str]:
    """
    Generates a secure 6-digit OTP, stores cryptographic hash with 10-minute validity,
    and dispatches OTP via WhatsApp to the owner's phone.
    """
    norm_phone = normalize_phone_number(phone_number)
    if len(norm_phone) < 10:
        return False, "Please provide a valid 10-digit phone number."

    # Generate 6-digit cryptographic numeric OTP
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    # Hash OTP with SHA-256 for secure storage
    otp_hash = hashlib.sha256(otp_code.encode("utf-8")).hexdigest()

    # Retrieve current settings and save pending verification
    current_settings = get_business_whatsapp_settings(business_id)
    current_settings["pending_phone"] = norm_phone
    current_settings["pending_otp_hash"] = otp_hash
    current_settings["otp_expires_at"] = expires_at
    save_business_whatsapp_settings(business_id, current_settings)

    # Message text
    message_text = (
        f"🔐 *ReviewBoost Verification Code*\n\n"
        f"Your verification code is: *{otp_code}*\n\n"
        f"Use this code to verify your WhatsApp alerts number for direct customer feedback. "
        f"This code will expire in 10 minutes. Do not share it with anyone."
    )

    success, msg = send_whatsapp_message_with_retry(
        norm_phone,
        message_text,
        template_name=OTP_VERIFY_TEMPLATE,
        template_params=[otp_code]
    )

    log_whatsapp_alert_status(
        business_id=business_id,
        alert_type="otp_verification",
        recipient_masked=mask_phone_number(norm_phone),
        status="sent" if success else "failed",
        error_code="" if success else msg
    )

    if success:
        return True, f"OTP sent to {mask_phone_number(norm_phone)}"
    return False, f"Failed to send OTP: {msg}"


def verify_owner_otp(business_id: int, phone_number: str, otp_code: str) -> Tuple[bool, str]:
    """
    Verifies owner OTP against stored cryptographic hash.
    On success: marks phone verified and enables WhatsApp alerts.
    """
    norm_phone = normalize_phone_number(phone_number)
    current_settings = get_business_whatsapp_settings(business_id)

    pending_phone = current_settings.get("pending_phone", "")
    if normalize_phone_number(pending_phone) != norm_phone:
        return False, "Phone number mismatch. Please request a new OTP."

    expires_at_str = current_settings.get("otp_expires_at", "")
    if not expires_at_str:
        return False, "No OTP pending. Please request an OTP first."

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if datetime.now(timezone.utc) > expires_at:
            return False, "OTP has expired. Please request a new one."
    except Exception:
        return False, "Invalid OTP session."

    stored_hash = current_settings.get("pending_otp_hash", "")
    input_hash = hashlib.sha256(otp_code.strip().encode("utf-8")).hexdigest()

    if not hmac.compare_digest(stored_hash, input_hash):
        return False, "Incorrect verification code. Please check and try again."

    # Verification succeeded: activate alerts
    current_settings["phone_number"] = norm_phone
    current_settings["phone_verified"] = True
    current_settings["alerts_enabled"] = True
    current_settings["pending_otp_hash"] = ""
    current_settings["otp_expires_at"] = ""
    save_business_whatsapp_settings(business_id, current_settings)

    log_whatsapp_alert_status(
        business_id=business_id,
        alert_type="otp_verified",
        recipient_masked=mask_phone_number(norm_phone),
        status="verified"
    )

    return True, "WhatsApp number verified and alerts enabled successfully!"


def toggle_owner_whatsapp_alerts(business_id: int, enabled: bool) -> Tuple[bool, str]:
    """Toggles WhatsApp alerts ON or OFF for an owner."""
    settings = get_business_whatsapp_settings(business_id)
    if enabled and not settings.get("phone_verified"):
        return False, "Please verify your WhatsApp number with an OTP before enabling alerts."

    settings["alerts_enabled"] = enabled
    save_business_whatsapp_settings(business_id, settings)
    state_str = "enabled" if enabled else "disabled"
    return True, f"WhatsApp alerts successfully {state_str}."


# ── Customer Feedback Alert Dispatcher (1-2 Stars) ───────────────────────────

def dispatch_customer_feedback_alert(
    business_id: int,
    business_name: str,
    rating: int,
    message: str,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Dispatches customer feedback for 1 or 2 stars straight to business owner's WhatsApp.
    
    PRIVACY ENFORCEMENT:
    Feedback text is formatted in runtime memory and sent via WhatsApp API.
    The message text is NEVER stored in the database or persistent storage.
    Only transmission status (sent/failed/disabled) is logged in whatsapp_alert_logs.
    """
    settings = get_business_whatsapp_settings(business_id)
    if not settings.get("alerts_enabled") or not settings.get("phone_number"):
        log_whatsapp_alert_status(
            business_id=business_id,
            alert_type="customer_feedback",
            recipient_masked="N/A",
            status="disabled",
            error_code="owner_alerts_not_enabled"
        )
        return False, "owner_alerts_disabled"

    recipient_phone = settings["phone_number"]
    masked_phone = mask_phone_number(recipient_phone)

    # Current time in IST (UTC + 5:30)
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    time_str = ist_now.strftime("%d %b %Y, %I:%M %p IST")

    safe_name = (customer_name or "").strip()[:80] or "Customer (Private)"
    safe_contact = (customer_phone or "").strip()[:40] or "Not provided"
    safe_message = message.strip()[:600]

    stars_emoji = "⭐" * rating

    # WhatsApp Alert Message Format
    formatted_alert = (
        f"🚨 *Direct Customer Feedback Alert*\n"
        f"🏢 *{business_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ Rating: {stars_emoji} ({rating} / 5 stars)\n"
        f"🕒 Time: {time_str}\n"
        f"👤 Customer: {safe_name}\n"
        f"📞 Contact: {safe_contact}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💬 *Customer Note:*\n"
        f"\"{safe_message}\"\n\n"
        f"ℹ️ *Note:* This customer was also provided your direct Google Reviews link without filtering."
    )

    success, msg = send_whatsapp_message_with_retry(
        recipient_phone=recipient_phone,
        text=formatted_alert,
        template_name=FEEDBACK_ALERT_TEMPLATE,
        template_params=[business_name, f"{rating} stars", safe_name, safe_contact, safe_message]
    )

    log_whatsapp_alert_status(
        business_id=business_id,
        alert_type="customer_feedback",
        recipient_masked=masked_phone,
        status="sent" if success else "failed",
        error_code="" if success else msg
    )

    return success, "Alert sent directly to business owner" if success else msg


# ── Low Star Google Review WhatsApp Alert Dispatcher ──────────────────────────

def send_low_star_review_alert_to_owner(
    business_id: int,
    business_name: str,
    rating: int,
    reviewer_name: str,
    review_text: str,
    drafted_reply: str,
    dashboard_url: str
) -> Tuple[bool, str]:
    """
    Sends WhatsApp alert to owner when a new 1 or 2 star review is detected on Google,
    including the drafted AI reply ready for one-tap approval.
    """
    settings = get_business_whatsapp_settings(business_id)
    if not settings.get("alerts_enabled") or not settings.get("phone_number"):
        return False, "owner_alerts_disabled"

    recipient_phone = settings["phone_number"]
    masked_phone = mask_phone_number(recipient_phone)

    stars_emoji = "⭐" * rating
    snippet = (review_text or "").strip()[:200] or "[Rating only, no text]"

    alert_text = (
        f"⚠️ *New {rating}-Star Google Review Alert*\n"
        f"🏢 *{business_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Rating: {stars_emoji} by *{reviewer_name or 'Google Reviewer'}*\n"
        f"Review: \"{snippet}\"\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *AI Reply Ready for Your Approval:*\n"
        f"\"{drafted_reply}\"\n\n"
        f"👉 *Tap to Approve or Edit Reply:*\n"
        f"{dashboard_url}"
    )

    success, msg = send_whatsapp_message_with_retry(
        recipient_phone=recipient_phone,
        text=alert_text,
        template_name=LOW_REVIEW_ALERT_TEMPLATE,
        template_params=[business_name, str(rating), reviewer_name or "Reviewer", snippet, drafted_reply, dashboard_url]
    )

    log_whatsapp_alert_status(
        business_id=business_id,
        alert_type="low_star_google_review",
        recipient_masked=masked_phone,
        status="sent" if success else "failed",
        error_code="" if success else msg
    )

    return success, msg



def send_multi_option_review_alert_to_owner(
    business_id: int,
    business_name: str,
    rating: int,
    reviewer_name: str,
    review_text: str,
    options: List[Dict[str, str]],
    dashboard_url: str
) -> Tuple[bool, str]:
    """
    Sends WhatsApp and Telegram alert to business owner with 3 AI-drafted reply options,
    each with a direct 1-tap link to publish straight to Google Maps.
    """
    settings = get_business_whatsapp_settings(business_id)
    if not settings.get("alerts_enabled") or not settings.get("phone_number"):
        return False, "owner_alerts_disabled"

    recipient_phone = settings["phone_number"]
    masked_phone = mask_phone_number(recipient_phone)

    stars_emoji = "⭐" * rating
    snippet = (review_text or "").strip()[:300] or "[Rating only, no text]"

    options_text_blocks = []
    emoji_nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    for i, opt in enumerate(options):
        num = emoji_nums[i] if i < len(emoji_nums) else f"[{i+1}]"
        lbl = opt.get("label", f"Option {i+1}")
        txt = opt.get("text", "")
        url = opt.get("url", "")
        block = (
            f"{num} *{lbl}:*\n"
            f"\"{txt}\"\n"
            f"👉 *Tap to Post Option {i+1}:*\n"
            f"{url}"
        )
        options_text_blocks.append(block)

    options_joined = "\n\n".join(options_text_blocks)

    alert_text = (
        f"⭐ *New {rating}-Star Google Review Alert!*\n"
        f"🏢 *{business_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Reviewer: *{reviewer_name or 'Google Reviewer'}*\n"
        f"⭐ Rating: {stars_emoji} ({rating}/5)\n"
        f"💬 Review: \"{snippet}\"\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Choose a Reply to Post Directly to Google:*\n\n"
        f"{options_joined}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✏️ *Or edit in dashboard:*\n"
        f"{dashboard_url}"
    )

    success, msg = send_whatsapp_message_with_retry(
        recipient_phone=recipient_phone,
        text=alert_text,
        template_name=LOW_REVIEW_ALERT_TEMPLATE,
        template_params=[business_name, str(rating), reviewer_name or "Reviewer", snippet, options[0]["text"] if options else "", dashboard_url]
    )

    log_whatsapp_alert_status(
        business_id=business_id,
        alert_type="multi_option_review_reply",
        recipient_masked=masked_phone,
        status="sent" if success else "failed",
        error_code="" if success else msg
    )

    return success, msg
