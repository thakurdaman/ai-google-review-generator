# ReviewBoost - Multi-Tenant Google Review Collection & Subscription SaaS

A clean, mobile-first SaaS web application for Google review collection agencies, resellers, and direct business owners.

## 🚀 Key Features

### 1. Self-Serve Business Owner Onboarding & Portal
- **Public Signup (`/signup`)**: Business owners can self-register with their Business Name, Email, Password, Google Review Link, Category, Logo, and Brand Accent Color—no reseller manual setup required.
- **Dedicated Business Owner Dashboard (`/dashboard`)**:
  - Live QR code and NFC link with instant high-res PNG / SVG download.
  - Printable Table Tent / Counter Stand Flyer (`/dashboard/flyer`) ready for printing in 4x6 format.
  - Live analytics: Scans, completed 5-star reviews, review conversion rate %, and shielded feedback count.
  - **Shielded Feedback Inbox**: View intercepted 1-3 star ratings with customer contact information to resolve issues directly.
  - **Profile & Google Link Settings**: Update business details and Google review link with automatic timestamp tracking and activity audit logging.
  - **Plan & Billing Management**: View current subscription plan, payment method (UPI Autopay vs One-Time), renewal dates, change plans, cancel autopay, and view complete payment invoice history.

### 2. Pricing & Subscription Plans with UPI Autopay
- **Two Flexible Plans**:
  - **Monthly Pro**: ₹999 / month (Full access to AI review generation, QR/NFC links, and review shielding).
  - **Annual Growth**: ₹8,999 / year (Save 25% + 2 Months Free, priority AI drafting).
- **Two Payment Modes**:
  - 🔄 **UPI Autopay Mandate (Recurring)**: Seamless recurring mandate setup via Google Pay, PhonePe, Paytm, BHIM, and Indian bank UPI apps. Can be cancelled anytime with 1 tap.
  - 💳 **One-Time Payment**: Single billing cycle charge for businesses preferring manual renewal.
- **Razorpay Integration & Sandbox Mode**:
  - Seamless Razorpay Subscriptions & Orders gateway integration.
  - Built-in interactive sandbox simulation allows instant testing of UPI Autopay mandates and one-time payments even before entering live Razorpay API keys.
- **Automatic Access Gating**:
  - Actively enforces subscription status on customer landing pages (`/r/:slug`).
  - Active subscriptions unlock instant access.
  - Inactive/expired subscriptions display a graceful "Portal Temporarily Paused" notice with a 1-click button for the owner to log in and renew.

### 3. Reseller Admin Hub with Full Platform Visibility
- **Complete Business & Owner Transparency**:
  - Platform overview: Total Registered Businesses, Platform MRR, Active Subscriptions, Total Scans, and Google Reviews.
  - Complete Business Owners Table: View every owner's signup date, selected plan, billing mode, subscription status, last login timestamp, and last Google URL update timestamp.
- **Read-Only Business Inspection**:
  - Admins can inspect any business individually to view the exact same performance analytics the owner sees.
  - Complete Activity Audit Trail: See when owners registered, when they logged in, when they updated their Google review links, and when payments were authorized.
  - Read-only protection ensures owners retain autonomy over their settings while admins maintain 100% backend oversight.
- **Platform Invoices & Payments**: Platform-wide transaction ledger displaying all charges, payment modes, and payment IDs.
- **Platform Configuration**: Settings panel to configure OpenAI API keys (`gpt-4o-mini`) and Razorpay keys (`key_id` & `key_secret`).

### 4. Mobile-First Customer Review Experience (`/r/:slug`)
- Designed specifically for smartphone screens right after scanning a counter QR code or tapping an NFC tag.
- **Step 1: 5-Star Interactive Rating**:
  - Delightful touch-friendly star rating selector with dynamic descriptive labels.
- **Step 2A: Positive Review Flow (4-5 Stars)**:
  - Quick tap-to-select experience chips (e.g. "Smooth Espresso", "Friendly Staff", "Quick Turnaround").
  - AI Review Drafting: Calls OpenAI `gpt-4o-mini` (or built-in smart natural synthesizer) to generate **three distinct short review drafts** in authentic, everyday human tones (*Warm & Friendly*, *Short & Punchy*, *Detailed & Helpful*).
  - Interactive selection cards + inline editable textarea.
  - **Copy & Post to Google**: Automatically copies the chosen draft to clipboard, logs analytics completion, and redirects straight to the business's Google review URL with visual paste instructions.
- **Step 2B: Strict Review Gating Guardrail (1-3 Stars)**:
  - **Strictly skips AI generation**: No AI is called, preventing false positive reviews.
  - Opens private customer feedback form submitted directly to management.
  - Never redirects to Google.

---

## 🛠️ Tech Stack
- **Backend**: Python 3.12, FastAPI, Uvicorn, Jinja2, Pydantic, Requests
- **Database**: SQLite3 (`reviews.db`) with automatic schema migration and seed data
- **Billing**: Razorpay Subscriptions REST API & UPI Autopay Mandates (with Sandbox Mode)
- **AI Engine**: OpenAI API (`gpt-4o-mini`) + Built-in Smart Synthesizer fallback
- **QR & NFC Engine**: Python `qrcode`, `Pillow`, SVG vector generator
- **Frontend**: Mobile-first responsive HTML5, Tailwind CSS, Font Awesome 6, Plus Jakarta Sans typography
- **Deployment**: Vercel Serverless Ready (`api/index.py`, `vercel.json`, `requirements.txt`)

---

## 🏃 Quick Start

### 1. Launch the Server Locally
```bash
python run.py
```
Or double-click `start.bat` on Windows.

### 2. Access the Application
- **Public Self-Serve Signup**: [http://localhost:8000/signup](http://localhost:8000/signup)
- **Business Owner Login**: [http://localhost:8000/login](http://localhost:8000/login)
  - **Demo Owner Credentials**: `owner@artisancafe.com` / `owner123`
- **Owner Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
- **Reseller Admin Dashboard**: [http://localhost:8000/admin](http://localhost:8000/admin)
  - **Admin Credentials**: `admin` / `admin123`
- **Customer Review Landing Page (Demo)**: [http://localhost:8000/r/artisan-roast](http://localhost:8000/r/artisan-roast)
- **Printable Table Tent / Stand**: [http://localhost:8000/dashboard/flyer](http://localhost:8000/dashboard/flyer)

---

## 🧪 Automated Testing
Run the comprehensive 11-point end-to-end test suite:
```bash
python tests/test_flow.py
```
All tests verify public signup, subscription gating, UPI Autopay checkout, review link updates with audit logs, AI drafting, review gating, and admin read-only inspection.
