import os
import json
import re
import uuid
import requests
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .database import (
    init_db, get_db, get_setting, set_setting, hash_password,
    log_activity, get_business_activity_logs, get_business_payments,
    get_all_platform_payments, get_business_metrics
)
from .auth import (
    authenticate_reseller, create_session_token, get_current_reseller, require_reseller,
    authenticate_owner, create_owner_token, get_current_owner, require_owner,
    get_owner_by_email, create_oauth_state_token, verify_oauth_state_token,
    create_google_signup_token, verify_google_signup_token
)
from .ai_service import (
    generate_review_drafts, test_openai_connection
)
from .qr_service import (
    generate_qr_png_bytes, generate_qr_svg_string
)
from .billing_service import (
    PLANS, create_checkout_session, verify_and_activate_subscription,
    is_subscription_active, cancel_autopay_mandate, is_payments_enabled
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
    clean_and_verify_email
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Google Review Collection & Subscription SaaS Platform")

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Templates
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

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

    # Validate email legitimacy
    is_valid_email, email_err = validate_email_address(email_clean)
    if not is_valid_email:
        return templates.TemplateResponse(
            request=request,
            name="signup.html",
            context={
                "error": email_err or f"The email address '{email_clean}' is not valid.",
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

    # Handle logo file upload
    logo_url = None
    if logo_file and logo_file.filename:
        import base64
        contents = await logo_file.read()
        if len(contents) > 2 * 1024 * 1024:  # 2MB limit
            return templates.TemplateResponse(
                request=request,
                name="signup.html",
                context={"error": "Logo file is too large. Please use an image under 2MB."},
                status_code=400
            )
        # Save to static/uploads for local use
        uploads_dir = os.path.join(BASE_DIR, "static", "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        ext = os.path.splitext(logo_file.filename)[1] or ".png"
        safe_name = f"{uuid.uuid4().hex[:12]}{ext}"
        file_path = os.path.join(uploads_dir, safe_name)
        with open(file_path, "wb") as fp:
            fp.write(contents)
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

    # 1. Create Business Record
    slug = generate_slug(bname_clean)
    brand_color = accent_hex or accent_color or "#2563EB"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else ["Friendly Staff", "Quick Service"]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

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
    log_activity(business_id, "signup", f"Owner {email_clean} self-registered business '{bname_clean}'")
    log_activity(business_id, "google_url_updated", f"Configured Google Business Profile direct review link for Place ID {place_id}: {direct_review_url}")

    # 4. Set session cookie and redirect (to /dashboard if free launch, else /pricing)
    token = create_owner_token(owner_id, email_clean, business_id)
    target_url = "/dashboard" if not payments_on else "/pricing"
    resp = RedirectResponse(url=target_url, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        key="owner_session",
        value=token,
        httponly=True,
        max_age=86400 * 14,
        samesite="lax"
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

    owner = authenticate_owner(email_clean, password)
    if not owner:
        return templates.TemplateResponse(
            request=request,
            name="owner_login.html",
            context={"error": "Invalid email or password"},
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

    token = create_owner_token(owner["id"], owner["email"], owner["business_id"])
    target_url = "/dashboard"
    if is_payments_enabled() and owner.get("subscription_status") not in ["active", "trialing"]:
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

@app.get("/logout")
async def owner_logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie("owner_session")
    return resp


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

    # Handle logo upload
    logo_url = None
    if logo_file and logo_file.filename:
        contents = await logo_file.read()
        if len(contents) <= 2 * 1024 * 1024:
            uploads_dir = os.path.join(BASE_DIR, "static", "uploads")
            os.makedirs(uploads_dir, exist_ok=True)
            ext = os.path.splitext(logo_file.filename)[1] or ".png"
            safe_name = f"{uuid.uuid4().hex[:12]}{ext}"
            file_path = os.path.join(uploads_dir, safe_name)
            with open(file_path, "wb") as fp:
                fp.write(contents)
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
    """
    try:
        body = await request.json()
        event = body.get("event", "")
        # Process recurring charge or cancellation if sent by gateway
        return {"status": "ok", "event_received": event}
    except Exception:
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

    # Retrieve private feedbacks for this business
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM private_feedback
        WHERE business_id = ?
        ORDER BY created_at DESC
    """, (business_id,))
    feedbacks = [dict(r) for r in cursor.fetchall()]

    # Retrieve payments history
    payments = get_business_payments(business_id)
    conn.close()

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
            "feedbacks": feedbacks,
            "payments": payments,
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
async def api_generate_reviews(req: ReviewGenerateRequest):
    """
    Generates 3 natural review drafts using OpenAI GPT-4o mini or smart fallback.
    Crucial guardrail: Strictly restricted to positive ratings (4 or 5 stars).
    """
    if req.rating < 4:
        raise HTTPException(
            status_code=400,
            detail="AI review drafts are strictly disabled for 1-3 star ratings to prevent false positives. Please use private feedback."
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


@app.post("/api/feedback/submit")
async def api_submit_feedback(req: FeedbackSubmitRequest):
    """
    Stores unhappy customer feedback (1-3 stars) privately so management can address it directly.
    """
    if not req.feedback_text.strip():
        raise HTTPException(status_code=400, detail="Feedback text is required")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO private_feedback (business_id, rating, feedback_text, customer_name, customer_contact)
        VALUES (?, ?, ?, ?, ?)
    """, (req.business_id, req.rating, req.feedback_text.strip(), req.customer_name, req.customer_contact))
    
    # Also log analytics event
    cursor.execute("""
        INSERT INTO analytics_events (business_id, event_type, rating)
        VALUES (?, 'feedback_submitted', ?)
    """, (req.business_id, req.rating))

    conn.commit()
    conn.close()

    return {"status": "success", "message": "Feedback submitted directly to management"}


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
            (SELECT COUNT(*) FROM analytics_events WHERE business_id = b.id AND event_type = 'completed_review') as reviews_count,
            (SELECT COUNT(*) FROM private_feedback WHERE business_id = b.id) as feedback_count
        FROM businesses b
        ORDER BY b.created_at DESC
    """)
    raw_businesses = cursor.fetchall()

    businesses = []
    total_scans = 0
    total_completed = 0
    total_feedback = 0
    active_subscriptions = 0
    platform_mrr = 0.0

    for row in raw_businesses:
        b_dict = dict(row)
        scans = b_dict.get("scans_count", 0)
        reviews = b_dict.get("reviews_count", 0)
        fbacks = b_dict.get("feedback_count", 0)
        
        total_scans += scans
        total_completed += reviews
        total_feedback += fbacks

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
        "total_feedback": total_feedback,
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

    # 3. Fetch private feedback list
    cursor.execute("""
        SELECT pf.*, b.name as business_name, b.slug as business_slug
        FROM private_feedback pf
        JOIN businesses b ON pf.business_id = b.id
        ORDER BY pf.created_at DESC
    """)
    feedbacks = [dict(row) for row in cursor.fetchall()]

    # 4. Fetch platform payments history
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
            "feedbacks": feedbacks,
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
    if authenticate_reseller(username.strip(), password):
        token = create_session_token(username.strip())
        resp = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(
            key="reseller_session",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax"
        )
        return resp
    
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
    cursor.execute("DELETE FROM private_feedback WHERE business_id = ?", (business_id,))
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
