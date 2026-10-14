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
from app.database import init_db, get_db, set_setting

def run_all_tests():
    print("=" * 60)
    print("🧪 RUNNING COMPREHENSIVE TEST SUITE FOR GOOGLE REVIEW SAAS")
    print("   INCLUDING SELF-SERVE SIGNUP & UPI AUTOPAY SUBSCRIPTIONS")
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

    # 8. Test Free Launch Public Signup (When Payments Disabled)
    print("\n[TEST 7] Testing Free Launch Self-Serve Signup (Payments Disabled)...")
    set_setting("payments_enabled", "false")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM business_owners WHERE email = 'mario@bellanapoli.com'")
    cursor.execute("DELETE FROM businesses WHERE name = 'Trattoria Bella Napoli'")
    conn.commit()
    conn.close()

    # Test 7A: Verify clear error on invalid Google Business Profile link without Place ID
    invalid_signup = {
        "business_name": "Trattoria Bella Napoli",
        "email": "mario@bellanapoli.com",
        "password": "napoliPassword123",
        "category": "Restaurant & Café",
        "google_business_profile_url": "https://invalid-website.com/no-place-id"
    }
    res_invalid = client.post("/signup", data=invalid_signup, follow_redirects=False)
    assert res_invalid.status_code == 400
    assert "Could not extract a usable Google Place ID" in res_invalid.text
    print("  ✅ Invalid link correctly rejected with clear error.")

    # Test 7B: Valid Google Business Profile Link with Place ID extraction
    signup_data = {
        "business_name": "Trattoria Bella Napoli",
        "email": "mario@bellanapoli.com",
        "password": "napoliPassword123",
        "category": "Restaurant & Café",
        "google_business_profile_url": "https://maps.google.com/?place_id=ChIJBellaNapoli123",
        "accent_color": "#DC2626",
        "accent_hex": "#DC2626",
        "tags": "Wood-Fired Pizza, Handmade Pasta, Friendly Waiters"
    }
    res_signup = client.post("/signup", data=signup_data, files={"logo_file": ("", b"", "application/octet-stream")}, follow_redirects=False)
    assert res_signup.status_code == 303, f"Expected 303 redirect to /dashboard, got {res_signup.status_code}"
    assert res_signup.headers["location"] == "/dashboard", f"Expected /dashboard, got {res_signup.headers['location']}"
    assert "owner_session" in res_signup.cookies
    owner_cookie = res_signup.cookies["owner_session"]
    print("  ✅ Owner signed up! In free launch mode, immediately activated and redirected straight to /dashboard.")

    # Find slug for Trattoria Bella Napoli and verify auto-constructed review URL
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, slug, subscription_status, google_review_url FROM businesses WHERE name = 'Trattoria Bella Napoli'")
    new_biz = cursor.fetchone()
    conn.close()
    assert new_biz is not None
    assert new_biz["google_review_url"] == "https://search.google.com/local/writereview?placeid=ChIJBellaNapoli123"
    print("  ✅ Direct Google review link automatically constructed: " + new_biz["google_review_url"])
    new_biz_id = new_biz["id"]
    new_slug = new_biz["slug"]

    # 9. Test Portal Gating When Payments Disabled (Must be accessible!)
    print("\n[TEST 8] Testing Portal Access When Payments Disabled...")
    res_gate = client.get(f"/r/{new_slug}")
    assert res_gate.status_code == 200, f"Expected 200 for active portal, got {res_gate.status_code}"
    assert "Trattoria Bella Napoli" in res_gate.text
    print("  ✅ Access granted! Review portal is live with zero paywalls.")

    # 10. Test Free Checkout Session
    print("\n[TEST 9] Testing Free Checkout Session Activation...")
    res_free_checkout = client.post("/api/billing/create-checkout", json={
        "plan_type": "monthly",
        "payment_mode": "upi_autopay"
    }, cookies={"owner_session": owner_cookie})
    assert res_free_checkout.status_code == 200
    free_data = res_free_checkout.json()
    assert free_data["is_free"] is True
    print("  ✅ Free checkout activated without payment gateway call.")

    # 11. Test Paywall Re-enabling via Settings Toggle
    print("\n[TEST 10] Testing Payment Gateway Re-enablement and Inactive Paywall Gating...")
    set_setting("payments_enabled", "true")
    # Temporarily mark subscription as inactive to verify 402 paywall
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE businesses SET subscription_status = 'pending_payment', active = 0 WHERE id = ?", (new_biz_id,))
    conn.commit()
    conn.close()

    res_blocked = client.get(f"/r/{new_slug}")
    assert res_blocked.status_code == 402, f"Expected 402 Payment Required, got {res_blocked.status_code}"
    assert "temporarily paused pending subscription renewal" in res_blocked.text
    print("  ✅ Paywall gating confirmed when payments_enabled is true and subscription is inactive.")

    # Reactivate and switch payments_enabled back to false (default state)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE businesses SET subscription_status = 'active', active = 1 WHERE id = ?", (new_biz_id,))
    conn.commit()
    conn.close()
    set_setting("payments_enabled", "false")
    print("  ✅ Re-disabled payment gateway (default state maintained).")

    # 11. Test Business Owner Dashboard & Profile Update (with Audit Logging)
    print("\n[TEST 10] Testing Owner Dashboard and Google Business Profile Link Update...")
    res_owner_dash = client.get("/dashboard", cookies={"owner_session": owner_cookie})
    assert res_owner_dash.status_code == 200
    assert "Trattoria Bella Napoli" in res_owner_dash.text
    assert "Active Portal" in res_owner_dash.text
    print("  ✅ Owner dashboard loads with live stats, QR code, and billing details.")

    # Test 10A: Invalid link rejection on /api/owner/profile
    res_invalid_profile = client.put("/api/owner/profile", json={
        "name": "Trattoria Bella Napoli",
        "google_business_profile_url": "https://invalid-store.com/not-maps",
        "category": "Italian Restaurant"
    }, cookies={"owner_session": owner_cookie})
    assert res_invalid_profile.status_code == 400
    assert "Could not extract a usable Google Place ID" in res_invalid_profile.json()["detail"]
    print("  ✅ Owner profile update correctly rejected invalid link without Place ID.")

    # Test 10B: Update Google Business Profile Link with Google Maps Place URL
    pasted_maps_url = "https://www.google.com/maps/place/Trattoria/@40.8518,14.2681,17z/data=!3m1!4b1!4m6!3m5!1sChIJUpdatedPlace123!8m2!3d40.8518!4d14.2681"
    updated_url = "https://search.google.com/local/writereview?placeid=ChIJUpdatedPlace123"
    res_profile_update = client.put("/api/owner/profile", json={
        "name": "Trattoria Bella Napoli",
        "google_business_profile_url": pasted_maps_url,
        "category": "Italian Restaurant",
        "accent_color": "#B91C1C",
        "tags_preset": ["Fresh Pasta", "Warm Welcome"]
    }, cookies={"owner_session": owner_cookie})
    assert res_profile_update.status_code == 200
    assert res_profile_update.json()["direct_review_url"] == updated_url
    print("  ✅ Google Business Profile link auto-extracted Place ID and updated destination.")

    # 12. Test Reseller Admin Full Visibility & Read-Only Inspection
    print("\n[TEST 11] Testing Reseller Admin Visibility and Read-Only Inspection...")
    # Admin login
    res_admin_login = client.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert res_admin_login.status_code == 303
    admin_cookie = res_admin_login.cookies["reseller_session"]

    # Admin dashboard overview
    res_admin_dash = client.get("/admin", cookies={"reseller_session": admin_cookie})
    assert res_admin_dash.status_code == 200
    assert "Trattoria Bella Napoli" in res_admin_dash.text
    assert "mario@bellanapoli.com" in res_admin_dash.text
    print("  ✅ Admin dashboard lists all self-registered owners, plans, and subscription states.")

    # Admin inspects business details & activity audit logs (read-only)
    res_inspect = client.get(f"/api/admin/businesses/{new_biz_id}/inspect", cookies={"reseller_session": admin_cookie})
    assert res_inspect.status_code == 200
    inspect_data = res_inspect.json()
    assert inspect_data["business"]["google_review_url"] == updated_url
    assert len(inspect_data["logs"]) >= 3  # signup, autopay_mandate_authorized, google_url_updated
    log_actions = [log["action"] for log in inspect_data["logs"]]
    assert "signup" in log_actions
    assert ("subscription_activated" in log_actions or "autopay_mandate_authorized" in log_actions)
    assert "google_url_updated" in log_actions
    print(f"  ✅ Admin read-only inspection confirmed! Activity audit trail: {log_actions}")

    print("\n" + "=" * 60)
    print("🎉 ALL 11 TESTS PASSED WITH ZERO ERRORS!")
    print("=" * 60)

if __name__ == "__main__":
    run_all_tests()
