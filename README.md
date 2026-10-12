# ReviewBoost - Multi-Tenant Google Review Collection Platform

A clean, mobile-first SaaS web application designed for Google review collection businesses, digital agencies, and resellers.

## 🚀 Key Features

### 1. Reseller & Admin Portal
- **Secure Authentication**: Reseller login with session cookies and SHA-256 hashed passwords.
- **Client Business Management**: Add, edit, or delete client businesses.
  - Business name, unique short-link slug (`/r/:slug`), category.
  - Target Google review URL (Place ID or Google Business link).
  - Logo URL / upload and custom brand accent colors.
  - Customizable quick-tap tags / experience chips per business.
- **Live Analytics Dashboard**:
  - Total Scans / NFC taps per business and platform-wide.
  - Completed 5-star Google review clicks.
  - Conversion rate percentage (% of visitors who posted a review).
  - Shielded negative feedback count.
- **QR Code & NFC Material Generator**:
  - Live high-resolution QR code generator (PNG / SVG).
  - Printable Table Tent / Counter Stand Flyer (`/admin/business/:id/flyer`) with QR code, NFC tap indicator, and instructions ready for printing.
- **Private Feedback Inbox**:
  - Dedicated inbox displaying intercepted 1-3 star customer feedback.
  - View customer rating, written comments, and optional customer contact info for management follow-up.
- **AI Settings**:
  - Configure OpenAI API Key (`gpt-4o-mini`) with live connection testing.
  - Seamless fallback engine ensures the app works immediately out of the box even before adding an API key.

### 2. Mobile-First Customer Landing Page (`/r/:slug`)
- Designed specifically for smartphone screens right after scanning a table QR code or tapping an NFC card.
- **Step 1: 5-Star Interactive Rating**:
  - Delightful star rating selector with dynamic descriptive labels.
- **Step 2A: Positive Review Flow (4-5 Stars)**:
  - Quick tap-to-select options about their experience (e.g. "Smooth Espresso", "Friendly Staff", "Quick Service").
  - Optional custom note input.
  - AI Review Drafting: Generates **three distinct short review drafts** in authentic, everyday human tones:
    1. *Warm & Friendly* (enthusiastic, genuine)
    2. *Short & Punchy* (concise, 1-2 sentences)
    3. *Detailed & Helpful* (mentions specific highlights)
  - Interactive selection cards + inline editable textarea so customers can tweak the text.
  - **Copy & Post to Google**: Automatically copies the chosen draft to the customer's clipboard, logs analytics completion, and redirects straight to the business's Google review URL with visual paste instructions.
- **Step 2B: Strict Review Gating Guardrail (1-3 Stars)**:
  - **Strictly skips AI generation**: No AI is called, preventing false positive reviews.
  - Opens a private customer feedback form.
  - Submits comments directly to the business owner / reseller dashboard.
  - Shows a compassionate thank-you screen and **never** redirects to Google.

---

## 🛠️ Tech Stack
- **Backend**: Python 3.12, FastAPI, Uvicorn, Jinja2, Pydantic, Requests
- **Database**: SQLite3 (`reviews.db`) with automatic schema migration and seed data
- **AI Engine**: OpenAI API (`gpt-4o-mini`) + Built-in Smart Synthesizer fallback
- **QR & NFC Engine**: Python `qrcode`, `Pillow`, SVG vector generator
- **Frontend**: Mobile-first responsive HTML5, Tailwind CSS, Font Awesome 6, Plus Jakarta Sans typography

---

## 🏃 Quick Start

### 1. Launch the Server
From the project folder, run:
```bash
python run.py
```
Or double-click `start.bat` on Windows.

### 2. Access the Application
- **Reseller Admin Dashboard**: [http://localhost:8000/admin](http://localhost:8000/admin)
  - **Username**: `admin`
  - **Password**: `admin123`
- **Demo Customer Review Pages**:
  - Café: [http://localhost:8000/r/artisan-roast](http://localhost:8000/r/artisan-roast)
  - Dental Clinic: [http://localhost:8000/r/smilecraft-dental](http://localhost:8000/r/smilecraft-dental)
  - Auto Care: [http://localhost:8000/r/apex-auto](http://localhost:8000/r/apex-auto)
- **Printable Table Tent / Stand**:
  - [http://localhost:8000/admin/business/1/flyer](http://localhost:8000/admin/business/1/flyer)

---

## 🧪 Running Automated Tests
Run the included 10-point end-to-end test suite:
```bash
python tests/test_flow.py
```
All tests verify database seeding, 5-star AI review generation, 1-3 star AI guardrail rejection, private feedback storage, analytics logging, and QR image generation.
