import os
import sys

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add root directory to sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from starlette.testclient import TestClient
from app.main import app
from app.database import init_db, get_db

def run_all_tests():
    print("=" * 60)
    print("🧪 RUNNING COMPREHENSIVE TEST SUITE FOR GOOGLE REVIEW SAAS")
    print("=" * 60)

    # 1. Initialize DB
    init_db()
    client = TestClient(app)

    # 2. Test Customer Landing Page
    print("\n[TEST 1] Testing Customer Landing Page for 'artisan-roast'...")
    res = client.get("/r/artisan-roast")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "Artisan Roast Coffee Co." in res.text
    assert "Google Verified" in res.text
    print("  ✅ Customer landing page renders with business branding.")

    # 3. Test AI Review Generation with 5 Stars (Positive Review Flow)
    print("\n[TEST 2] Testing 5-Star AI Review Generation...")
    payload = {
        "business_id": 1,
        "rating": 5,
        "selected_tags": ["Smooth Espresso", "Cozy Ambience"],
        "custom_note": "Best cappuccino in town!"
    }
    res = client.post("/api/reviews/generate", json=payload)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert "drafts" in data, "No drafts found in response"
    assert len(data["drafts"]) == 3, f"Expected 3 drafts, got {len(data['drafts'])}"
    for idx, d in enumerate(data["drafts"]):
        print(f"  Option {idx+1} ({d['tone']}): {d['text']}")
    print(f"  Engine used: {data.get('engine')}")
    print("  ✅ 3 natural review drafts generated successfully.")

    # 4. Test Guardrail: 1-3 Stars AI Generation MUST Be Rejected
    print("\n[TEST 3] Testing AI Guardrail (1-3 stars MUST NOT call AI)...")
    payload_bad = {
        "business_id": 1,
        "rating": 2,
        "selected_tags": ["Slow Service"]
    }
    res_bad = client.post("/api/reviews/generate", json=payload_bad)
    assert res_bad.status_code == 400, f"Expected 400 rejection, got {res_bad.status_code}"
    print(f"  ✅ Guardrail confirmed! Rejected with message: {res_bad.json()['detail']}")

    # 5. Test Private Feedback Submission (1-3 Stars Flow)
    print("\n[TEST 4] Testing Private Feedback Submission...")
    feedback_payload = {
        "business_id": 1,
        "rating": 2,
        "feedback_text": "The espresso took over 15 minutes during the afternoon rush.",
        "customer_name": "Taylor Swift",
        "customer_contact": "taylor@example.com"
    }
    res_fb = client.post("/api/feedback/submit", json=feedback_payload)
    assert res_fb.status_code == 200, f"Expected 200, got {res_fb.status_code}"
    print("  ✅ Private feedback successfully recorded in database without redirecting to Google.")

    # 6. Test Analytics Event Tracking
    print("\n[TEST 5] Testing Analytics Event Tracking...")
    res_event = client.post("/api/analytics/event", json={
        "business_id": 1,
        "event_type": "completed_review",
        "rating": 5
    })
    assert res_event.status_code == 200
    print("  ✅ Analytics completion event recorded.")

    # 7. Test QR Code Generation
    print("\n[TEST 6] Testing Dynamic QR Code Image Generation...")
    res_qr = client.get("/api/qr/artisan-roast.png")
    assert res_qr.status_code == 200
    assert res_qr.headers["content-type"] == "image/png"
    assert len(res_qr.content) > 100
    print(f"  ✅ QR Code PNG generated ({len(res_qr.content)} bytes).")

    # 8. Test Admin Login and Reseller Session Authentication
    print("\n[TEST 7] Testing Reseller Admin Login...")
    res_login = client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False
    )
    assert res_login.status_code == 303, f"Expected 303 redirect, got {res_login.status_code}"
    assert "reseller_session" in res_login.cookies
    session_cookie = res_login.cookies["reseller_session"]
    print("  ✅ Admin credentials validated; reseller session cookie issued.")

    # 9. Test Access to Admin Dashboard with Cookie
    print("\n[TEST 8] Testing Admin Dashboard with Session...")
    res_dash = client.get("/admin", cookies={"reseller_session": session_cookie})
    assert res_dash.status_code == 200, f"Expected 200, got {res_dash.status_code}"
    assert "ReviewBoost" in res_dash.text
    assert "Reseller Hub" in res_dash.text
    assert "Artisan Roast Coffee Co." in res_dash.text
    print("  ✅ Admin Dashboard loaded with live metrics and client businesses.")

    # 10. Test Admin Client Creation
    print("\n[TEST 9] Testing Adding a New Client Business via Admin API...")
    new_biz = {
        "name": "Dr. Miller Orthodontics",
        "slug": "miller-ortho",
        "category": "Healthcare & Dental",
        "google_review_url": "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
        "logo_url": "/static/uploads/smile_logo.svg",
        "accent_color": "#0284C7",
        "tags_preset": ["Great Braces", "Super Gentle", "Friendly Orthodontist", "Zero Wait Time"]
    }
    res_create = client.post("/api/admin/businesses", json=new_biz, cookies={"reseller_session": session_cookie})
    assert res_create.status_code == 200, f"Expected 200, got {res_create.status_code}"
    new_id = res_create.json()["id"]
    print(f"  ✅ New business created with ID: {new_id}")

    # Verify new business landing page immediately works
    res_new_landing = client.get("/r/miller-ortho")
    assert res_new_landing.status_code == 200
    assert "Dr. Miller Orthodontics" in res_new_landing.text
    print("  ✅ Newly created business landing page verified active.")

    # 11. Test Printable Stand Flyer
    print("\n[TEST 10] Testing Printable Flyer / Table Tent Generation...")
    res_flyer = client.get(f"/admin/business/{new_id}/flyer", cookies={"reseller_session": session_cookie})
    assert res_flyer.status_code == 200
    assert "Dr. Miller Orthodontics" in res_flyer.text
    assert "Scan Camera" in res_flyer.text
    print("  ✅ Printable counter stand card rendered.")

    print("\n" + "=" * 60)
    print("🎉 ALL 10 TESTS PASSED WITH ZERO ERRORS!")
    print("=" * 60)

if __name__ == "__main__":
    run_all_tests()
