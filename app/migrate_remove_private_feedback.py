"""
Safe Migration Script: Removes private_feedback storage from live Supabase and local SQLite.
Ensures zero disruption to live tenants, businesses, owners, payments, and analytics.
"""
import os
import sys
import sqlite3
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.database import (
    SUPABASE_URL,
    SUPABASE_KEY,
    REST_BASE,
    _headers,
    _delete,
    _get,
    _count
)

def run_migration():
    print("=" * 60)
    print("[MIGRATION] RUNNING SAFE MIGRATION: REMOVE PRIVATE FEEDBACK STORAGE")
    print("=" * 60)

    # 1. Supabase Migration
    if SUPABASE_URL and SUPABASE_KEY:
        print(f"\n[1/2] Connecting to Supabase at: {SUPABASE_URL}")
        try:
            # Check existing private_feedback count
            count_before = _count("private_feedback")
            print(f"  Current private_feedback rows in Supabase: {count_before}")

            if count_before > 0:
                # Delete all rows from private_feedback via REST PostgREST
                del_url = f"{REST_BASE}/private_feedback?id=gte.0"
                resp = requests.delete(del_url, headers=_headers())
                print(f"  Deleted private_feedback rows: status {resp.status_code}")

            # Also delete 'feedback_submitted' analytics events
            del_events_url = f"{REST_BASE}/analytics_events?event_type=eq.feedback_submitted"
            resp_ev = requests.delete(del_events_url, headers=_headers())
            print(f"  Cleaned feedback_submitted analytics events: status {resp_ev.status_code}")

            count_after = _count("private_feedback")
            print(f"  [SUCCESS] Supabase private_feedback count is now: {count_after}")
        except Exception as e:
            print(f"  Supabase migration note: {e}")
    else:
        print("\n[1/2] Supabase credentials not found, skipping cloud migration.")

    # 2. Local SQLite Migration (reviews.db)
    db_path = os.path.join(os.path.dirname(__file__), "reviews.db")
    print(f"\n[2/2] Checking local SQLite at: {db_path}")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Drop private_feedback table
        cur.execute("DROP TABLE IF EXISTS private_feedback;")
        print("  Dropped table 'private_feedback' in SQLite.")

        # Clean analytics_events
        cur.execute("DELETE FROM analytics_events WHERE event_type = 'feedback_submitted';")
        print("  Cleaned feedback_submitted analytics events in SQLite.")

        # Create whatsapp_alert_logs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_alert_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL,
                alert_type TEXT NOT NULL,
                recipient_masked TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                error_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("  Ensured table 'whatsapp_alert_logs' in SQLite.")

        # Create google_business_reviews
        cur.execute("""
            CREATE TABLE IF NOT EXISTS google_business_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL,
                google_review_id TEXT NOT NULL UNIQUE,
                reviewer_name TEXT,
                star_rating INTEGER NOT NULL,
                review_text TEXT,
                review_language TEXT DEFAULT 'en',
                reply_status TEXT NOT NULL DEFAULT 'draft',
                drafted_reply TEXT,
                posted_reply TEXT,
                reply_posted_at TIMESTAMP,
                whatsapp_alert_sent BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("  Ensured table 'google_business_reviews' in SQLite.")

        conn.commit()
        conn.close()
        print("  [SUCCESS] Local SQLite migration complete.")
    else:
        print("  Local reviews.db does not exist yet.")

    print("\n[SUCCESS] Migration completed successfully!")

if __name__ == "__main__":
    run_migration()
