import sqlite3
import os
import json
import hashlib
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

    # Reseller Admin table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS resellers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Businesses table
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Analytics Events table
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

    # Private Feedback table (for 1-3 star reviews)
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

    # Settings table (for reseller configuration, OpenAI API key, etc.)
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
        demo_businesses = [
            (
                "artisan-roast",
                "Artisan Roast Coffee Co.",
                "Café & Bakery",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/artisan_logo.svg",
                "#78350F",
                json.dumps(["Smooth Espresso", "Fresh Pastries", "Cozy Ambience", "Friendly Baristas", "Fast Wi-Fi", "Oat Milk Latte"])
            ),
            (
                "smilecraft-dental",
                "SmileCraft Dental Studio",
                "Healthcare & Dental",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/smile_logo.svg",
                "#0284C7",
                json.dumps(["Gentle Cleaning", "Painless Experience", "Friendly Staff", "Modern Clinic", "Clear Explanations", "On-Time Appointment"])
            ),
            (
                "apex-auto",
                "Apex Precision Auto Care",
                "Automotive Repair",
                "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
                "/static/uploads/apex_logo.svg",
                "#DC2626",
                json.dumps(["Honest Pricing", "Quick Turnaround", "Thorough Inspection", "Helpful Mechanics", "Quality Parts", "Clear Estimates"])
            )
        ]

        for b in demo_businesses:
            cursor.execute("""
                INSERT INTO businesses (slug, name, category, google_review_url, logo_url, accent_color, tags_preset)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, b)

        conn.commit()

        # Seed initial realistic analytics events for demo businesses
        # e.g. Artisan Roast has 42 scans, 28 completed reviews, 3 feedback
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
                """, (bid, 2, f"Service was slightly slower during morning peak hour. Would appreciate faster takeout counter service.", "Alex Jordan", "alex.j@example.com"))

        conn.commit()

    conn.close()

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
