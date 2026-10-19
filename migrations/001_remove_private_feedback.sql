-- Migration: 001_remove_private_feedback.sql
-- Description: Safely drops private_feedback table and cleans up low-rating gated analytics
-- Multi-tenant Compliance: Removes review gating so all customers (1-5 stars) proceed to Google Reviews

-- 1. Remove private_feedback table
DROP TABLE IF EXISTS private_feedback CASCADE;

-- 2. Remove analytics events tied to private feedback submissions
DELETE FROM analytics_events WHERE event_type = 'feedback_submitted';

-- 3. Create whatsapp_alert_logs table (records transmission metadata only - NEVER customer message content)
CREATE TABLE IF NOT EXISTS whatsapp_alert_logs (
    id BIGSERIAL PRIMARY KEY,
    business_id BIGINT NOT NULL,
    alert_type VARCHAR(50) NOT NULL,
    recipient_masked VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'sent',
    error_code VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create google_business_reviews table for storing fetched Google reviews and AI drafted/posted replies
CREATE TABLE IF NOT EXISTS google_business_reviews (
    id BIGSERIAL PRIMARY KEY,
    business_id BIGINT NOT NULL,
    google_review_id VARCHAR(255) NOT NULL UNIQUE,
    reviewer_name VARCHAR(150),
    star_rating INT NOT NULL,
    review_text TEXT,
    review_language VARCHAR(20) DEFAULT 'en',
    reply_status VARCHAR(30) NOT NULL DEFAULT 'draft', -- 'posted', 'draft', 'pending_approval', 'off'
    drafted_reply TEXT,
    posted_reply TEXT,
    reply_posted_at TIMESTAMPTZ,
    whatsapp_alert_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
