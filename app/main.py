import os
import json
import re
import uuid
import requests
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Response, Form, Query, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .database import (
    init_db, get_db, get_setting, set_setting, hash_password,
    log_activity, get_business_activity_logs, get_business_payments,
    get_all_platform_payments, get_business_metrics, update_owner_password,
    get_business_whatsapp_settings, save_business_whatsapp_settings,
    get_whatsapp_alert_logs, get_google_business_profile_tokens,
    save_google_business_profile_tokens, disconnect_google_business_profile,
    get_business_google_connection_status, get_business_autoreply_settings,
    save_business_autoreply_settings, get_business_reply_usage,
    get_business_google_reviews, save_google_review, update_google_review_reply
)
from .auth import (
    authenticate_reseller, create_session_token, get_current_reseller, require_reseller,
    authenticate_owner, create_owner_token, get_current_owner, require_owner,
    get_owner_by_email, create_oauth_state_token, verify_oauth_state_token,
    create_google_signup_token, verify_google_signup_token,
    create_password_reset_token, verify_password_reset_token
)
from .ai_service import (
    generate_review_drafts, test_openai_connection, generate_google_review_reply, detect_review_language
)
from .qr_service import (
    generate_qr_png_bytes, generate_qr_svg_string
)
from .billing_service import (
    PLANS, create_checkout_session, verify_and_activate_subscription,
    is_subscription_active, cancel_autopay_mandate, is_payments_enabled
)
from .whatsapp_service import (
    dispatch_customer_feedback_alert, generate_and_send_owner_otp,
    verify_owner_otp, toggle_owner_whatsapp_alerts
)
from .google_reviews_service import (
    build_google_oauth_url, exchange_code_for_tokens,
    sync_and_process_google_reviews, approve_and_post_reply,
    regenerate_ai_reply, get_plan_reply_limit
)
from .google_places import (
    extract_google_place_id,
    construct_direct_review_url,
    process_google_profile_link,
    PLACE_ID_ERROR_MESSAGE,
    VALID_EXAMPLE_URL
)
from .email_validator import (
    validate_email_address,
    verify_email_deliverability,
    clean_and_verify_email
)
from .email_confirmation import (
    create_email_confirmation_token,
    verify_email_confirmation_token,
    is_owner_email_verified,
    mark_owner_email_verified,
    send_confirmation_email,
    get_last_verify_link
)
from .security import (
    validate_password_strength,
    validate_uploaded_image_bytes,
    generate_csrf_token,
    verify_csrf_token,
    check_rate_limit,
    get_client_ip,
    is_account_locked,
    record_failed_login,
    reset_failed_logins,
    verify_razorpay_webhook_signature,
    is_webhook_event_processed,
    mark_webhook_event_processed,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SECRET_KEY
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Google Review Collection & Subscription SaaS Platform")

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Templates
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["get_csrf_token"] = generate_csrf_token

# Security Middleware: HTTPS Enforcing, HSTS, Security Headers, and CSRF
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. Transport Security: Force HTTPS in production behind reverse proxies
    proto = request.headers.get("x-forwarded-proto", "")
    host = request.url.hostname or ""
    is_local = host in ("localhost", "127.0.0.1", "testserver")
    if proto == "http" and not is_local:
        https_url = str(request.url).replace("http://", "https://", 1)
        return RedirectResponse(url=https_url, status_code=status.HTTP_301_MOVED_PERMANENTLY)

    # 2. CSRF Verification for state-changing browser requests
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        path = request.url.path
        if path != "/api/payments/webhook":
            csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
            csrf_header = request.headers.get(CSRF_HEADER_NAME)
            if csrf_cookie or csrf_header:
                token_to_verify = csrf_header or csrf_cookie
                if not verify_csrf_token(token_to_verify):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF validation failed. Please refresh the page and try again."}
                    )

    response: Response = await call_next(request)

    # 3. Security Headers (Section 1 & 3)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(self)"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com https://checkout.razorpay.com https://www.googletagmanager.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data: https: blob: https://www.google-analytics.com https://www.googletagmanager.com; "
        "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com https://www.google-analytics.com https://analytics.google.com; "
        "frame-src 'self' https://api.razorpay.com; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    if "x-powered-by" in response.headers:
        del response.headers["x-powered-by"]
    if "server" in response.headers:
        del response.headers["server"]


    # 4. Set / Refresh Double-Submit CSRF Cookie on GET requests
    if request.method == "GET" and not request.cookies.get(CSRF_COOKIE_NAME):
        csrf_tok = generate_csrf_token()
        is_secure = (request.url.scheme == "https" or proto == "https") and not is_local
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_tok,
            httponly=False,
            secure=is_secure,
            samesite="Strict",
            max_age=86400
        )

    return response

# Startup event to initialize DB schema and seed data
@app.on_event("startup")
def on_startup():
    init_db()


# --- Pydantic Request Models ---
class ReviewGenerateRequest(BaseModel):
    business_id: int
    rating: int
    selected_tags: List[str] = []
    custom_note: Optional[str] = None

class FeedbackSubmitRequest(BaseModel):
    business_id: int
    rating: int
    feedback_text: str
    customer_name: Optional[str] = None
    customer_contact: Optional[str] = None

class FeedbackAlertRequest(BaseModel):
    business_id: int
    rating: int
    message: Optional[str] = None
    feedback_text: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_contact: Optional[str] = None
    hp_website: Optional[str] = None  # Spam honeypot

class OwnerWhatsAppSendOtpRequest(BaseModel):
    phone_number: str

class OwnerWhatsAppVerifyOtpRequest(BaseModel):
    phone_number: str
    otp_code: str

class OwnerWhatsAppToggleRequest(BaseModel):
    enabled: bool

class GoogleReviewReplyRequest(BaseModel):
    reply_text: str

class GoogleAutoReplySettingsRequest(BaseModel):
    mode_5_star: Optional[str] = "Auto-post"
    mode_4_star: Optional[str] = "Auto-post"
    mode_3_star: Optional[str] = "Draft for my approval"
    mode_2_star: Optional[str] = "Draft for my approval"
    mode_1_star: Optional[str] = "Draft for my approval"
    tone: Optional[str] = "friendly"
    signature: Optional[str] = ""
    required_phrases: Optional[str] = ""
    banned_phrases: Optional[str] = ""

class AnalyticsEventRequest(BaseModel):
    business_id: int
    event_type: str
    rating: Optional[int] = None

class BusinessCreateRequest(BaseModel):
    name: str
    slug: str
    category: str = "General"
    google_business_profile_url: Optional[str] = None
    google_review_url: Optional[str] = None
    logo_url: Optional[str] = None
    accent_color: str = "#2563EB"
    tags_preset: List[str] = []

    def get_review_url(self) -> str:
        raw = (self.google_business_profile_url or self.google_review_url or "").strip()
        _, direct_url = process_google_profile_link(raw)
        return direct_url

class BusinessUpdateRequest(BaseModel):
    name: str
    slug: str
    category: str
    google_business_profile_url: Optional[str] = None
    google_review_url: Optional[str] = None
    logo_url: Optional[str] = None
    accent_color: str = "#2563EB"
    tags_preset: List[str] = []

    def get_review_url(self) -> str:
        raw = (self.google_business_profile_url or self.google_review_url or "").strip()
        _, direct_url = process_google_profile_link(raw)
        return direct_url

class OwnerProfileUpdateRequest(BaseModel):
    name: str
    google_business_profile_url: Optional[str] = None
    google_review_url: Optional[str] = None
    category: Optional[str] = "General"
    accent_color: Optional[str] = "#2563EB"
    logo_url: Optional[str] = None
    tags_preset: List[str] = []

    def get_review_url(self) -> str:
        raw = (self.google_business_profile_url or self.google_review_url or "").strip()
        _, direct_url = process_google_profile_link(raw)
        return direct_url

class CreateCheckoutRequest(BaseModel):
    plan_type: str
    payment_mode: str

class VerifyPaymentRequest(BaseModel):
    plan_type: str
    payment_mode: str
    payment_id: str
    order_id: Optional[str] = ""
    subscription_id: Optional[str] = ""

class SettingsUpdateRequest(BaseModel):
    openai_api_key: Optional[str] = ""
    razorpay_key_id: Optional[str] = ""
    razorpay_key_secret: Optional[str] = ""
    google_client_id: Optional[str] = ""
    google_client_secret: Optional[str] = ""
    payments_enabled: Optional[bool] = None

class TestAiRequest(BaseModel):
    api_key: str


def get_base_url(request: Request) -> str:
    """Accurately derive base URL behind reverse proxies like Vercel edge."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


# Helper: Generate clean unique slug
def generate_slug(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    if not slug:
        slug = f"biz-{uuid.uuid4().hex[:6]}"
    
    # Check uniqueness
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM businesses WHERE slug = ?", (slug,))
    if cursor.fetchone():
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    conn.close()
    return slug


# ==========================================
# ROOT & PUBLIC NAVIGATION
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # If owner is logged in -> owner dashboard
    owner = get_current_owner(request)
    if owner:
        return RedirectResponse(url="/dashboard")
    # If reseller is logged in -> reseller dashboard
    reseller = get_current_reseller(request)
    if reseller:
        return RedirectResponse(url="/admin")
    # Otherwise render high-converting landing page
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "plans": PLANS,
            "payments_enabled": is_payments_enabled()
        }
    )


# ==========================================
# PUBLIC SELF-SERVE ONBOARDING & SIGNUP
# ==========================================

@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: Optional[str] = None):
    if get_current_owner(request):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="signup.html",
        context={"error": error}
    )

@app.post("/signup")
async def signup_submit(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    category: str = Form("General"),
    google_business_profile_url: Optional[str] = Form(None),
    google_review_url: Optional[str] = Form(None),
    accent_color: Optional[str] = Form("#2563EB"),
    accent_hex: Optional[str] = Form("#2563EB"),
    logo_file: Optional[UploadFile] = File(None),
    tags: Optional[str] = Form("Friendly Staff, Quick Service, Great Quality")
):
    email_clean = email.strip().lower()
    bname_clean = business_name.strip()
    raw_google_url = (google_business_profile_url or google_review_url or "").strip()

    if not bname_clean or not email_clean or not password or not raw_google_url:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": "All required fields must be filled out.",
                "business_name": bname_clean,
                "email": email_clean,
                "category": category,
                "google_business_profile_url": raw_google_url,
                "tags": tags
            },
            status_code=400
        )

    # Validate email deliverability & active status
    is_deliverable, deliverability_err, _ = verify_email_deliverability(email_clean)
    if not is_deliverable:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": deliverability_err or f"The email address '{email_clean}' could not be verified. Please enter a valid, active email address that can receive mail.",
                "business_name": bname_clean,
                "email": email_clean,
                "category": category,
                "google_business_profile_url": raw_google_url,
                "tags": tags
            },
            status_code=400
        )

    try:
        place_id, direct_review_url = process_google_profile_link(raw_google_url)
    except ValueError as ve:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": str(ve),
                "business_name": bname_clean,
                "email": email_clean,
                "category": category,
                "google_business_profile_url": raw_google_url,
                "tags": tags
            },
            status_code=400
        )

    # Rate limiting on signup
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"signup_{client_ip}", max_requests=15, window_seconds=60):
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "Too many signup attempts. Please wait a moment and try again."},
            status_code=429
        )

    # Validate password complexity (>= 10 chars, not easily breached)
    is_strong_pw, pw_err = validate_password_strength(password)
    if not is_strong_pw:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": pw_err,
                "business_name": bname_clean,
                "email": email_clean,
                "category": category,
                "google_business_profile_url": raw_google_url,
                "tags": tags
            },
            status_code=400
        )

    # Handle logo file upload with magic byte verification and raster re-encoding
    logo_url = None
    if logo_file and logo_file.filename:
        contents = await logo_file.read()
        is_valid_img, img_err, safe_bytes, safe_ext = validate_uploaded_image_bytes(contents, logo_file.filename)
        if not is_valid_img:
            return templates.TemplateResponse(
                request=request,
                name="signup.html",
                context={
                    "error": img_err or "Invalid logo image format. Allowed formats: PNG, JPEG, WEBP, SVG.",
                    "business_name": bname_clean,
                    "email": email_clean,
                    "category": category,
                    "google_business_profile_url": raw_google_url,
                    "tags": tags
                },
                status_code=400
            )
        uploads_dir = os.path.join(BASE_DIR, "static", "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        safe_name = f"{uuid.uuid4().hex[:16]}{safe_ext}"
        file_path = os.path.join(uploads_dir, safe_name)
        with open(file_path, "wb") as fp:
            fp.write(safe_bytes)
        logo_url = f"/static/uploads/{safe_name}"

    conn = get_db()
    cursor = conn.cursor()

    # Check if email is already registered
    cursor.execute("SELECT id FROM business_owners WHERE email = ?", (email_clean,))
    if cursor.fetchone():
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={"error": "An account with this email already exists. Please sign in."},
            status_code=400
        )

    # 1. Create Business Record (inactive pending email verification)
    slug = generate_slug(bname_clean)
    brand_color = accent_hex or accent_color or "#2563EB"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else ["Friendly Staff", "Quick Service"]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    payments_on = is_payments_enabled()
    init_active = 0
    init_status = "pending_verification"

    cursor.execute("""
        INSERT INTO businesses (slug, name, category, google_review_url, logo_url, accent_color, tags_preset, active, subscription_status, plan_tier, payment_method, last_google_url_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'monthly', 'upi_autopay', ?)
    """, (
        slug,
        bname_clean,
        category,
        direct_review_url,
        logo_url,
        brand_color,
        json.dumps(tag_list),
        init_active,
        init_status,
        now_str
    ))
    business_id = cursor.lastrowid

    # 2. Create Business Owner Record
    cursor.execute("""
        INSERT INTO business_owners (email, password_hash, full_name, business_id, last_login_at)
        VALUES (?, ?, ?, ?, ?)
    """, (email_clean, hash_password(password), bname_clean, business_id, now_str))
    owner_id = cursor.lastrowid

    # Link owner_id on business
    cursor.execute("UPDATE businesses SET owner_id = ? WHERE id = ?", (owner_id, business_id))
    conn.commit()
    conn.close()

    # 3. Log Activity
    log_activity(business_id, "signup", f"Owner {email_clean} self-registered business '{bname_clean}'. Pending email confirmation.")
    log_activity(business_id, "google_url_updated", f"Configured Google Business Profile direct review link for Place ID {place_id}: {direct_review_url}")

    # 4. Generate confirmation token and dispatch verification email
    base_url = get_base_url(request)
    verify_token = create_email_confirmation_token(owner_id, email_clean, business_id)
    send_confirmation_email(email_clean, bname_clean, verify_token, base_url, owner_id)
    log_activity(business_id, "verification_email_sent", f"Sent account confirmation link to {email_clean}")

    # 5. Redirect to /verify-email-pending
    target_url = f"/verify-email-pending?email={urllib.parse.quote(email_clean)}"
    return RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)


# ==========================================
# REAL-TIME EMAIL VERIFICATION & CONFIRMATION
# ==========================================

@app.get("/api/verify-email")
async def api_verify_email(request: Request, email: str = Query(...)):
    """
    Real-time email deliverability API for signup form typing/blur validation.
    Checks MX records and verification provider APIs with IP rate limiting.
    """
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"verify_email_{client_ip}", max_requests=60, window_seconds=60):
        return JSONResponse(
            status_code=429,
            content={
                "email": email,
                "valid": False,
                "deliverable": False,
                "message": "Rate limit exceeded. Please wait a moment."
            }
        )

    clean = email.strip().lower()
    is_deliverable, error_msg, details = verify_email_deliverability(clean)
    return {
        "email": clean,
        "valid": is_deliverable,
        "deliverable": is_deliverable,
        "message": error_msg or "Active email address verified and ready to receive mail.",
        "details": details
    }



@app.get("/verify-email-pending", response_class=HTMLResponse)
async def verify_email_pending_page(request: Request, email: str = "", message: Optional[str] = None, error: Optional[str] = None):
    dev_link = None
    if email:
        clean = email.strip().lower()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM business_owners WHERE email = ?", (clean,))
        row = cursor.fetchone()
        conn.close()
        if row:
            dev_link = get_last_verify_link(row["id"])
    return templates.TemplateResponse(
        request=request,
        name="verify_email_pending.html",
        context={
            "email": email,
            "message": message,
            "error": error,
            "dev_verify_link": dev_link
        }
    )


@app.post("/api/resend-verification")
async def api_resend_verification(request: Request, email: str = Form(...)):
    clean = email.strip().lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bo.*, b.name as business_name 
        FROM business_owners bo 
        LEFT JOIN businesses b ON bo.business_id = b.id 
        WHERE bo.email = ?
    """, (clean,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return RedirectResponse(
            url=f"/verify-email-pending?email={urllib.parse.quote(clean)}&error=No+account+found+with+this+email.+Please+sign+up.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    owner_id = row["id"]
    business_id = row.get("business_id") or 0
    biz_name = row.get("business_name") or "Your Store"
    token = create_email_confirmation_token(owner_id, clean, business_id)
    send_confirmation_email(clean, biz_name, token, get_base_url(request), owner_id)

    return RedirectResponse(
        url=f"/verify-email-pending?email={urllib.parse.quote(clean)}&message=A+new+activation+link+has+been+dispatched+to+your+inbox.",
        status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email_action(request: Request, token: str = Query(...)):
    """
    Handles confirmation link click from owner's email inbox.
    Activates the account and signs the owner in.
    """
    data = verify_email_confirmation_token(token)
    if not data:
        return templates.TemplateResponse(
            request=request,
            name="verify_email_status.html",
            context={
                "success": False,
                "error": "This activation link is invalid, has already expired after 48 hours, or has been tampered with."
            },
            status_code=400
        )

    owner_id = data["owner_id"]
    email = data["email"]
    business_id = data["business_id"]

    # Mark verified in database
    mark_owner_email_verified(owner_id, business_id)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM businesses WHERE id = ?", (business_id,))
    biz = cursor.fetchone()
    biz_name = biz["name"] if biz else "Your Store"
    conn.close()

    session_token = create_owner_token(owner_id, email, business_id)
    resp = templates.TemplateResponse(
        request=request,
        name="verify_email_status.html",
        context={
            "success": True,
            "email": email,
            "business_name": biz_name
        }
    )
    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(
        key="owner_session",
        value=session_token,
        httponly=True,
        secure=is_secure,
        max_age=86400,
        samesite="Strict"
    )
    return resp



# ==========================================
# BUSINESS OWNER AUTHENTICATION
# ==========================================

@app.get("/login", response_class=HTMLResponse)
async def owner_login_page(request: Request, error: Optional[str] = None):
    if get_current_owner(request):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="owner_login.html",
        context={"error": error}
    )

@app.post("/login")
async def owner_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    email_clean = email.strip()
    is_valid_email, email_err = validate_email_address(email_clean)
    if not is_valid_email:
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={"error": email_err or f"The email address '{email_clean}' is not valid."},
            status_code=400
        )

    # Rate limiting per IP
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"login_{client_ip}", max_requests=30, window_seconds=60):
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={"error": "Too many login attempts. Please wait a moment and try again."},
            status_code=429
        )

    # Account Lockout / Slowdown check per account
    is_locked, remaining_sec = is_account_locked(email_clean)
    if is_locked:
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={"error": f"Account temporarily locked due to repeated failed login attempts. Please try again in {remaining_sec} seconds."},
            status_code=400
        )

    owner = authenticate_owner(email_clean, password)
    if not owner:
        record_failed_login(email_clean)
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={"error": "Invalid email or password"},
            status_code=400
        )

    # Login succeeded - reset lockout tracker
    reset_failed_logins(email_clean)

    # Check if owner's email has been verified
    if not is_owner_email_verified(owner["id"]):
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={
                "error": f"Please verify your email address before logging in. We sent an activation link to {email_clean}."
            },
            status_code=400
        )

    # Update last login & log activity
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE business_owners SET last_login_at = ? WHERE id = ?", (now_str, owner["id"]))
    conn.commit()
    conn.close()

    if owner.get("business_id"):
        log_activity(owner["business_id"], "login", f"Owner logged in ({owner['email']})")

    # Rotate session token upon successful login
    token = create_owner_token(owner["id"], owner["email"], owner["business_id"])
    target_url = "/dashboard"
    if is_payments_enabled() and owner.get("subscription_status") not in ["active", "trialing"]:
        target_url = "/pricing"

    resp = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(
        key="owner_session",
        value=token,
        httponly=True,
        secure=is_secure,
        max_age=86400,
        samesite="Strict"
    )
    return resp

@app.get("/logout")
async def owner_logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("owner_session")
    resp.delete_cookie(CSRF_COOKIE_NAME)
    return resp


# ==========================================
# PASSWORD RESET & RECOVERY FLOW
# ==========================================

@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, error: Optional[str] = None):
    if get_current_owner(request):
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"error": error}
    )

@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(request: Request, email: str = Form(...)):
    clean_email = email.strip().lower()
    is_valid, _ = validate_email_address(clean_email)
    if not is_valid:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"error": "Please enter a valid email address."}
        )

    # Rate limiting per IP
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"pwd_reset_{client_ip}", max_requests=10, window_seconds=60):
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"error": "Too many requests. Please wait a minute and try again."},
            status_code=429
        )

    token = create_password_reset_token(clean_email)
    base_url = get_base_url(request)
    dev_reset_link = f"{base_url}/reset-password?token={token}"

    print(f"[AUTH] Password reset requested for {clean_email}: {dev_reset_link}")

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "success_message": f"If an account is registered with {clean_email}, we've sent password reset instructions.",
            "dev_reset_link": dev_reset_link
        }
    )

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = Query(...)):
    email = verify_password_reset_token(token)
    if not email:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"error": "This password reset link is invalid or has expired (valid for 1 hour). Please request a new one."}
        )
    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={"token": token}
    )

@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    email = verify_password_reset_token(token)
    if not email:
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"error": "Password reset token has expired or is invalid. Please request a new link."}
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"token": token, "error": "Passwords do not match. Please re-enter."}
        )
    is_complex, err_msg = validate_password_strength(new_password)
    if not is_complex:
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"token": token, "error": err_msg}
        )

    # Hash new password with Argon2id and update database
    new_hash = hash_password(new_password)
    update_owner_password(email, new_hash)
    reset_failed_logins(email)

    return RedirectResponse(
        url="/login?error=Password+successfully+updated.+Please+sign+in+with+your+new+password.",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ==========================================
# MANDATORY LEGAL & COMPLIANCE PAGES (RAZORPAY)
# ==========================================

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request=request, name="privacy.html")

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request=request, name="terms.html")

@app.get("/refund", response_class=HTMLResponse)
async def refund_page(request: Request):
    return templates.TemplateResponse(request=request, name="refund.html")

@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse(request=request, name="contact.html", context={"success": False})

@app.post("/contact", response_class=HTMLResponse)
async def contact_submit(
    request: Request,
    name: str = Form(...),
    contact: str = Form(...),
    message: str = Form(...),
    website_hp: Optional[str] = Form(None)
):
    # Spam honeypot trap: if bot filled hidden field, quietly accept without processing
    if website_hp:
        return templates.TemplateResponse(request=request, name="contact.html", context={"success": True})

    clean_name = name.strip()[:100]
    clean_contact = contact.strip()[:100]
    clean_message = message.strip()[:1000]

    print(f"[CONTACT INQUIRY] From: {clean_name} ({clean_contact}) - Message: {clean_message}")

    return templates.TemplateResponse(request=request, name="contact.html", context={"success": True})


# ==========================================
# SEO & WEB STANDARDS (SITEMAP, ROBOTS, FAVICON)
# ==========================================

@app.get("/robots.txt", response_class=Response)
async def robots_txt():
    content = "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /dashboard\nSitemap: https://ai-google-review-generator-jarvis-def9.vercel.app/sitemap.xml\n"
    return Response(content=content, media_type="text/plain")

@app.get("/sitemap.xml", response_class=Response)
async def sitemap_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/pricing</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/login</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/signup</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/privacy</loc><changefreq>yearly</changefreq><priority>0.4</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/terms</loc><changefreq>yearly</changefreq><priority>0.4</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/refund</loc><changefreq>yearly</changefreq><priority>0.4</priority></url>
  <url><loc>https://ai-google-review-generator-jarvis-def9.vercel.app/contact</loc><changefreq>yearly</changefreq><priority>0.5</priority></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")

@app.get("/favicon.ico", response_class=Response)
async def favicon_ico():
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='#F59E0B'><path d='M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z'/></svg>"""
    return Response(content=svg, media_type="image/svg+xml")


# ==========================================
# GOOGLE / GMAIL OAUTH (SIGN IN & SIGN UP)
# ==========================================

@app.get("/auth/google/login")
async def google_oauth_login(request: Request, action: Optional[str] = "login"):
    client_id = get_setting("google_client_id", os.getenv("GOOGLE_CLIENT_ID", "")).strip()
    target_page = "/signup" if action == "signup" else "/login"

    if not client_id:
        return RedirectResponse(
            url=f"{target_page}?error=Google+Sign-In+is+not+configured+yet.+Please+add+Google+Client+ID+in+Admin+Settings.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    base_url = get_base_url(request)
    redirect_uri = f"{base_url}/auth/google/callback"
    state_token = create_oauth_state_token(action=action or "login")

    google_auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
        "access_type": "online",
        "prompt": "select_account"
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(google_auth_params)}"
    return RedirectResponse(url=auth_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None
):
    action = verify_oauth_state_token(state) if state else "login"
    login_fallback = "/signup" if action == "signup" else "/login"

    if error:
        return RedirectResponse(url=f"{login_fallback}?error=Google+sign-in+cancelled", status_code=status.HTTP_303_SEE_OTHER)

    if not code:
        return RedirectResponse(url=f"{login_fallback}?error=Missing+Google+authorization+code", status_code=status.HTTP_303_SEE_OTHER)

    client_id = get_setting("google_client_id", os.getenv("GOOGLE_CLIENT_ID", "")).strip()
    client_secret = get_setting("google_client_secret", os.getenv("GOOGLE_CLIENT_SECRET", "")).strip()

    if not client_id or not client_secret:
        return RedirectResponse(url=f"{login_fallback}?error=Google+OAuth+credentials+are+not+configured", status_code=status.HTTP_303_SEE_OTHER)

    base_url = get_base_url(request)
    redirect_uri = f"{base_url}/auth/google/callback"

    # 1. Exchange code for access token
    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            },
            timeout=10
        )
        if token_resp.status_code != 200:
            return RedirectResponse(url=f"{login_fallback}?error=Failed+to+exchange+token+with+Google", status_code=status.HTTP_303_SEE_OTHER)

        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return RedirectResponse(url=f"{login_fallback}?error=Google+did+not+return+access+token", status_code=status.HTTP_303_SEE_OTHER)

        # 2. Fetch Google User Profile
        user_resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        if user_resp.status_code != 200:
            return RedirectResponse(url=f"{login_fallback}?error=Could+not+retrieve+Google+user+profile", status_code=status.HTTP_303_SEE_OTHER)

        user_info = user_resp.json()
        email = user_info.get("email", "").strip().lower()
        full_name = user_info.get("name", "")
        picture = user_info.get("picture", "")

        if not email:
            return RedirectResponse(url=f"{login_fallback}?error=Google+account+has+no+verified+email", status_code=status.HTTP_303_SEE_OTHER)

    except Exception as e:
        return RedirectResponse(url=f"{login_fallback}?error=Google+connection+error", status_code=status.HTTP_303_SEE_OTHER)

    # 3. Check if owner already exists
    existing_owner = get_owner_by_email(email)
    if existing_owner:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE business_owners SET last_login_at = ? WHERE id = ?", (now_str, existing_owner["id"]))
        conn.commit()
        conn.close()

        if existing_owner.get("business_id"):
            log_activity(existing_owner["business_id"], "login", f"Owner signed in via Google ({email})")

        token = create_owner_token(existing_owner["id"], email, existing_owner.get("business_id", 0))
        target_url = "/dashboard"
        if is_payments_enabled() and existing_owner.get("subscription_status") not in ["active", "trialing"]:
            target_url = "/pricing"

        resp = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(
            key="owner_session",
            value=token,
            httponly=True,
            max_age=86400 * 14,
            samesite="lax"
        )
        return resp

    # 4. New user: generate signed token and redirect to finish business setup
    signup_token = create_google_signup_token(email=email, name=full_name, picture=picture)
    return RedirectResponse(
        url=f"/signup/google-complete?token={signup_token}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/signup/google-complete", response_class=HTMLResponse)
async def google_signup_complete_page(request: Request, token: Optional[str] = None):
    if get_current_owner(request):
        return RedirectResponse(url="/dashboard")

    profile = verify_google_signup_token(token) if token else None
    if not profile:
        return RedirectResponse(url="/signup?error=Google+session+expired.+Please+try+again.")

    return templates.TemplateResponse(
        request=request,
        name="google_signup_complete.html",
        context={
            "profile": profile,
            "token": token,
            "error": None
        }
    )


@app.post("/signup/google-complete")
async def google_signup_complete_submit(
    request: Request,
    token: str = Form(...),
    business_name: str = Form(...),
    category: str = Form("General"),
    google_business_profile_url: Optional[str] = Form(None),
    google_review_url: Optional[str] = Form(None),
    accent_color: Optional[str] = Form("#2563EB"),
    accent_hex: Optional[str] = Form("#2563EB"),
    logo_file: Optional[UploadFile] = File(None),
    tags: Optional[str] = Form("Friendly Staff, Quick Service, Great Quality")
):
    profile = verify_google_signup_token(token)
    if not profile:
        return RedirectResponse(url="/signup?error=Session+expired.+Please+sign+up+with+Google+again.")

    email_clean = profile["email"].strip().lower()
    full_name = profile.get("name") or business_name.strip()
    bname_clean = business_name.strip()
    raw_google_url = (google_business_profile_url or google_review_url or "").strip()

    if not bname_clean or not raw_google_url:
        return templates.TemplateResponse(
            request=request,
            name="google_signup_complete.html",
            context={
                "profile": profile,
                "token": token,
                "error": "Business Name and Google Business Profile Link are required."
            },
            status_code=400
        )

    try:
        place_id, direct_review_url = process_google_profile_link(raw_google_url)
    except ValueError as ve:
        return templates.TemplateResponse(
            request=request,
            name="google_signup_complete.html",
            context={
                "profile": profile,
                "token": token,
                "error": str(ve),
                "business_name": bname_clean,
                "category": category,
                "google_business_profile_url": raw_google_url,
                "tags": tags
            },
            status_code=400
        )

    # Check if registered concurrently
    if get_owner_by_email(email_clean):
        return RedirectResponse(url="/login?error=An+account+with+this+Google+email+already+exists.+Please+sign+in.")

    # Handle logo upload with magic byte verification and raster re-encoding
    logo_url = None
    if logo_file and logo_file.filename:
        contents = await logo_file.read()
        is_valid_img, img_err, safe_bytes, safe_ext = validate_uploaded_image_bytes(contents, logo_file.filename)
        if is_valid_img and safe_bytes:
            uploads_dir = os.path.join(BASE_DIR, "static", "uploads")
            os.makedirs(uploads_dir, exist_ok=True)
            safe_name = f"{uuid.uuid4().hex[:16]}{safe_ext}"
            file_path = os.path.join(uploads_dir, safe_name)
            with open(file_path, "wb") as fp:
                fp.write(safe_bytes)
            logo_url = f"/static/uploads/{safe_name}"


    slug = generate_slug(bname_clean)
    brand_color = accent_hex or accent_color or "#2563EB"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else ["Friendly Staff", "Quick Service"]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()

    payments_on = is_payments_enabled()
    init_active = 0 if payments_on else 1
    init_status = "pending_payment" if payments_on else "active"

    cursor.execute("""
        INSERT INTO businesses (slug, name, category, google_review_url, logo_url, accent_color, tags_preset, active, subscription_status, plan_tier, payment_method, last_google_url_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'monthly', 'upi_autopay', ?)
    """, (
        slug,
        bname_clean,
        category,
        direct_review_url,
        logo_url,
        brand_color,
        json.dumps(tag_list),
        init_active,
        init_status,
        now_str
    ))
    business_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO business_owners (email, password_hash, full_name, business_id, last_login_at)
        VALUES (?, 'oauth:google', ?, ?, ?)
    """, (email_clean, full_name, business_id, now_str))
    owner_id = cursor.lastrowid

    cursor.execute("UPDATE businesses SET owner_id = ? WHERE id = ?", (owner_id, business_id))
    conn.commit()
    conn.close()

    log_activity(business_id, "signup", f"Owner {email_clean} registered business '{bname_clean}' via Google")
    log_activity(business_id, "google_url_updated", f"Configured Google Business Profile direct review link for Place ID {place_id}: {direct_review_url}")

    token_str = create_owner_token(owner_id, email_clean, business_id)
    target_url = "/dashboard" if not payments_on else "/pricing"
    resp = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        key="owner_session",
        value=token_str,
        httponly=True,
        max_age=86400 * 14,
        samesite="lax"
    )
    return resp


# ==========================================
# PRICING & PAYMENT GATEWAY INTEGRATION
# ==========================================

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    owner = get_current_owner(request)
    payments_on = is_payments_enabled()
    return templates.TemplateResponse(
        request=request,
        name="pricing.html",
        context={
            "owner": owner,
            "plans": PLANS,
            "payments_enabled": payments_on
        }
    )

@app.post("/api/billing/create-checkout")
async def api_create_checkout(req: CreateCheckoutRequest, request: Request):
    owner = get_current_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Please log in to continue")

    business_id = owner["business_id"]
    checkout_data = create_checkout_session(
        business_id=business_id,
        plan_type=req.plan_type,
        payment_mode=req.payment_mode
    )
    return checkout_data

@app.post("/api/billing/verify-payment")
async def api_verify_payment(req: VerifyPaymentRequest, request: Request):
    owner = get_current_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Please log in to continue")

    business_id = owner["business_id"]

    # Server-side validation of plan
    if req.plan_type not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan selected")

    res = verify_and_activate_subscription(
        business_id=business_id,
        plan_type=req.plan_type,
        payment_mode=req.payment_mode,
        payment_id=req.payment_id,
        order_id=req.order_id,
        subscription_id=req.subscription_id
    )
    return res

@app.post("/api/payments/webhook")
async def api_payment_webhook(request: Request):
    """
    Razorpay Webhook endpoint for recurring UPI Autopay renewals, mandate events, and captures.
    Verifies HMAC-SHA256 signature and guarantees idempotency.
    """
    webhook_secret = get_setting("razorpay_webhook_secret", os.getenv("RAZORPAY_WEBHOOK_SECRET", "")).strip()
    body_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # If webhook secret is configured, enforce strict HMAC signature verification
    if webhook_secret:
        if not signature or not verify_razorpay_webhook_signature(body_bytes, signature, webhook_secret):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        body = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        event = body.get("event", "")
        event_id = body.get("id") or body.get("event_id") or ""
        
        # Enforce webhook idempotency
        if event_id and is_webhook_event_processed(event_id):
            return {"status": "ok", "message": "Duplicate event ignored"}

        if event_id:
            mark_webhook_event_processed(event_id)

        # Process webhook payload safely
        payload_entity = body.get("payload", {})

        # 1. Payment Captured / Order Paid (One-Time or Initial Autopay)
        if event in ("payment.captured", "order.paid"):
            payment_obj = payload_entity.get("payment", {}).get("entity", {})
            notes = payment_obj.get("notes", {})
            biz_id_str = notes.get("business_id")
            if biz_id_str and str(biz_id_str).isdigit():
                biz_id = int(biz_id_str)
                plan = notes.get("plan", "monthly")
                mode = notes.get("mode", "one_time")
                verify_and_activate_subscription(
                    business_id=biz_id,
                    plan_type=plan,
                    payment_mode=mode,
                    payment_id=payment_obj.get("id", ""),
                    order_id=payment_obj.get("order_id", "")
                )
                print(f"[WEBHOOK] Activated subscription for business {biz_id} via {event}")

        # 2. Recurring Subscription Charged / Activated (UPI Autopay Mandates)
        elif event in ("subscription.charged", "subscription.activated"):
            sub_obj = payload_entity.get("subscription", {}).get("entity", {})
            notes = sub_obj.get("notes", {})
            biz_id_str = notes.get("business_id")
            if biz_id_str and str(biz_id_str).isdigit():
                biz_id = int(biz_id_str)
                verify_and_activate_subscription(
                    business_id=biz_id,
                    plan_type="monthly",
                    payment_mode="upi_autopay",
                    payment_id=sub_obj.get("id", ""),
                    subscription_id=sub_obj.get("id", "")
                )
                print(f"[WEBHOOK] Renewed/Activated UPI Autopay mandate for business {biz_id} via {event}")

        # 3. Recurring Subscription Cancelled / Halted
        elif event in ("subscription.cancelled", "subscription.halted"):
            sub_obj = payload_entity.get("subscription", {}).get("entity", {})
            notes = sub_obj.get("notes", {})
            biz_id_str = notes.get("business_id")
            if biz_id_str and str(biz_id_str).isdigit():
                biz_id = int(biz_id_str)
                cancel_autopay_mandate(biz_id)
                print(f"[WEBHOOK] Cancelled recurring mandate for business {biz_id} via {event}")

        # 4. Refund Processed
        elif event == "refund.processed":
            refund_obj = payload_entity.get("refund", {}).get("entity", {})
            pay_id = refund_obj.get("payment_id", "")
            print(f"[WEBHOOK] Refund processed for payment {pay_id}")

        return {"status": "ok", "event_received": event}
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return {"status": "ok"}



# ==========================================
# BUSINESS OWNER DASHBOARD & SETTINGS
# ==========================================

@app.get("/dashboard", response_class=HTMLResponse)
async def owner_dashboard(request: Request):
    owner = require_owner(request)
    business_id = owner["business_id"]

    # Retrieve live metrics for this business
    metrics = get_business_metrics(business_id)

    # Retrieve payments history
    payments = get_business_payments(business_id)

    # WhatsApp Alert Settings & Audit Logs (No message content stored)
    whatsapp_settings = get_business_whatsapp_settings(business_id)
    whatsapp_logs = get_whatsapp_alert_logs(business_id, limit=10)

    # Google Business Profile Connection & Reviews Store
    google_status = get_business_google_connection_status(business_id)
    autoreply_settings = get_business_autoreply_settings(business_id)
    google_reviews = get_business_google_reviews(business_id, limit=50)
    reply_usage = get_business_reply_usage(business_id)
    reply_limit = get_plan_reply_limit(owner.get("plan_tier"))

    tags_list = []
    try:
        tags_list = json.loads(owner.get("tags_preset") or "[]")
    except Exception:
        tags_list = []

    is_active = is_subscription_active(owner)

    return templates.TemplateResponse(
        request=request,
        name="owner_dashboard.html",
        context={
            "owner": owner,
            "metrics": metrics,
            "payments": payments,
            "whatsapp_settings": whatsapp_settings,
            "whatsapp_logs": whatsapp_logs,
            "google_status": google_status,
            "autoreply_settings": autoreply_settings,
            "google_reviews": google_reviews,
            "reply_usage": reply_usage,
            "reply_limit": reply_limit,
            "tags_str": ", ".join(tags_list),
            "is_active": is_active
        }
    )

@app.get("/dashboard/flyer", response_class=HTMLResponse)
async def owner_flyer(request: Request):
    owner = require_owner(request)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses WHERE id = ?", (owner["business_id"],))
    bus = cursor.fetchone()
    conn.close()

    if not bus:
        raise HTTPException(status_code=404, detail="Business not found")

    return templates.TemplateResponse(
        request=request,
        name="flyer.html",
        context={"business": dict(bus)}
    )

@app.put("/api/owner/profile")
async def api_owner_update_profile(req: OwnerProfileUpdateRequest, request: Request):
    owner = require_owner(request)
    business_id = owner["business_id"]

    try:
        direct_review_url = req.get_review_url()
        place_id = extract_google_place_id(direct_review_url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    conn = get_db()
    cursor = conn.cursor()

    # Check if Google Review URL was updated
    cursor.execute("SELECT google_review_url FROM businesses WHERE id = ?", (business_id,))
    old_row = cursor.fetchone()
    old_url = old_row["google_review_url"] if old_row else ""
    url_changed = old_url != direct_review_url

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE businesses
        SET name = ?, category = ?, google_review_url = ?, logo_url = ?, accent_color = ?, tags_preset = ?,
            last_google_url_update = CASE WHEN ? THEN ? ELSE last_google_url_update END
        WHERE id = ?
    """, (
        req.name.strip(),
        (req.category or "General").strip(),
        direct_review_url,
        req.logo_url.strip() if req.logo_url else None,
        req.accent_color.strip() if req.accent_color else "#2563EB",
        json.dumps(req.tags_preset),
        1 if url_changed else 0,
        now_str,
        business_id
    ))
    conn.commit()
    conn.close()

    if url_changed:
        log_activity(
            business_id=business_id,
            action="google_url_updated",
            details=f"Owner updated Google Business Profile destination: Place ID {place_id} -> {direct_review_url}"
        )
    else:
        log_activity(business_id, "profile_updated", "Owner updated business profile and branding options")

    return {"status": "updated", "direct_review_url": direct_review_url, "place_id": place_id}

@app.post("/api/owner/cancel-autopay")
async def api_owner_cancel_autopay(request: Request):
    owner = require_owner(request)
    cancel_autopay_mandate(owner["business_id"])
    return {"status": "cancelled"}


# ==========================================
# CUSTOMER FACING ROUTES (Mobile First / NFC / QR)
# ==========================================

@app.get("/demo")
@app.get("/try")
@app.get("/try-demo")
@app.get("/try-review")
@app.get("/try-remove")
async def demo_review_redirect():
    return RedirectResponse(url="/r/artisan-roast", status_code=status.HTTP_302_FOUND)

@app.get("/r/{slug}", response_class=HTMLResponse)
async def customer_review_landing(slug: str, request: Request):
    """
    Landing page accessed when customer scans QR code or taps NFC card.
    Enforces automatic activation / deactivation gating based on subscription status.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses WHERE slug = ?", (slug,))
    business = cursor.fetchone()
    conn.close()

    if not business:
        return HTMLResponse(
            content="""
            <div style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Business Not Found</h2>
                <p>The review link you tapped is invalid or has been removed.</p>
                <a href="/login">Return to Portal</a>
            </div>
            """,
            status_code=404
        )

    bus_dict = dict(business)

    # AUTOMATIC ACCESS GATING BASED ON SUBSCRIPTION STATUS
    if not is_subscription_active(bus_dict):
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Review Portal Paused - {bus_dict.get('name', 'Business')}</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
            </head>
            <body class="bg-slate-900 text-slate-100 min-h-screen flex items-center justify-center p-6 text-center">
                <div class="max-w-md w-full bg-slate-800/95 border border-slate-700/80 p-8 rounded-3xl shadow-2xl">
                    <div class="w-16 h-16 rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/30 flex items-center justify-center mx-auto mb-4 text-2xl">
                        <i class="fa-solid fa-clock-rotate-left"></i>
                    </div>
                    <h1 class="text-xl font-bold text-white mb-2">{bus_dict.get('name', 'Business')}</h1>
                    <p class="text-xs text-slate-400 mb-6 leading-relaxed">
                        This business's Google review collection portal is temporarily paused pending subscription renewal. If you are the owner, please log in to activate your account.
                    </p>
                    <a href="/login" class="block w-full py-3 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl text-xs transition">
                        Owner Login / Activate Subscription
                    </a>
                </div>
            </body>
            </html>
            """,
            status_code=402
        )

    # Convert row to dict
    try:
        bus_dict["tags_preset"] = json.loads(bus_dict.get("tags_preset") or "[]")
    except Exception:
        bus_dict["tags_preset"] = ["Friendly Staff", "Quick Service", "Great Quality"]

    return templates.TemplateResponse(
        request=request,
        name="customer_review.html",
        context={"business": bus_dict}
    )


@app.post("/api/reviews/generate")
async def api_generate_reviews(req: ReviewGenerateRequest, request: Request):
    """
    Generates 3 natural review drafts using OpenAI GPT-4o mini or smart fallback.
    Crucial guardrail: Strictly restricted to positive ratings (4 or 5 stars).
    """
    # Abuse prevention: Rate limit per IP to protect AI credit usage
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"ai_gen_{client_ip}", max_requests=30, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait a moment before generating more review drafts."
        )

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name, category FROM businesses WHERE id = ?", (req.business_id,))
    bus = cursor.fetchone()
    conn.close()

    if not bus:
        raise HTTPException(status_code=404, detail="Business not found")

    try:
        result = generate_review_drafts(
            business_name=bus["name"],
            business_category=bus["category"],
            rating=req.rating,
            selected_tags=req.selected_tags,
            custom_note=req.custom_note
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Customer Feedback Alert to Owner's WhatsApp (1-2 Stars) ───────────────────

@app.post("/api/feedback/alert")
async def api_submit_feedback_alert(req: FeedbackAlertRequest, request: Request):
    """
    Change 2: When a customer picks 1 or 2 stars, sends their optional private message
    directly to the business owner's verified WhatsApp number.
    Rate-limited and protected with honeypot spam detection.
    CRITICAL PRIVACY RULE: DO NOT store the feedback text in any database table.
    """
    # 1. Spam honeypot check
    if req.hp_website and req.hp_website.strip():
        # Silent ignore of automated bot submissions
        return {"status": "success", "success": True, "filtered": True, "alert_sent": False, "message": "Feedback received"}

    # 2. Rate limiting (max 5 submissions per 10 minutes per IP)
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"feedback_alert_{client_ip}", max_requests=5, window_seconds=600):
        raise HTTPException(
            status_code=429,
            detail="Too many feedback submissions. Please wait a few minutes."
        )

    clean_msg = (req.message or req.feedback_text or "").strip()
    if not clean_msg:
        raise HTTPException(status_code=400, detail="Feedback message is required")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM businesses WHERE id = ?", (req.business_id,))
    bus = cursor.fetchone()
    conn.close()

    if not bus:
        raise HTTPException(status_code=404, detail="Business not found")

    cust_phone = (req.customer_phone or req.customer_contact or "").strip()

    # Send straight to owner's WhatsApp without storing message text in database!
    success, msg = dispatch_customer_feedback_alert(
        business_id=req.business_id,
        business_name=bus["name"],
        rating=req.rating,
        message=clean_msg,
        customer_name=req.customer_name,
        customer_phone=cust_phone
    )

    return {
        "status": "success",
        "success": True,
        "alert_sent": success,
        "message": "Alert sent directly to business owner" if success else msg
    }


@app.post("/api/feedback/submit")
async def api_submit_feedback_legacy(req: FeedbackSubmitRequest, request: Request):
    """
    Backwards-compatible endpoint for feedback submissions.
    Ensures ZERO database writes of customer feedback text, routing alert straight to WhatsApp.
    """
    alert_req = FeedbackAlertRequest(
        business_id=req.business_id,
        rating=req.rating,
        message=req.feedback_text,
        customer_name=req.customer_name,
        customer_phone=req.customer_contact
    )
    return await api_submit_feedback_alert(alert_req, request)


# ── Owner WhatsApp Alert Management Endpoints ─────────────────────────────────

@app.post("/api/owner/whatsapp/send-otp")
async def api_owner_whatsapp_send_otp(req: OwnerWhatsAppSendOtpRequest, request: Request):
    owner = require_owner(request)
    client_ip = get_client_ip(request)
    if not check_rate_limit(f"otp_req_{client_ip}_{owner['id']}", max_requests=4, window_seconds=600):
        raise HTTPException(status_code=429, detail="Too many OTP requests. Please wait a few minutes.")

    success, msg = generate_and_send_owner_otp(owner["business_id"], req.phone_number)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/owner/whatsapp/verify-otp")
async def api_owner_whatsapp_verify_otp(req: OwnerWhatsAppVerifyOtpRequest, request: Request):
    owner = require_owner(request)
    success, msg = verify_owner_otp(owner["business_id"], req.phone_number, req.otp_code)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/owner/whatsapp/toggle")
async def api_owner_whatsapp_toggle(req: OwnerWhatsAppToggleRequest, request: Request):
    owner = require_owner(request)
    success, msg = toggle_owner_whatsapp_alerts(owner["business_id"], req.enabled)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.get("/api/owner/whatsapp/status")
async def api_owner_whatsapp_status(request: Request):
    owner = require_owner(request)
    settings = get_business_whatsapp_settings(owner["business_id"])
    logs = get_whatsapp_alert_logs(owner["business_id"], limit=20)
    return {"settings": settings, "logs": logs}


# ── Google Business Profile OAuth & Auto-Reply Endpoints ──────────────────────

@app.get("/auth/google/business/connect")
async def auth_google_business_connect(request: Request):
    owner = require_owner(request)
    base_url = get_base_url(request)
    redirect_uri = f"{base_url}/auth/google/business/callback"
    oauth_url = build_google_oauth_url(owner["business_id"], owner["id"], redirect_uri)
    return RedirectResponse(url=oauth_url)


@app.get("/auth/google/business/callback")
async def auth_google_business_callback(
    request: Request,
    code: Optional[str] = Query(None),
    mock_connect: Optional[int] = Query(None),
    business_id: Optional[int] = Query(None)
):
    owner = require_owner(request)
    bid = owner["business_id"]
    base_url = get_base_url(request)
    redirect_uri = f"{base_url}/auth/google/business/callback"

    auth_code = code or ("mock_auth_code" if mock_connect else "")
    if not auth_code:
        return RedirectResponse(url="/dashboard?tab=reviews&error=no_auth_code")

    success, msg = exchange_code_for_tokens(auth_code, redirect_uri, bid)
    if success:
        # Trigger initial sync of reviews in background
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM businesses WHERE id = ?", (bid,))
            bus = cursor.fetchone()
            conn.close()
            bus_name = bus["name"] if bus else "My Business"
            sync_and_process_google_reviews(bid, bus_name, owner.get("plan_tier", "monthly"), base_url)
        except Exception as e:
            print(f"[Initial Review Sync Error] {e}")

        return RedirectResponse(url="/dashboard?tab=reviews&connected=true")
    else:
        return RedirectResponse(url=f"/dashboard?tab=reviews&error={requests.utils.quote(msg)}")


@app.post("/api/google/disconnect")
async def api_google_disconnect(request: Request):
    owner = require_owner(request)
    disconnect_google_business_profile(owner["business_id"])
    return {"status": "ok", "message": "Google Business Profile disconnected."}


@app.get("/api/google/reviews")
async def api_get_google_reviews(request: Request):
    owner = require_owner(request)
    bid = owner["business_id"]
    status_data = get_business_google_connection_status(bid)
    reviews = get_business_google_reviews(bid, limit=100)
    usage = get_business_reply_usage(bid)
    limit = get_plan_reply_limit(owner.get("plan_tier"))
    return {
        "connected": status_data.get("connected", False),
        "location_name": status_data.get("location_name", ""),
        "reviews": reviews,
        "reply_usage": usage,
        "reply_limit": limit
    }


@app.post("/api/google/reviews/sync")
async def api_sync_google_reviews(request: Request):
    owner = require_owner(request)
    bid = owner["business_id"]
    base_url = get_base_url(request)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM businesses WHERE id = ?", (bid,))
    bus = cursor.fetchone()
    conn.close()
    bus_name = bus["name"] if bus else "My Business"

    result = sync_and_process_google_reviews(
        business_id=bid,
        business_name=bus_name,
        plan_tier=owner.get("plan_tier", "monthly"),
        base_url=base_url
    )
    return result


@app.post("/api/google/reviews/{review_id}/reply")
async def api_post_review_reply(review_id: str, req: GoogleReviewReplyRequest, request: Request):
    owner = require_owner(request)
    success, msg = approve_and_post_reply(owner["business_id"], review_id, req.reply_text)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/api/google/reviews/{review_id}/regenerate")
async def api_regenerate_review_reply(review_id: str, request: Request):
    owner = require_owner(request)
    bid = owner["business_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM businesses WHERE id = ?", (bid,))
    bus = cursor.fetchone()
    conn.close()
    bus_name = bus["name"] if bus else "My Business"

    success, msg, new_reply = regenerate_ai_reply(bid, bus_name, review_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg, "drafted_reply": new_reply}


@app.get("/api/google/settings/autoreply")
async def api_get_autoreply_settings(request: Request):
    owner = require_owner(request)
    settings = get_business_autoreply_settings(owner["business_id"])
    return settings


@app.post("/api/google/settings/autoreply")
async def api_save_autoreply_settings(req: GoogleAutoReplySettingsRequest, request: Request):
    owner = require_owner(request)
    data = req.dict()
    save_business_autoreply_settings(owner["business_id"], data)
    return {"status": "ok", "message": "Auto-reply preferences saved successfully."}


@app.post("/api/analytics/event")
async def api_log_analytics(req: AnalyticsEventRequest, request: Request):
    """
    Tracks scans, rating interactions, completions, and clicks.
    """
    user_agent = request.headers.get("user-agent", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO analytics_events (business_id, event_type, rating, user_agent)
        VALUES (?, ?, ?, ?)
    """, (req.business_id, req.event_type, req.rating, user_agent))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/qr/{slug}.png")
async def api_get_qr_png(slug: str, request: Request, download: bool = False):
    """
    Dynamically generates high-res QR code PNG for the business landing page.
    """
    scheme = request.url.scheme
    netloc = request.url.netloc
    landing_url = f"{scheme}://{netloc}/r/{slug}"

    png_bytes = generate_qr_png_bytes(landing_url, box_size=12, border=3)

    headers = {"Content-Type": "image/png"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{slug}-review-qr.png"'

    return Response(content=png_bytes, media_type="image/png", headers=headers)


@app.get("/api/qr/{slug}.svg")
async def api_get_qr_svg(slug: str, request: Request):
    """
    Returns vector SVG QR code.
    """
    scheme = request.url.scheme
    netloc = request.url.netloc
    landing_url = f"{scheme}://{netloc}/r/{slug}"
    svg_str = generate_qr_svg_string(landing_url)
    return Response(content=svg_str, media_type="image/svg+xml")


# ==========================================
# RESELLER ADMIN PORTAL (FULL VISIBILITY)
# ==========================================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_current_reseller(request)
    if not user:
        return RedirectResponse(url="/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    # 1. Get businesses with individual metrics
    cursor.execute("""
        SELECT b.*,
            (SELECT COUNT(*) FROM analytics_events WHERE business_id = b.id AND event_type = 'scan') as scans_count,
            (SELECT COUNT(*) FROM analytics_events WHERE business_id = b.id AND event_type = 'completed_review') as reviews_count
        FROM businesses b
        ORDER BY b.created_at DESC
    """)
    raw_businesses = cursor.fetchall()

    businesses = []
    total_scans = 0
    total_completed = 0
    total_google_connected = 0
    total_replies_posted = 0
    active_subscriptions = 0
    platform_mrr = 0.0

    for row in raw_businesses:
        b_dict = dict(row)
        bid = b_dict.get("id")
        scans = b_dict.get("scans_count", 0)
        reviews = b_dict.get("reviews_count", 0)

        # Google Business connection & reply counts (NO review content shown to admin)
        conn_status = get_business_google_connection_status(bid)
        is_connected = conn_status.get("connected", False)
        b_dict["google_connected"] = is_connected
        usage = get_business_reply_usage(bid)
        replies_posted = usage.get("total_posted", 0)
        b_dict["replies_posted_count"] = replies_posted

        total_scans += scans
        total_completed += reviews
        if is_connected:
            total_google_connected += 1
        total_replies_posted += replies_posted

        if b_dict.get("subscription_status") in ["active", "trialing"]:
            active_subscriptions += 1
            if b_dict.get("plan_tier") == "yearly":
                platform_mrr += (8999.0 / 12)
            else:
                platform_mrr += 999.0

        businesses.append(b_dict)

    total_businesses = len(businesses)
    conversion_rate = round((total_completed / total_scans * 100), 1) if total_scans > 0 else 0.0

    stats = {
        "total_businesses": total_businesses,
        "total_scans": total_scans,
        "total_completed": total_completed,
        "conversion_rate": conversion_rate,
        "total_google_connected": total_google_connected,
        "total_replies_posted": total_replies_posted,
        "active_subscriptions": active_subscriptions,
        "platform_mrr": round(platform_mrr, 0)
    }

    # 2. Get registered business owners list
    cursor.execute("""
        SELECT b.id as business_id, b.name as business_name, b.slug as business_slug,
               b.subscription_status, b.plan_tier, b.payment_method, b.subscription_cycle_end,
               b.last_google_url_update, b.created_at as created_at,
               bo.email as owner_email, bo.full_name as owner_name, bo.last_login_at, bo.created_at as signup_date
        FROM businesses b
        LEFT JOIN business_owners bo ON b.owner_id = bo.id
        ORDER BY b.created_at DESC
    """)
    owners = [dict(r) for r in cursor.fetchall()]

    # 3. Fetch platform payments history
    payments = get_all_platform_payments(100)

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "user": user,
            "businesses": businesses,
            "owners": owners,
            "stats": stats,
            "payments": payments
        }
    )


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if get_current_reseller(request):
        return RedirectResponse(url="/admin")
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": None}
    )


@app.post("/admin/login")
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    u_clean = username.strip()
    client_ip = get_client_ip(request)

    # Rate limiting on admin login
    if not check_rate_limit(f"admin_login_{client_ip}", max_requests=20, window_seconds=60):
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": "Too many login attempts. Please wait a moment and try again."},
            status_code=429
        )

    # Account Lockout / Slowdown check
    is_locked, rem_sec = is_account_locked(u_clean)
    if is_locked:
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": f"Account temporarily locked due to repeated failed attempts. Please try again in {rem_sec} seconds."},
            status_code=400
        )

    if authenticate_reseller(u_clean, password):
        reset_failed_logins(u_clean)
        token = create_session_token(u_clean)
        resp = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(
            key="reseller_session",
            value=token,
            httponly=True,
            secure=is_secure,
            max_age=86400,
            samesite="Strict"
        )
        return resp

    record_failed_login(u_clean)
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": "Invalid username or password"},
        status_code=400
    )


@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin/login")
    resp.delete_cookie("reseller_session")
    resp.delete_cookie(CSRF_COOKIE_NAME)
    return resp


@app.get("/admin/business/{business_id}/flyer", response_class=HTMLResponse)
async def printable_flyer(business_id: int, request: Request):
    user = get_current_reseller(request)
    if not user:
        return RedirectResponse(url="/admin/login")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses WHERE id = ?", (business_id,))
    bus = cursor.fetchone()
    conn.close()

    if not bus:
        raise HTTPException(status_code=404, detail="Business not found")

    return templates.TemplateResponse(
        request=request,
        name="flyer.html",
        context={"business": dict(bus)}
    )


# ==========================================
# RESELLER ADMIN REST APIs (FULL DRILLDOWN)
# ==========================================

@app.get("/api/admin/businesses/{business_id}/inspect")
async def api_admin_inspect_business(
    business_id: int,
    current_user: str = Depends(require_reseller)
):
    """
    Read-only drilldown for admin reseller: returns identical owner metrics,
    owner account info, activity audit logs, and payment transactions.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT b.*, bo.email as owner_email, bo.full_name as owner_name, bo.last_login_at, bo.created_at as signup_date
        FROM businesses b
        LEFT JOIN business_owners bo ON b.owner_id = bo.id
        WHERE b.id = ?
    """, (business_id,))
    bus_row = cursor.fetchone()
    conn.close()

    if not bus_row:
        raise HTTPException(status_code=404, detail="Business not found")

    metrics = get_business_metrics(business_id)
    logs = get_business_activity_logs(business_id, 50)
    payments = get_business_payments(business_id)

    return {
        "business": dict(bus_row),
        "metrics": metrics,
        "logs": logs,
        "payments": payments
    }


@app.post("/api/admin/businesses")
async def api_create_business(
    data: BusinessCreateRequest,
    current_user: str = Depends(require_reseller)
):
    try:
        direct_review_url = data.get_review_url()
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO businesses (name, slug, category, google_review_url, logo_url, accent_color, tags_preset, subscription_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            data.name,
            data.slug,
            data.category,
            direct_review_url,
            data.logo_url,
            data.accent_color,
            json.dumps(data.tags_preset)
        ))
        conn.commit()
        new_id = cursor.lastrowid
        return {"status": "created", "id": new_id, "direct_review_url": direct_review_url}
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail=f"Slug '{data.slug}' already exists. Please choose a different unique short link.")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/admin/businesses/{business_id}")
async def api_get_business(
    business_id: int,
    current_user: str = Depends(require_reseller)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses WHERE id = ?", (business_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Business not found")

    bus = dict(row)
    try:
        bus["tags_preset"] = json.loads(bus.get("tags_preset") or "[]")
    except Exception:
        bus["tags_preset"] = []
    return bus


@app.put("/api/admin/businesses/{business_id}")
async def api_update_business(
    business_id: int,
    data: BusinessUpdateRequest,
    current_user: str = Depends(require_reseller)
):
    try:
        direct_review_url = data.get_review_url()
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE businesses
            SET name = ?, slug = ?, category = ?, google_review_url = ?, logo_url = ?, accent_color = ?, tags_preset = ?
            WHERE id = ?
        """, (
            data.name,
            data.slug,
            data.category,
            direct_review_url,
            data.logo_url,
            data.accent_color,
            json.dumps(data.tags_preset),
            business_id
        ))
        conn.commit()
        return {"status": "updated", "direct_review_url": direct_review_url}
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail=f"Slug '{data.slug}' is already in use by another business.")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.delete("/api/admin/businesses/{business_id}")
async def api_delete_business(
    business_id: int,
    current_user: str = Depends(require_reseller)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
    cursor.execute("DELETE FROM business_owners WHERE business_id = ?", (business_id,))
    cursor.execute("DELETE FROM analytics_events WHERE business_id = ?", (business_id,))
    cursor.execute("DELETE FROM payments WHERE business_id = ?", (business_id,))
    cursor.execute("DELETE FROM activity_logs WHERE business_id = ?", (business_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


@app.get("/api/admin/settings")
async def api_get_settings(current_user: str = Depends(require_reseller)):
    api_key = get_setting("openai_api_key", os.getenv("OPENAI_API_KEY", ""))
    rzp_key = get_setting("razorpay_key_id", os.getenv("RAZORPAY_KEY_ID", ""))
    rzp_secret = get_setting("razorpay_key_secret", os.getenv("RAZORPAY_KEY_SECRET", ""))
    google_client_id = get_setting("google_client_id", os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret = get_setting("google_client_secret", os.getenv("GOOGLE_CLIENT_SECRET", ""))
    return {
        "openai_api_key": api_key,
        "razorpay_key_id": rzp_key,
        "razorpay_key_secret": rzp_secret,
        "google_client_id": google_client_id,
        "google_client_secret": google_client_secret,
        "payments_enabled": is_payments_enabled()
    }


@app.post("/api/admin/settings")
async def api_save_settings(
    data: SettingsUpdateRequest,
    current_user: str = Depends(require_reseller)
):
    if data.openai_api_key is not None:
        set_setting("openai_api_key", data.openai_api_key.strip())
    if data.razorpay_key_id is not None:
        set_setting("razorpay_key_id", data.razorpay_key_id.strip())
    if data.razorpay_key_secret is not None:
        set_setting("razorpay_key_secret", data.razorpay_key_secret.strip())
    if data.google_client_id is not None:
        set_setting("google_client_id", data.google_client_id.strip())
    if data.google_client_secret is not None:
        set_setting("google_client_secret", data.google_client_secret.strip())
    if data.payments_enabled is not None:
        set_setting("payments_enabled", "true" if data.payments_enabled else "false")
    return {"status": "saved"}


@app.post("/api/admin/settings/test-ai")
async def api_test_ai(
    data: TestAiRequest,
    current_user: str = Depends(require_reseller)
):
    res = test_openai_connection(data.api_key.strip())
    return res


# ==========================================
# CUSTOM FRIENDLY ERROR HANDLERS (NO RAW CRASHES)
# ==========================================

@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: Any):
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header or request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Resource not found")
        return JSONResponse(status_code=404, content={"detail": detail})
    return templates.TemplateResponse(request=request, name="404.html", status_code=404)


@app.exception_handler(500)
async def server_error_exception_handler(request: Request, exc: Any):
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header or request.url.path.startswith("/api/"):
        return JSONResponse(status_code=500, content={"detail": "An internal server error occurred"})
    return templates.TemplateResponse(request=request, name="500.html", status_code=500)

