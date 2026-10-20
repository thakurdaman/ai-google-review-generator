import os
import json
import time
import hmac
import hashlib
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from .database import get_setting, get_db, record_payment, log_activity

PLANS = {
    "monthly": {
        "id": "monthly",
        "name": "Monthly Pro",
        "price": 999.00,
        "amount_paise": 99900,
        "currency": "INR",
        "period": "monthly",
        "days": 30,
        "description": "Full access to Google Review AI generator, QR/NFC links, and review shielding"
    },
    "yearly": {
        "id": "yearly",
        "name": "Annual Growth (Save 25%)",
        "price": 8999.00,
        "amount_paise": 899900,
        "currency": "INR",
        "period": "yearly",
        "days": 365,
        "description": "Best value: 12 months for the price of 9. Unlimited reviews, priority AI speed"
    }
}

def is_payments_enabled() -> bool:
    """Returns True if payment gateway is actively required, False if temporarily disabled (free beta launch)."""
    val = get_setting("payments_enabled", os.getenv("PAYMENTS_ENABLED", "false")).strip().lower()
    return val in ["1", "true", "yes", "enabled"]

def get_razorpay_keys():
    key_id = get_setting("razorpay_key_id", os.getenv("RAZORPAY_KEY_ID", "")).strip()
    key_secret = get_setting("razorpay_key_secret", os.getenv("RAZORPAY_KEY_SECRET", "")).strip()
    return key_id, key_secret

def create_checkout_session(business_id: int, plan_type: str, payment_mode: str) -> Dict[str, Any]:
    """
    Creates either a UPI Autopay mandate subscription or a one-time payment order.
    When payment gateway is temporarily disabled, immediately activates account for free.
    """
    if plan_type not in PLANS:
        plan_type = "monthly"
    
    plan_info = PLANS[plan_type]

    # If payment gateway is temporarily disabled, grant instant free access
    if not is_payments_enabled():
        verify_and_activate_subscription(
            business_id=business_id,
            plan_type=plan_type,
            payment_mode="free_launch",
            payment_id="free_launch_access",
            order_id="",
            subscription_id=""
        )
        return {
            "mode": "free",
            "type": "free",
            "is_free": True,
            "amount": 0,
            "plan": plan_info,
            "message": "Payment gateway is currently disabled. Your account has been activated for free!"
        }

    key_id, key_secret = get_razorpay_keys()

    # Real Razorpay Gateway Call (if keys are configured)
    if key_id and key_secret:
        try:
            if payment_mode == "upi_autopay":
                # Create Subscription on Razorpay for UPI Autopay mandate
                # Create plan if needed or subscription directly
                sub_url = "https://api.razorpay.com/v1/subscriptions"
                sub_payload = {
                    "plan_id": get_setting(f"razorpay_plan_{plan_type}", "plan_demo_123"),
                    "total_count": 12 if plan_type == "monthly" else 5,
                    "quantity": 1,
                    "customer_notify": 1,
                    "notes": {"business_id": str(business_id), "mode": "upi_autopay"}
                }
                res = requests.post(sub_url, auth=(key_id, key_secret), json=sub_payload, timeout=10)
                if res.status_code in [200, 201]:
                    data = res.json()
                    return {
                        "mode": "live",
                        "type": "upi_autopay",
                        "key_id": key_id,
                        "subscription_id": data.get("id"),
                        "amount": plan_info["price"],
                        "plan": plan_info
                    }
            else:
                # One-time payment order
                order_url = "https://api.razorpay.com/v1/orders"
                order_payload = {
                    "amount": plan_info["amount_paise"],
                    "currency": "INR",
                    "receipt": f"rcpt_biz_{business_id}_{int(time.time())}",
                    "notes": {"business_id": str(business_id), "mode": "one_time", "plan": plan_type}
                }
                res = requests.post(order_url, auth=(key_id, key_secret), json=order_payload, timeout=10)
                if res.status_code in [200, 201]:
                    data = res.json()
                    return {
                        "mode": "live",
                        "type": "one_time",
                        "key_id": key_id,
                        "order_id": data.get("id"),
                        "amount": plan_info["price"],
                        "plan": plan_info
                    }
        except Exception as e:
            print(f"[Razorpay API Error] {e}. Falling back to sandbox checkout.")

    # Seamless Interactive Sandbox / Test Mode
    # Allows full testing of both UPI Autopay mandate and One-time payment immediately!
    dummy_id = f"rzp_mock_{int(time.time())}"
    return {
        "mode": "sandbox",
        "type": payment_mode,
        "key_id": key_id or "rzp_test_sandbox_mode",
        "order_id": f"order_mock_{dummy_id}",
        "subscription_id": f"sub_mock_{dummy_id}" if payment_mode == "upi_autopay" else None,
        "amount": plan_info["price"],
        "plan": plan_info,
        "is_sandbox": True
    }

def verify_and_activate_subscription(
    business_id: int,
    plan_type: str,
    payment_mode: str,
    payment_id: str,
    order_id: str = "",
    subscription_id: str = ""
) -> Dict[str, Any]:
    """
    Activates the business subscription, sets billing cycle end date,
    records payment in the database, and logs activity audit trail.
    """
    plan_info = PLANS.get(plan_type, PLANS["monthly"])
    days = plan_info["days"]

    # Record the successful payment transaction
    pay_id = record_payment(
        business_id=business_id,
        amount=plan_info["price"],
        plan_type=plan_type,
        payment_mode=payment_mode,
        razorpay_payment_id=payment_id or f"pay_mock_{int(time.time())}",
        razorpay_order_id=order_id,
        razorpay_subscription_id=subscription_id,
        status="captured",
        days=days
    )

    # Log activity in audit logs
    if payment_mode == "upi_autopay":
        log_activity(
            business_id=business_id,
            action="autopay_mandate_authorized",
            details=f"Authorized UPI Autopay mandate for {plan_info['name']} (₹{plan_info['price']:,.0f})"
        )
    else:
        log_activity(
            business_id=business_id,
            action="subscription_activated",
            details=f"Completed one-time payment for {plan_info['name']} (₹{plan_info['price']:,.0f})"
        )

    return {
        "success": True,
        "message": f"Subscription activated on {plan_info['name']} via {payment_mode.replace('_', ' ').title()}",
        "payment_id": pay_id,
        "days_valid": days
    }

def is_subscription_active(business: Dict[str, Any]) -> bool:
    """
    Checks if business has an active subscription.
    Accounts pending email confirmation are blocked until verified.
    When payment gateway is temporarily disabled, all verified businesses have active access.
    """
    if business.get("subscription_status") == "pending_verification":
        return False

    if not is_payments_enabled():
        return True

    status = business.get("subscription_status", "inactive")
    if status == "active":
        # Check cycle end date if present
        cycle_end = business.get("subscription_cycle_end")
        if cycle_end:
            try:
                # Handle ISO format or standard space format
                end_dt = datetime.strptime(cycle_end.replace("T", " "), "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() > end_dt:
                    return False
            except Exception:
                pass
        return True
    return False

def cancel_autopay_mandate(business_id: int):
    """
    Cancels recurring UPI Autopay mandate, switching business to one-time billing cycle.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE businesses
        SET payment_method = 'one_time',
            auto_renew = 0
        WHERE id = ?
    """, (business_id,))
    conn.commit()
    conn.close()

    log_activity(
        business_id=business_id,
        action="plan_changed",
        details="Owner cancelled UPI Autopay recurring mandate (retained access until end of current cycle)"
    )


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Verifies HMAC SHA-256 signature returned by Razorpay for one-time orders."""
    key_id, key_secret = get_razorpay_keys()
    if not key_secret:
        return True
    if not signature or not order_id or not payment_id:
        return False
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(key_secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
