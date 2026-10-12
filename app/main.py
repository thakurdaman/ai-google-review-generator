import os
import json
from typing import Optional, List
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from .database import (
    init_db, get_db, get_setting, set_setting, hash_password
)
from .auth import (
    authenticate_reseller, create_session_token, get_current_reseller, require_reseller
)
from .ai_service import (
    generate_review_drafts, test_openai_connection
)
from .qr_service import (
    generate_qr_png_bytes, generate_qr_svg_string
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Google Review Collection Platform")

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
    google_review_url: str
    logo_url: Optional[str] = None
    accent_color: str = "#2563EB"
    tags_preset: List[str] = []

class BusinessUpdateRequest(BaseModel):
    name: str
    slug: str
    category: str
    google_review_url: str
    logo_url: Optional[str] = None
    accent_color: str = "#2563EB"
    tags_preset: List[str] = []

class SettingsUpdateRequest(BaseModel):
    openai_api_key: str

class TestAiRequest(BaseModel):
    api_key: str


# ==========================================
# CUSTOMER FACING ROUTES (Mobile First / NFC / QR)
# ==========================================

@app.get("/r/{slug}", response_class=HTMLResponse)
async def customer_review_landing(slug: str, request: Request):
    """
    Landing page accessed when customer scans QR code or taps NFC card.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM businesses WHERE slug = ? AND active = 1", (slug,))
    business = cursor.fetchone()
    conn.close()

    if not business:
        return HTMLResponse(
            content="""
            <div style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Business Not Found</h2>
                <p>The review link you tapped is inactive or invalid.</p>
                <a href="/admin">Return to Admin</a>
            </div>
            """,
            status_code=404
        )

    # Convert row to dict
    bus_dict = dict(business)
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
    # Construct absolute landing URL
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
# ADMIN & RESELLER PORTAL
# ==========================================

@app.get("/")
async def root():
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_current_reseller(request)
    if not user:
        return RedirectResponse(url="/admin/login")

    conn = get_db()
    cursor = conn.cursor()

    # Get businesses with individual metrics
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

    for row in raw_businesses:
        b_dict = dict(row)
        scans = b_dict.get("scans_count", 0)
        reviews = b_dict.get("reviews_count", 0)
        fbacks = b_dict.get("feedback_count", 0)
        
        total_scans += scans
        total_completed += reviews
        total_feedback += fbacks

        businesses.append(b_dict)

    total_businesses = len(businesses)
    conversion_rate = round((total_completed / total_scans * 100), 1) if total_scans > 0 else 0.0

    stats = {
        "total_businesses": total_businesses,
        "total_scans": total_scans,
        "total_completed": total_completed,
        "conversion_rate": conversion_rate,
        "total_feedback": total_feedback
    }

    # Fetch private feedback list with business names
    cursor.execute("""
        SELECT pf.*, b.name as business_name, b.slug as business_slug
        FROM private_feedback pf
        JOIN businesses b ON pf.business_id = b.id
        ORDER BY pf.created_at DESC
    """)
    feedbacks = [dict(row) for row in cursor.fetchall()]

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "user": user,
            "businesses": businesses,
            "stats": stats,
            "feedbacks": feedbacks
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
    """
    Renders a high-resolution printable table tent / counter stand flyer for a business.
    """
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
# RESELLER ADMIN REST APIs
# ==========================================

@app.post("/api/admin/businesses")
async def api_create_business(
    data: BusinessCreateRequest,
    current_user: str = Depends(require_reseller)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO businesses (name, slug, category, google_review_url, logo_url, accent_color, tags_preset)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data.name,
            data.slug,
            data.category,
            data.google_review_url,
            data.logo_url,
            data.accent_color,
            json.dumps(data.tags_preset)
        ))
        conn.commit()
        new_id = cursor.lastrowid
        return {"status": "created", "id": new_id}
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
            data.google_review_url,
            data.logo_url,
            data.accent_color,
            json.dumps(data.tags_preset),
            business_id
        ))
        conn.commit()
        return {"status": "updated"}
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
    cursor.execute("DELETE FROM analytics_events WHERE business_id = ?", (business_id,))
    cursor.execute("DELETE FROM private_feedback WHERE business_id = ?", (business_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


@app.get("/api/admin/settings")
async def api_get_settings(current_user: str = Depends(require_reseller)):
    api_key = get_setting("openai_api_key", os.getenv("OPENAI_API_KEY", ""))
    return {"openai_api_key": api_key}


@app.post("/api/admin/settings")
async def api_save_settings(
    data: SettingsUpdateRequest,
    current_user: str = Depends(require_reseller)
):
    set_setting("openai_api_key", data.openai_api_key.strip())
    return {"status": "saved"}


@app.post("/api/admin/settings/test-ai")
async def api_test_ai(
    data: TestAiRequest,
    current_user: str = Depends(require_reseller)
):
    res = test_openai_connection(data.api_key.strip())
    return res
