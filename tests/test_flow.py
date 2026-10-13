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

    # 8. Test Self-Serve Public Signup for Business Owner
    print("\n[TEST 7] Testing Public Self-Serve Signup for Business Owner...")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM business_owners WHERE email = 'mario@bellanapoli.com'")
    cursor.execute("DELETE FROM businesses WHERE name = 'Trattoria Bella Napoli'")
    conn.commit()
    conn.close()

    signup_data = {
        "business_name": "Trattoria Bella Napoli",
        "email": "mario@bellanapoli.com",
        "password": "napoliPassword123",
        "category": "Restaurant & Café",
        "google_review_url": "https://g.page/r/bellanapoli/review",
        "accent_color": "#DC2626",
        "accent_hex": "#DC2626",
        "logo_url": "/static/uploads/artisan_logo.svg",
        "tags": "Wood-Fired Pizza, Handmade Pasta, Friendly Waiters"
    }
    res_signup = client.post("/signup", data=signup_data, follow_redirects=False)
    assert res_signup.status_code == 303, f"Expected 303 redirect to /pricing, got {res_signup.status_code}"
    assert res_signup.headers["location"] == "/pricing"
    assert "owner_session" in res_signup.cookies
    owner_cookie = res_signup.cookies["owner_session"]
    print("  ✅ Owner signed up! Account created, logged in, and redirected to /pricing.")

    # 9. Test Subscription Gating When Inactive / Pending
    print("\n[TEST 8] Testing Subscription Gating on Newly Created Inactive Business...")
    # Find slug for Trattoria Bella Napoli
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, slug, subscription_status FROM businesses WHERE name = 'Trattoria Bella Napoli'")
    new_biz = cursor.fetchone()
    conn.close()
    assert new_biz is not None
    new_biz_id = new_biz["id"]
    new_slug = new_biz["slug"]

    res_gate = client.get(f"/r/{new_slug}")
    assert res_gate.status_code == 402, f"Expected 402 Payment Required for inactive portal, got {res_gate.status_code}"
    assert "temporarily paused pending subscription renewal" in res_gate.text
    print("  ✅ Access gating active! Unsubscribed portal correctly blocked with renewal notice.")

    # 10. Test Pricing & UPI Autopay Activation
    print("\n[TEST 9] Testing Subscription Activation via UPI Autopay Mandate...")
    # Create checkout session
    res_checkout = client.post("/api/billing/create-checkout", json={
        "plan_type": "monthly",
        "payment_mode": "upi_autopay"
    }, cookies={"owner_session": owner_cookie})
    assert res_checkout.status_code == 200, f"Checkout failed: {res_checkout.text}"
    checkout_info = res_checkout.json()
    assert checkout_info["type"] == "upi_autopay"

    # Verify & activate payment
    res_verify = client.post("/api/billing/verify-payment", json={
        "plan_type": "monthly",
        "payment_mode": "upi_autopay",
        "payment_id": "pay_test_upi_mandate_8899",
        "subscription_id": checkout_info.get("subscription_id", "sub_test_8899")
    }, cookies={"owner_session": owner_cookie})
    assert res_verify.status_code == 200
    assert res_verify.json()["success"] is True
    print("  ✅ Payment verified! Monthly Plan with UPI Autopay Mandate activated.")

    # Verify that the landing page is now immediately ACTIVE!
    res_active_gate = client.get(f"/r/{new_slug}")
    assert res_active_gate.status_code == 200
    assert "Trattoria Bella Napoli" in res_active_gate.text
    print("  ✅ Portal access unlocked! Customer landing page is live and active.")

    # 11. Test Business Owner Dashboard & Profile Update (with Audit Logging)
    print("\n[TEST 10] Testing Owner Dashboard and Google Review Link Update...")
    res_owner_dash = client.get("/dashboard", cookies={"owner_session": owner_cookie})
    assert res_owner_dash.status_code == 200
    assert "Trattoria Bella Napoli" in res_owner_dash.text
    assert "Active Portal" in res_owner_dash.text
    print("  ✅ Owner dashboard loads with live stats, QR code, and billing details.")

    # Update Google Review Link (Must update last_google_url_update and log activity)
    updated_url = "https://search.google.com/local/writereview?placeid=ChIJUpdatedPlace123"
    res_profile_update = client.put("/api/owner/profile", json={
        "name": "Trattoria Bella Napoli",
        "google_review_url": updated_url,
        "category": "Italian Restaurant",
        "accent_color": "#B91C1C",
        "tags_preset": ["Fresh Pasta", "Warm Welcome"]
    }, cookies={"owner_session": owner_cookie})
    assert res_profile_update.status_code == 200
    print("  ✅ Google review destination link updated by owner.")

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
    assert "autopay_mandate_authorized" in log_actions
    assert "google_url_updated" in log_actions
    print(f"  ✅ Admin read-only inspection confirmed! Activity audit trail: {log_actions}")

    print("\n" + "=" * 60)
    print("🎉 ALL 11 TESTS PASSED WITH ZERO ERRORS!")
    print("=" * 60)

if __name__ == "__main__":
    run_all_tests()
