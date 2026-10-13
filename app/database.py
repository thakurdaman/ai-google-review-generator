import sqlite3
import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

if os.environ.get("VERCEL"):
    DB_PATH = "/tmp/reviews.db"
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "reviews.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_db():
    if not os.path.exists(DB_PATH):
        init_db()
    return get_db_connection()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Reseller Admin table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS resellers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Businesses table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS businesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        category TEXT DEFAULT 'General',
        google_review_url TEXT NOT NULL,
        logo_url TEXT,
        accent_color TEXT DEFAULT '#2563EB',
        tags_preset TEXT DEFAULT '[]',
        active INTEGER DEFAULT 1,
        owner_id INTEGER,
        subscription_status TEXT DEFAULT 'active', -- 'active', 'trialing', 'past_due', 'inactive', 'cancelled'
        plan_tier TEXT DEFAULT 'monthly', -- 'monthly', 'yearly'
        payment_method TEXT DEFAULT 'upi_autopay', -- 'upi_autopay', 'one_time'
        subscription_cycle_end TIMESTAMP,
        last_google_url_update TIMESTAMP,
        razorpay_customer_id TEXT,
        razorpay_subscription_id TEXT,
        auto_renew INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Migration check for existing databases (adds columns if missing)
    cursor.execute("PRAGMA table_info(businesses)")
    existing_cols = [col["name"] for col in cursor.fetchall()]
    migration_cols = [
        ("owner_id", "INTEGER"),
        ("subscription_status", "TEXT DEFAULT 'active'"),
        ("plan_tier", "TEXT DEFAULT 'monthly'"),
        ("payment_method", "TEXT DEFAULT 'upi_autopay'"),
        ("subscription_cycle_end", "TIMESTAMP"),
        ("last_google_url_update", "TIMESTAMP"),
        ("razorpay_customer_id", "TEXT"),
        ("razorpay_subscription_id", "TEXT"),
        ("auto_renew", "INTEGER DEFAULT 1"),
    ]
    for col_name, col_def in migration_cols:
        if col_name not in existing_cols:
            try:
                cursor.execute(f"ALTER TABLE businesses ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass

    # 3. Business Owners table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS business_owners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        business_id INTEGER UNIQUE,
        last_login_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses (id) ON DELETE SET NULL
    );
    """)

    # 4. Analytics Events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        event_type TEXT NOT NULL, -- 'scan', 'star_selected', 'ai_generated', 'completed_review', 'feedback_submitted'
        rating INTEGER,
        user_agent TEXT,
        ip_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses (id) ON DELETE CASCADE
    );
    """)

    # 5. Private Feedback table (for 1-3 star reviews)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS private_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        feedback_text TEXT NOT NULL,
        customer_name TEXT,
        customer_contact TEXT,
        status TEXT DEFAULT 'unread', -- 'unread', 'contacted', 'resolved'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses (id) ON DELETE CASCADE
    );
    """)

    # 6. Payments & Invoices table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        currency TEXT DEFAULT 'INR',
        plan_type TEXT NOT NULL, -- 'monthly', 'yearly'
        payment_mode TEXT NOT NULL, -- 'upi_autopay', 'one_time'
        razorpay_payment_id TEXT,
        razorpay_order_id TEXT,
        razorpay_subscription_id TEXT,
        status TEXT NOT NULL, -- 'captured', 'authorized', 'failed', 'refunded'
        billing_start TIMESTAMP,
        billing_end TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses (id) ON DELETE CASCADE
    );
    """)

    # 7. Activity Audit Logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        action TEXT NOT NULL, -- 'signup', 'login', 'google_url_updated', 'profile_updated', 'subscription_activated', 'plan_changed', 'autopay_mandate_authorized'
        details TEXT,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses (id) ON DELETE CASCADE
    );
    """)

    # 8. Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    conn.commit()

    # Seed default reseller admin if none exists
    cursor.execute("SELECT COUNT(*) as cnt FROM resellers")
    if cursor.fetchone()["cnt"] == 0:
        cursor.execute(
            "INSERT INTO resellers (username, password_hash, email) VALUES (?, ?, ?)",
            ("admin", hash_password("admin123"), "admin@reviewbooster.local")
        )
        conn.commit()

    # Seed demo businesses if empty
    cursor.execute("SELECT COUNT(*) as cnt FROM businesses")
    if cursor.fetchone()["cnt"] == 0:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        month_later = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        year_later = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

        demo_businesses = [
            (
                "artisan-roast",
                "Artisan Roast Coffee Co.",
                "Café & Bakery",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/artisan_logo.svg",
                "#78350F",
                json.dumps(["Smooth Espresso", "Fresh Pastries", "Cozy Ambience", "Friendly Baristas", "Fast Wi-Fi", "Oat Milk Latte"]),
                "active",
                "monthly",
                "upi_autopay",
                month_later,
                now_str
            ),
            (
                "smilecraft-dental",
                "SmileCraft Dental Studio",
                "Healthcare & Dental",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/smile_logo.svg",
                "#0284C7",
                json.dumps(["Gentle Cleaning", "Painless Experience", "Friendly Staff", "Modern Clinic", "Clear Explanations", "On-Time Appointment"]),
                "active",
                "yearly",
                "upi_autopay",
                year_later,
                now_str
            ),
            (
                "apex-auto",
                "Apex Precision Auto Care",
                "Automotive Repair",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/apex_logo.svg",
                "#DC2626",
                json.dumps(["Honest Pricing", "Quick Turnaround", "Thorough Inspection", "Helpful Mechanics", "Quality Parts", "Clear Estimates"]),
                "active",
                "monthly",
                "one_time",
                month_later,
                now_str
            )
        ]

        for b in demo_businesses:
            cursor.execute("""
                INSERT INTO businesses (slug, name, category, google_review_url, logo_url, accent_color, tags_preset, subscription_status, plan_tier, payment_method, subscription_cycle_end, last_google_url_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, b)

        conn.commit()

        # Create demo business owner for Artisan Roast Coffee Co.
        cursor.execute("SELECT id FROM businesses WHERE slug = 'artisan-roast'")
        artisan_row = cursor.fetchone()
        if artisan_row:
            artisan_id = artisan_row["id"]
            cursor.execute("""
                INSERT INTO business_owners (email, password_hash, full_name, business_id, last_login_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("owner@artisancafe.com", hash_password("owner123"), "Marco Rossi", artisan_id, now_str))
            owner_id = cursor.lastrowid
            cursor.execute("UPDATE businesses SET owner_id = ? WHERE id = ?", (owner_id, artisan_id))

            # Log initial owner activity
            cursor.execute("""
                INSERT INTO activity_logs (business_id, action, details)
                VALUES (?, 'signup', 'Owner registered business account via self-serve onboarding')
            """, (artisan_id,))
            cursor.execute("""
                INSERT INTO activity_logs (business_id, action, details)
                VALUES (?, 'autopay_mandate_authorized', 'Authorized UPI Autopay mandate for Monthly Plan (₹999/mo)')
            """, (artisan_id,))
            cursor.execute("""
                INSERT INTO activity_logs (business_id, action, details)
                VALUES (?, 'google_url_updated', 'Set initial Google review place link')
            """, (artisan_id,))

            # Seed demo payment transaction
            cursor.execute("""
                INSERT INTO payments (business_id, amount, currency, plan_type, payment_mode, razorpay_payment_id, razorpay_subscription_id, status, billing_start, billing_end)
                VALUES (?, 999.00, 'INR', 'monthly', 'upi_autopay', 'pay_mock_upi_98231', 'sub_mock_mandate_4412', 'captured', ?, ?)
            """, (artisan_id, now_str, month_later))

        # Seed initial realistic analytics events for demo businesses
        cursor.execute("SELECT id, slug FROM businesses")
        bus_rows = cursor.fetchall()
        for row in bus_rows:
            bid = row["id"]
            slug = row["slug"]
            if slug == "artisan-roast":
                scans, completions, fbacks = 48, 33, 2
            elif slug == "smilecraft-dental":
                scans, completions, fbacks = 29, 19, 1
            else:
                scans, completions, fbacks = 18, 11, 2

            for _ in range(scans):
                cursor.execute("INSERT INTO analytics_events (business_id, event_type) VALUES (?, 'scan')", (bid,))
            for _ in range(completions):
                cursor.execute("INSERT INTO analytics_events (business_id, event_type, rating) VALUES (?, 'completed_review', 5)", (bid,))
            for i in range(fbacks):
                cursor.execute("INSERT INTO analytics_events (business_id, event_type, rating) VALUES (?, 'feedback_submitted', 2)", (bid,))
                cursor.execute("""
                    INSERT INTO private_feedback (business_id, rating, feedback_text, customer_name, customer_contact)
                    VALUES (?, ?, ?, ?, ?)
                """, (bid, 2, "Service was slightly slower during morning peak hour. Would appreciate faster takeout counter service.", "Alex Jordan", "alex.j@example.com"))

        conn.commit()

    conn.close()

# Helper Functions
def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def log_activity(business_id: int, action: str, details: str = "", ip_address: str = ""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO activity_logs (business_id, action, details, ip_address)
        VALUES (?, ?, ?, ?)
    """, (business_id, action, details, ip_address))
    conn.commit()
    conn.close()

def record_payment(
    business_id: int,
    amount: float,
    plan_type: str,
    payment_mode: str,
    razorpay_payment_id: str = "",
    razorpay_order_id: str = "",
    razorpay_subscription_id: str = "",
    status: str = "captured",
    days: int = 30
) -> int:
    conn = get_db()
    cursor = conn.cursor()
    now = datetime.utcnow()
    cycle_end = now + timedelta(days=days)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    end_str = cycle_end.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO payments (business_id, amount, currency, plan_type, payment_mode, razorpay_payment_id, razorpay_order_id, razorpay_subscription_id, status, billing_start, billing_end)
        VALUES (?, ?, 'INR', ?, ?, ?, ?, ?, ?, ?, ?)
    """, (business_id, amount, plan_type, payment_mode, razorpay_payment_id, razorpay_order_id, razorpay_subscription_id, status, now_str, end_str))
    payment_id = cursor.lastrowid

    # Update business subscription status & cycle end
    cursor.execute("""
        UPDATE businesses
        SET subscription_status = 'active',
            plan_tier = ?,
            payment_method = ?,
            subscription_cycle_end = ?,
            active = 1
        WHERE id = ?
    """, (plan_type, payment_mode, end_str, business_id))

    conn.commit()
    conn.close()
    return payment_id

def get_business_activity_logs(business_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM activity_logs
        WHERE business_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (business_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_business_payments(business_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM payments
        WHERE business_id = ?
        ORDER BY created_at DESC
    """, (business_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_platform_payments(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.*, b.name as business_name, b.slug as business_slug
        FROM payments p
        JOIN businesses b ON p.business_id = b.id
        ORDER BY p.created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_business_metrics(business_id: int) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            (SELECT COUNT(*) FROM analytics_events WHERE business_id = ? AND event_type = 'scan') as scans_count,
            (SELECT COUNT(*) FROM analytics_events WHERE business_id = ? AND event_type = 'completed_review') as reviews_count,
            (SELECT COUNT(*) FROM private_feedback WHERE business_id = ?) as feedback_count
    """, (business_id, business_id, business_id))
    row = cursor.fetchone()
    conn.close()
    scans = row["scans_count"] if row else 0
    reviews = row["reviews_count"] if row else 0
    feedback = row["feedback_count"] if row else 0
    conv = round((reviews / scans * 100), 1) if scans > 0 else 0.0
    return {
        "scans_count": scans,
        "reviews_count": reviews,
        "feedback_count": feedback,
        "conversion_rate": conv
    }
