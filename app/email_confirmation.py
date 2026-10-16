"""
Email Confirmation & Account Activation Module
Handles:
1. Cryptographic confirmation token generation & validation
2. Sending confirmation email (SMTP delivery or simulated preview delivery)
3. Owner email verification state management
"""

import os
import hmac
import hashlib
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any
from .database import get_db, get_setting, set_setting, log_activity
from .security import SECRET_KEY



def create_email_confirmation_token(owner_id: int, email: str, business_id: int) -> str:
    """
    Creates a secure, tamper-proof, timestamped HMAC confirmation token.
    """
    ts = str(int(time.time()))
    email_clean = email.strip().lower()
    payload = f"verify:{owner_id}:{email_clean}:{business_id}:{ts}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_email_confirmation_token(token: str, max_age: int = 86400 * 2) -> Optional[Dict[str, Any]]:
    """
    Verifies a confirmation token and ensures it has not expired (default: 48 hours).
    Returns dict with owner_id, email, business_id or None if invalid.
    """
    if not token or not isinstance(token, str):
        return None
    try:
        parts = token.split(":")
        if len(parts) != 6 or parts[0] != "verify":
            return None
        _, owner_id_str, email, business_id_str, ts_str, signature = parts
        payload = f"verify:{owner_id_str}:{email}:{business_id_str}:{ts_str}"
        expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        ts = int(ts_str)
        if time.time() - ts > max_age:
            return None
        return {
            "owner_id": int(owner_id_str),
            "email": email,
            "business_id": int(business_id_str)
        }
    except Exception as e:
        print(f"[EmailConfirmation] Token verification error: {e}")
        return None


def is_owner_email_verified(owner_id: int) -> bool:
    """
    Checks if business owner has clicked their confirmation link and verified email.
    Demo accounts (ID 1) default to verified.
    """
    if not owner_id:
        return False
    # Pre-seeded demo account is verified by default
    if owner_id == 1:
        return True
    val = get_setting(f"email_verified:{owner_id}", "")
    return val.lower() in ("true", "1", "yes")


def mark_owner_email_verified(owner_id: int, business_id: Optional[int] = None):
    """
    Marks the owner's email as verified and activates their business.
    """
    set_setting(f"email_verified:{owner_id}", "true")

    if business_id:
        conn = get_db()
        cursor = conn.cursor()
        # Fetch current business to check subscription status
        cursor.execute("SELECT id, subscription_status FROM businesses WHERE id = ?", (business_id,))
        biz = cursor.fetchone()
        if biz:
            # If status was pending_verification, transition to active
            new_status = "active" if biz.get("subscription_status") in ("pending_verification", None) else biz.get("subscription_status")
            cursor.execute("""
                UPDATE businesses 
                SET active = 1, subscription_status = ? 
                WHERE id = ?
            """, (new_status, business_id))
            conn.commit()
        conn.close()
        log_activity(business_id, "email_verified", "Owner confirmed email address and activated store account")


def get_last_verify_link(owner_id: int) -> str:
    """Retrieves last generated verification link for development/demo helper displays."""
    return get_setting(f"last_verify_link:{owner_id}", "")


def send_confirmation_email(
    email: str,
    business_name: str,
    token: str,
    base_url: str,
    owner_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Sends confirmation email containing the one-click activation link.
    If SMTP credentials are provided, delivers via SMTP.
    Otherwise, simulates delivery and records link for easy verification.
    """
    clean_base = base_url.rstrip("/")
    confirmation_url = f"{clean_base}/verify-email?token={token}"

    if owner_id:
        set_setting(f"last_verify_link:{owner_id}", confirmation_url)

    smtp_host = os.getenv("SMTP_HOST") or get_setting("smtp_host", "")
    smtp_port = int(os.getenv("SMTP_PORT") or get_setting("smtp_port", "587"))
    smtp_user = os.getenv("SMTP_USER") or get_setting("smtp_user", "")
    smtp_password = os.getenv("SMTP_PASSWORD") or get_setting("smtp_password", "")
    smtp_from = os.getenv("SMTP_FROM") or get_setting("smtp_from_email", "no-reply@reviewboost.local")

    # If SMTP is configured, send live email
    if smtp_host and smtp_user and smtp_password:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Action Required: Confirm your ReviewBoost account for {business_name}"
            msg["From"] = f"ReviewBoost <{smtp_from}>"
            msg["To"] = email

            text_content = f"""Hello from ReviewBoost!

Thank you for registering '{business_name}'.

To complete your registration and activate your store review portal, please confirm your email address by clicking the link below:

{confirmation_url}

This link is valid for 48 hours. If you did not create this account, you can safely ignore this email.
"""

            html_content = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 40px 20px;">
  <div style="max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; padding: 36px 32px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="text-align: center; margin-bottom: 24px;">
      <div style="display: inline-block; width: 44px; height: 44px; line-height: 44px; border-radius: 12px; background: #0f172a; color: #fbbf24; font-size: 20px; font-weight: bold;">★</div>
      <h2 style="color: #0f172a; font-size: 20px; font-weight: 800; margin: 12px 0 4px 0;">ReviewBoost</h2>
      <p style="color: #64748b; font-size: 13px; margin: 0;">Automated Google Review Collection</p>
    </div>

    <div style="border-top: 1px solid #f1f5f9; padding-top: 20px; margin-bottom: 24px;">
      <h3 style="color: #1e293b; font-size: 17px; font-weight: 700; margin-top: 0;">Confirm your email address</h3>
      <p style="color: #475569; font-size: 14px; line-height: 1.6; margin: 0 0 16px 0;">
        Welcome to ReviewBoost! To protect your store <strong>{business_name}</strong> and confirm that your email <strong>{email}</strong> can receive important notifications, please click below to activate your account:
      </p>

      <div style="text-align: center; margin: 28px 0;">
        <a href="{confirmation_url}" style="display: inline-block; background-color: #2563eb; color: #ffffff; font-weight: 700; font-size: 14px; text-decoration: none; padding: 14px 28px; border-radius: 10px; box-shadow: 0 2px 4px rgba(37, 99, 235, 0.2);">
          Confirm & Activate My Account &rarr;
        </a>
      </div>

      <p style="color: #64748b; font-size: 12px; line-height: 1.5; margin: 0;">
        Or copy and paste this URL into your browser:<br>
        <a href="{confirmation_url}" style="color: #2563eb; word-break: break-all;">{confirmation_url}</a>
      </p>
    </div>

    <div style="border-top: 1px solid #f1f5f9; padding-top: 16px; text-align: center; color: #94a3b8; font-size: 11px;">
      This verification link expires in 48 hours.<br>
      ReviewBoost Platform • Automated Google Review Management
    </div>
  </div>
</body>
</html>"""

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=8)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=8)
                server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [email], msg.as_string())
            server.quit()
            print(f"[EmailConfirmation] Live SMTP confirmation email sent to {email}")
            return {"sent": True, "mode": "smtp", "confirmation_url": confirmation_url}
        except Exception as e:
            print(f"[EmailConfirmation] Failed to send via SMTP: {e}")

    # Simulated/development delivery fallback (prints to log and saves link for preview)
    print(f"[EmailConfirmation] SIMULATED DELIVERY for {email}: {confirmation_url}")
    return {"sent": True, "mode": "simulated", "confirmation_url": confirmation_url}
