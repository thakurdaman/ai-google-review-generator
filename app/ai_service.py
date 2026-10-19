"""
AI Service for ReviewBoost SaaS Platform.
Provides:
1. Multi-language Customer Google Review Draft Generator (for customer NFC/QR scans).
2. Intelligent Google Business Profile Auto-Reply Engine:
   - Supports English, Hindi (Devanagari), and Hinglish.
   - Distinct logic per star rating:
     * 4-5 stars: Warm, specific thank-you mentioning details from review.
     * 3 stars: Constructive gratitude, acknowledges improvement areas, invites feedback.
     * 1-2 stars: Calm, apologetic, professional. NEVER argues, NEVER admits legal liability,
                 NEVER offers financial compensation, invites direct contact with owner.
     * Star-only (no text): Short, polite thank-you.
   - Owner customization: tone (friendly, professional, casual), signature, required & banned phrases.
   - High variety enforcement (avoids repetitive templates and prevents duplicate replies).
   - Strict 500-char input capping & prompt injection neutralization.
"""

import os
import re
import json
import random
import requests
from typing import List, Dict, Any, Optional
from .database import get_setting
from .security import sanitize_ai_customer_note, PROMPT_INJECTION_PATTERNS


# ── Language Detection Helper ─────────────────────────────────────────────────

def detect_review_language(text: str) -> str:
    """
    Detects whether review text is in English ('en'), Hindi Devanagari ('hi'), or Hinglish ('hinglish').
    """
    if not text or not isinstance(text, str):
        return "en"
    clean = text.strip()
    
    # 1. Check for Devanagari Unicode script range U+0900 to U+097F
    if re.search(r'[\u0900-\u097F]', clean):
        return "hi"

    # 2. Check for genuine Hinglish vocabulary markers (exclude common English words like 'staff', 'service')
    hinglish_markers = {
        "accha", "achha", "achhi", "bahut", "bohot", "bhot", "nahi", "nhi", "karo", "kare",
        "hai", "hain", "tha", "thi", "the", "badhiya", "shandar", "dhanyawad", "dhanyavad",
        "sukriya", "shukriya", "bhai", "kripya", "swadist", "swad", "mast", "ekdum", "theek",
        "thik", "bakwas", "bekar", "ghatiya", "khana", "paisa", "vasool", "bura", "ganda",
        "bhi", "aur", "ye", "yeh", "wo", "woh", "pasand", "aaya", "aayi", "laajawab", "maza",
        "jaldi", "der", "lagi", "kuch", "sab", "kya", "kyu", "kyun", "kaise", "hoga", "hogi"
    }
    words = re.findall(r'\b[a-zA-Z]+\b', clean.lower())
    if not words:
        return "en"
    match_count = sum(1 for w in words if w in hinglish_markers)
    # Require at least 2 distinct hinglish markers or 1 strong marker in short phrase
    if match_count >= 2 or (len(words) <= 6 and match_count >= 1):
        return "hinglish"

    return "en"


def sanitize_review_input(text: Optional[str], max_len: int = 500) -> str:
    """Caps review input text to 500 chars and removes prompt injection attempts."""
    if not text or not isinstance(text, str):
        return ""
    clean = text.strip()[:max_len]
    for pattern in PROMPT_INJECTION_PATTERNS:
        clean = re.sub(pattern, "[filtered]", clean)
    clean = "".join(ch for ch in clean if ch.isprintable() or ch in ("\n", " "))
    return clean.strip()


# ── Customer Flow: Review Drafts Generator ────────────────────────────────────

def generate_review_drafts(
    business_name: str,
    business_category: str,
    rating: int,
    selected_tags: List[str],
    custom_note: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates 3 natural, human-sounding review drafts for customers scanning the QR/NFC.
    Supports all star ratings (1 to 5) so customers are never blocked from reviewing.
    """
    sanitized_note = sanitize_ai_customer_note(custom_note)
    safe_tags = [str(t).strip()[:50] for t in (selected_tags or []) if t][:8]

    api_key = get_setting("openai_api_key", os.getenv("OPENAI_API_KEY", "")).strip()
    tags_str = ", ".join(safe_tags) if safe_tags else "friendly staff and prompt service"
    extra_context = f" Extra detail: '{sanitized_note}'." if sanitized_note else ""

    if api_key:
        try:
            return call_openai_gpt(
                api_key=api_key,
                business_name=business_name,
                business_category=business_category,
                rating=rating,
                tags_str=tags_str,
                extra_context=extra_context
            )
        except Exception as e:
            print(f"[AI Service] OpenAI API error: {e}. Falling back to dynamic review synthesizer.")

    return generate_fallback_drafts(business_name, business_category, rating, safe_tags, custom_note)


def call_openai_gpt(
    api_key: str,
    business_name: str,
    business_category: str,
    rating: int,
    tags_str: str,
    extra_context: str
) -> Dict[str, Any]:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "You are an assistant that helps real customers write natural, authentic Google reviews. "
        "Write exactly 3 distinct, short review drafts matching the customer's star rating. "
        "Rules:\n"
        "1. Write like an ordinary person posting on Google Maps from their phone.\n"
        "2. Avoid cheesy marketing slogans or overly formal corporate words.\n"
        "3. Length: 1 to 3 sentences maximum per draft.\n"
        "4. Output must be strictly valid JSON matching this schema:\n"
        '{"drafts": [{"tone": "Warm & Friendly", "text": "..."}, {"tone": "Short & Punchy", "text": "..."}, {"tone": "Detailed & Helpful", "text": "..."}]}'
    )

    user_prompt = (
        f"Business: {business_name} ({business_category})\n"
        f"Star Rating: {rating} / 5 stars\n"
        f"Customer highlights: {tags_str}.{extra_context}\n"
        f"Generate 3 natural Google review drafts now."
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.75,
        "max_tokens": 400,
        "response_format": {"type": "json_object"}
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)

    return {
        "engine": "OpenAI GPT-4o mini",
        "drafts": parsed.get("drafts", [])
    }


def generate_fallback_drafts(
    business_name: str,
    category: str,
    rating: int,
    tags: List[str],
    custom_note: Optional[str]
) -> Dict[str, Any]:
    """
    Intelligent dynamic contextual review generator that creates 3 realistic,
    varied, authentic-sounding drafts for ratings 1-5 without external API key.
    """
    tag_mention = ", ".join(tags) if tags else "the service"
    first_tag = tags[0] if tags else "service"
    note_addon = f" {custom_note.strip()}." if custom_note and custom_note.strip() else ""

    if rating >= 4:
        drafts = [
            {
                "tone": "Warm & Friendly",
                "text": f"Had such a great experience at {business_name}! Especially loved {tag_mention.lower()}. The team was welcoming and made everything easy.{note_addon} Will definitely be coming back!"
            },
            {
                "tone": "Short & Punchy",
                "text": f"Fast, friendly, and top quality. {first_tag} was on point.{note_addon} Highly recommended!"
            },
            {
                "tone": "Detailed & Helpful",
                "text": f"If you're looking for quality {category.lower()}, {business_name} is the place to go. They really delivered on {tag_mention.lower()}.{note_addon} Professional staff and clean setup throughout."
            }
        ]
    elif rating == 3:
        drafts = [
            {
                "tone": "Balanced & Constructive",
                "text": f"Visited {business_name} today. {first_tag} was decent, though there are a couple of small areas that could be improved.{note_addon} Overall a fair visit."
            },
            {
                "tone": "Short & Honest",
                "text": f"Average experience at {business_name}. {tag_mention.lower()} was okay.{note_addon} Good potential with some adjustments."
            },
            {
                "tone": "Detailed Feedback",
                "text": f"A 3-star visit for {business_name}. The staff was polite and {first_tag.lower()} was satisfactory, but service pace could be tightened up.{note_addon}"
            }
        ]
    else:
        # 1-2 stars
        drafts = [
            {
                "tone": "Direct & Honest",
                "text": f"Had an issue during my visit to {business_name} regarding {tag_mention.lower()}.{note_addon} Hope management looks into this."
            },
            {
                "tone": "Constructive Concern",
                "text": f"Disappointed with {first_tag.lower()} today at {business_name}.{note_addon} Expected better based on the reviews."
            },
            {
                "tone": "Clear & Calm",
                "text": f"Unfortunately my visit to {business_name} fell short due to {tag_mention.lower()}.{note_addon} Sharing honest feedback for improvement."
            }
        ]

    return {
        "engine": "Smart Natural Engine (Ready for OpenAI key)",
        "drafts": drafts
    }


# ── Owner Flow: Google Business Review Auto-Reply Engine ──────────────────────

def generate_google_review_reply(
    review_text: str = "",
    rating: Optional[int] = None,
    reviewer_name: str = "",
    tone: str = "friendly",
    business_name: str = "our business",
    signature: str = "",
    required_phrases: str = "",
    banned_phrases: str = "",
    previous_replies: Optional[List[str]] = None,
    review_rating: Optional[int] = None,
    autoreply_settings: Optional[Dict[str, Any]] = None,
    **kwargs
) -> str:
    """
    Generates a personalized, context-aware reply to a Google Business review.
    """
    actual_rating = rating if rating is not None else (review_rating if review_rating is not None else 5)
    if autoreply_settings:
        tone = autoreply_settings.get("tone", tone)
        signature = autoreply_settings.get("signature", signature)
        req_p = autoreply_settings.get("required_phrases", required_phrases)
        if isinstance(req_p, list):
            required_phrases = ", ".join(req_p)
        else:
            required_phrases = req_p or ""
        ban_p = autoreply_settings.get("banned_phrases", banned_phrases)
        if isinstance(ban_p, list):
            banned_phrases = ", ".join(ban_p)
        else:
            banned_phrases = ban_p or ""

    # 1. Sanitize and cap review input
    safe_text = sanitize_review_input(review_text, max_len=500)
    has_text = bool(safe_text and len(safe_text) > 3)
    lang = detect_review_language(safe_text) if has_text else "en"

    reviewer_greeting = f"{reviewer_name.strip()}" if reviewer_name and len(reviewer_name) < 40 else ""

    api_key = get_setting("openai_api_key", os.getenv("OPENAI_API_KEY", "")).strip()

    reply = ""
    if api_key:
        try:
            reply = _call_openai_review_reply(
                api_key=api_key,
                review_text=safe_text,
                rating=actual_rating,
                reviewer_name=reviewer_greeting,
                lang=lang,
                tone=tone,
                business_name=business_name,
                has_text=has_text
            )
        except Exception as e:
            print(f"[AI Service] OpenAI Review Reply error: {e}. Using multi-language synthesizer.")

    if not reply:
        reply = _synthesize_review_reply(
            review_text=safe_text,
            rating=actual_rating,
            reviewer_name=reviewer_greeting,
            lang=lang,
            tone=tone,
            business_name=business_name,
            has_text=has_text,
            previous_replies=previous_replies or []
        )

    # 2. Inject required phrases if specified
    if required_phrases:
        req_list = [p.strip() for p in required_phrases.split(",") if p.strip()]
        for req in req_list:
            if req.lower() not in reply.lower():
                reply = f"{reply} {req}"

    # 3. Scrub banned phrases if specified
    if banned_phrases:
        banned_list = [p.strip() for p in banned_phrases.split(",") if p.strip()]
        for banned in banned_list:
            # Case-insensitive replacement
            pattern = re.compile(re.escape(banned), re.IGNORECASE)
            reply = pattern.sub("", reply)

    # 4. Clean extra spaces
    reply = re.sub(r'\s+', ' ', reply).strip()

    # 5. Append owner signature if specified
    if signature and signature.strip():
        clean_sig = signature.strip()
        if not clean_sig.startswith(("-", "—")):
            clean_sig = f"— {clean_sig}"
        reply = f"{reply}\n\n{clean_sig}"

    # 6. Ensure reply is not identical to previous reply
    if previous_replies and reply in previous_replies:
        # Slight modification for freshness
        salt_phrases = [" We truly appreciate your time.", " Thank you once again.", " Warm regards."]
        for salt in salt_phrases:
            candidate = f"{reply}{salt}"
            if candidate not in previous_replies:
                reply = candidate
                break

    return reply


def _call_openai_review_reply(
    api_key: str,
    review_text: str,
    rating: int,
    reviewer_name: str,
    lang: str,
    tone: str,
    business_name: str,
    has_text: bool
) -> str:
    """Calls OpenAI GPT-4o-mini with tailored prompt for authentic Google review replies."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    lang_desc = "English"
    if lang == "hi":
        lang_desc = "pure Hindi in Devanagari script"
    elif lang == "hinglish":
        lang_desc = "conversational Hinglish (Romanized Hindi as commonly spoken in India)"

    system_prompt = (
        f"You are the owner/manager of '{business_name}' writing an authentic, human reply to a Google review.\n"
        f"Language requirement: You MUST write your reply in {lang_desc}.\n"
        f"Tone style: {tone}.\n\n"
        "Strict Rating Protocols:\n"
        "1. For 4 or 5 stars: Give a warm, genuine thank-you. Mention specific details from the review rather than a generic line.\n"
        "2. For 3 stars: Thank them warmly, acknowledge what could be improved, and invite them to share more details so you can serve them better next time.\n"
        "3. For 1 or 2 stars:\n"
        "   - Reply in a calm, humble, apologetic, and professional manner.\n"
        "   - NEVER argue, NEVER make excuses, NEVER blame the customer.\n"
        "   - NEVER admit legal liability or say words like 'our legal fault'.\n"
        "   - NEVER offer financial compensation, refunds, or free items in public.\n"
        "   - Invite them to reach out directly to the management team so their concern can be resolved personally.\n"
        "4. For reviews with no text: Provide a short, polite thank-you acknowledging their star rating.\n"
        "5. Length: 2 to 4 sentences maximum. Keep it concise, natural, and never sounding like a bot."
    )

    greeting_hint = f"Reviewer name: {reviewer_name}. " if reviewer_name else ""
    user_prompt = (
        f"{greeting_hint}Rating: {rating}/5 stars.\n"
        f"Customer review text: '{review_text if has_text else '[No text provided, star rating only]'}'\n"
        f"Generate the reply in {lang_desc} now."
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.85,
        "max_tokens": 250
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    reply_text = data["choices"][0]["message"]["content"].strip()
    return reply_text.strip('"\'')


def _synthesize_review_reply(
    review_text: str,
    rating: int,
    reviewer_name: str,
    lang: str,
    tone: str,
    business_name: str,
    has_text: bool,
    previous_replies: List[str]
) -> str:
    """
    High-variety multi-language template synthesizer covering English, Hindi, and Hinglish
    for all star ratings and tone variants.
    """
    greeting = f"Hi {reviewer_name}, " if reviewer_name else "Hello, "
    greeting_hi = f"नमस्ते {reviewer_name}, " if reviewer_name else "नमस्ते, "
    greeting_hinglish = f"Hi {reviewer_name}, " if reviewer_name else "Hello, "

    pool = []

    # ── ENGLISH TEMPLATES ─────────────────────────────────────────────────────
    if lang == "en":
        if not has_text:
            pool = [
                f"{greeting}thank you for taking a moment to rate {business_name}! We appreciate your support and look forward to welcoming you back again soon.",
                f"{greeting}thanks for the rating! We're glad to have you as our guest and hope to see you again soon.",
                f"{greeting}thank you for your rating. We truly value your visit and look forward to serving you next time!"
            ]
        elif rating >= 4:
            if tone == "casual":
                pool = [
                    f"{greeting}thanks a bunch for the awesome review! We're thrilled you enjoyed your time with us at {business_name}. Can't wait to see you back here soon!",
                    f"{greeting}really appreciate the kind words! Super happy to know our team made your visit great. Catch you again soon!",
                    f"{greeting}you made our day! Thanks for sharing your experience with {business_name}. See you next time!"
                ]
            elif tone == "professional":
                pool = [
                    f"{greeting}thank you for taking the time to share your positive feedback. The team at {business_name} takes great pride in delivering excellent service, and we look forward to serving you again.",
                    f"{greeting}we sincerely appreciate your generous review. It is always our goal to ensure every guest has a seamless experience at {business_name}. We look forward to your next visit.",
                    f"{greeting}thank you for your commendation. Delivering dependable quality is our top priority, and we are grateful for your valued patronage."
                ]
            else:  # friendly
                pool = [
                    f"{greeting}thank you so much for the wonderful review! We're so glad to hear you had such a great experience with us at {business_name}. Looking forward to seeing you again soon!",
                    f"{greeting}we truly appreciate your feedback! It means the world to our team to know you enjoyed your visit. See you again very soon!",
                    f"{greeting}thank you for sharing your thoughts! We loved having you at {business_name} and can't wait to welcome you back."
                ]
        elif rating == 3:
            pool = [
                f"{greeting}thank you for sharing your feedback with us. We're glad you visited {business_name}, and we always look for ways to make our guest experience even better. We would love to hear more about how we can earn that 5th star next time!",
                f"{greeting}thanks for taking the time to leave a review. We appreciate your honest thoughts and are constantly refining our service. We hope to welcome you back for an improved experience.",
                f"{greeting}thank you for visiting {business_name}. We appreciate your constructive feedback and will discuss this with our team so your next visit is truly five-star."
            ]
        else:  # 1-2 stars
            pool = [
                f"{greeting}thank you for bringing this to our attention. We are genuinely sorry to hear that your experience fell short of expectations. We take guest satisfaction very seriously and would appreciate the chance to connect with you directly to understand what happened. Please feel free to reach out to our management team.",
                f"{greeting}we sincerely apologize that your visit did not meet the standards we strive for at {business_name}. We value your honest feedback and would welcome the opportunity to speak with you directly to make things right. Please contact us at your convenience.",
                f"{greeting}we are sorry to learn about your disappointing experience. Delivering courteous, quality service is our priority, and we regret missing the mark during your visit. Please contact our leadership directly so we can look into this for you."
            ]

    # ── HINDI (DEVANAGARI) TEMPLATES ──────────────────────────────────────────
    elif lang == "hi":
        if not has_text:
            pool = [
                f"{greeting_hi}{business_name} को रेटिंग देने के लिए आपका बहुत-बहुत धन्यवाद! हम आशा करते हैं कि आपका अनुभव अच्छा रहा होगा। जल्द ही दोबारा पधारें।",
                f"{greeting_hi}आपकी रेटिंग के लिए धन्यवाद। {business_name} में आपका स्वागत करना हमेशा हमारे लिए खुशी की बात है।"
            ]
        elif rating >= 4:
            pool = [
                f"{greeting_hi}{business_name} पर अपने बेहतरीन अनुभव को साझा करने के लिए आपका दिल से धन्यवाद! आपकी संतुष्टि ही हमारी सबसे बड़ी प्रेरणा है। जल्द ही आपसे दोबारा मिलने की उम्मीद है।",
                f"{greeting_hi}समीक्षा के लिए बहुत-बहुत शुक्रिया! हमारी पूरी टीम को यह जानकर बेहद खुशी हुई कि आपको हमारी सेवाएं पसंद आईं। अगली बार आपका फिर स्वागत है!",
                f"{greeting_hi}आपकी सकारात्मक प्रतिक्रिया के लिए धन्यवाद! हम हर ग्राहक को उत्कृष्ट सेवा देने के लिए प्रतिबद्ध हैं। आपसे दोबारा मिलने का इंतज़ार रहेगा।"
            ]
        elif rating == 3:
            pool = [
                f"{greeting_hi}अपनी राय साझा करने के लिए धन्यवाद। हमें खुशी है कि आप {business_name} पधारे। हम अपनी सेवाओं में निरंतर सुधार के लिए प्रयासरत हैं और अगली बार आपको और भी बेहतर अनुभव देने की पूरी कोशिश करेंगे।",
                f"{greeting_hi}आपके सुझाव के लिए धन्यवाद। हम आपकी बातों को गंभीरता से लेते हैं ताकि आपकी अगली यात्रा 5-स्टार अनुभव बन सके।"
            ]
        else:  # 1-2 stars
            pool = [
                f"{greeting_hi}अपनी असुविधा के बारे में हमें बताने के लिए धन्यवाद। हमें बेहद खेद है कि आपका अनुभव हमारी सामान्य गुणवत्ता के अनुरूप नहीं रहा। हम हर ग्राहक की संतुष्टि को प्राथमिकता देते हैं। कृपया हमारे प्रबंधन से सीधे संपर्क करें ताकि हम आपकी समस्या को ठीक से समझ सकें।",
                f"{greeting_hi}आपके अनुभव के लिए हमें खेद है। {business_name} में हम हमेशा श्रेष्ठ सेवा देने का प्रयास करते हैं, पर इस बार हमसे कमी रह गई। कृपया हमसे सीधे संपर्क करें ताकि हम इस मामले को व्यक्तिगत रूप से देख सकें।"
            ]

    # ── HINGLISH TEMPLATES ────────────────────────────────────────────────────
    else:  # hinglish
        if not has_text:
            pool = [
                f"{greeting_hinglish}{business_name} ko rating dene ke liye bohot shukriya! Next time aane par aapka phir se swagat hai.",
                f"{greeting_hinglish}thanks for the rating! Glad to have you at {business_name}. See you again soon!"
            ]
        elif rating >= 4:
            pool = [
                f"{greeting_hinglish}bohot bohot shukriya itna badhiya review share karne ke liye! Humari team ko jaankar bohot khushi hui ki aapko {business_name} ki service pasand aayi. Jaldi hi wapas aaiye!",
                f"{greeting_hinglish}dil se thank you! Aapka feedback humare liye bohot valuable hai. Next visit par aapko aur bhi accha experience dene ki koshish karenge. See you soon!",
                f"{greeting_hinglish}thanks a lot! Review padh kar bohot accha laga. {business_name} par aapka welcome hamesha rahega!"
            ]
        elif rating == 3:
            pool = [
                f"{greeting_hinglish}feedback share karne ke liye shukriya. Hum glad hain ki aap {business_name} aaye, aur hum hamesha apni service better karne par kaam kar rahe hain. Next time aane par aapko 5-star experience zaroor milega!",
                f"{greeting_hinglish}thanks for your honest feedback. Hum apni team ke saath discuss karenge taaki next time sab kuch perfect ho. Looking forward to seeing you again!"
            ]
        else:  # 1-2 stars
            pool = [
                f"{greeting_hinglish}hum genuinely sorry hain ki aapka experience accha nahi raha. {business_name} par hum hamesha best service deliver karne ki koshish karte hain. Please humare management team se directly contact karein taaki hum is matter ko personally look into kar sakein.",
                f"{greeting_hinglish}aapke experience ke baare me jaan kar hume sach me dukh hua. Hum har feedback ko bohot seriously lete hain. Please hume directly call ya message karein taaki hum aapki problem samajh sakein aur serve kar sakein."
            ]

    # Select candidate not in previous replies
    for choice in pool:
        if choice not in previous_replies:
            return choice

    return random.choice(pool) if pool else f"{greeting}thank you for your review. We appreciate your time and support."


def test_openai_connection(api_key: str) -> Dict[str, Any]:
    """Test connection to OpenAI API using provided key."""
    if not api_key:
        return {"success": False, "error": "API key cannot be empty"}
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=8
        )
        if resp.status_code == 200:
            return {"success": True, "message": "Successfully connected to OpenAI API!"}
        else:
            return {"success": False, "error": f"OpenAI returned error code {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

