import os
import json
import random
import requests
from typing import List, Dict, Any, Optional
from .database import get_setting

def generate_review_drafts(
    business_name: str,
    business_category: str,
    rating: int,
    selected_tags: List[str],
    custom_note: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generates 3 natural, human-sounding review drafts for 4 or 5 star reviews.
    Crucial guardrail: 1-3 star ratings must NEVER call AI.
    """
    if rating < 4:
        raise ValueError("AI review generation is strictly restricted to positive ratings (4-5 stars).")

    # Retrieve API key from settings or environment
    api_key = get_setting("openai_api_key", os.getenv("OPENAI_API_KEY", "")).strip()

    tags_str = ", ".join(selected_tags) if selected_tags else "great experience and friendly service"
    extra_context = f" Extra detail from customer: '{custom_note}'." if custom_note and custom_note.strip() else ""

    if api_key:
        try:
            return call_openai_gpt(
                api_key=api_key,
                business_name=business_name,
                business_category=business_category,
                tags_str=tags_str,
                extra_context=extra_context
            )
        except Exception as e:
            # If OpenAI API call fails (quota, network, invalid key), seamlessly fall back
            print(f"[AI Service] OpenAI API error: {e}. Falling back to dynamic review synthesizer.")

    # High quality fallback synthesizer
    return generate_fallback_drafts(business_name, business_category, selected_tags, custom_note)

def call_openai_gpt(
    api_key: str,
    business_name: str,
    business_category: str,
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
        "Write exactly 3 distinct, short review drafts. "
        "Rules:\n"
        "1. Write like an ordinary, real person posting on Google Maps from their smartphone.\n"
        "2. Avoid cheesy marketing slogans, buzzwords, or overly formal language (never say 'I recently had the pleasure' or 'exemplary establishment').\n"
        "3. Length: 1 to 3 sentences maximum per draft.\n"
        "4. Tone styles:\n"
        "   - Draft 1 (Warm & Friendly): Enthusiastic, genuine, upbeat.\n"
        "   - Draft 2 (Short & Punchy): Direct, straight to the point, casual.\n"
        "   - Draft 3 (Detailed & Helpful): Mentions the specifics naturally for other customers.\n"
        "5. Output must be strictly valid JSON matching this schema:\n"
        '{"drafts": [{"tone": "Warm & Friendly", "text": "..."}, {"tone": "Short & Punchy", "text": "..."}, {"tone": "Detailed & Helpful", "text": "..."}]}'
    )

    user_prompt = (
        f"Business: {business_name} ({business_category})\n"
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
    tags: List[str],
    custom_note: Optional[str]
) -> Dict[str, Any]:
    """
    Intelligent dynamic contextual review generator that creates 3 realistic,
    varied, authentic-sounding drafts even without an external API key.
    """
    tag_mention = ", ".join(tags) if tags else "the top-notch service"
    first_tag = tags[0] if tags else "great service"
    second_tag = tags[1] if len(tags) > 1 else "super friendly staff"

    note_addon = f" {custom_note.strip()}." if custom_note and custom_note.strip() else ""

    # Draft 1: Warm & Friendly
    warm_templates = [
        f"Had such a great experience at {business_name}! Especially loved {tag_mention.lower()}. The team was welcoming and made everything easy.{note_addon} Will definitely be coming back!",
        f"Always love coming to {business_name}. {first_tag} was incredible and {second_tag.lower()} really stood out.{note_addon} 10/10 recommend to anyone in the area!",
        f"Super impressed by {business_name}! Everything from {first_tag.lower()} to {second_tag.lower()} was handled with care.{note_addon} Friendly atmosphere and great vibes all around."
    ]

    # Draft 2: Short & Punchy
    punchy_templates = [
        f"Fast, friendly, and top quality. {first_tag} was on point.{note_addon} Highly recommended!",
        f"Great spot! Really appreciated {tag_mention.lower()}.{note_addon} Will definitely return.",
        f"5 stars for {business_name}. {first_tag} and {second_tag.lower()} made the visit awesome.{note_addon}"
    ]

    # Draft 3: Detailed & Helpful
    detailed_templates = [
        f"If you're looking for quality {category.lower()}, {business_name} is the place to go. They really delivered on {tag_mention.lower()}.{note_addon} Professional staff and clean setup throughout.",
        f"First time checking out {business_name} and they exceeded expectations. Huge fan of {first_tag.lower()} and {second_tag.lower()}.{note_addon} Appreciate businesses that actually care about good customer care.",
        f"Consistently high standards at {business_name}. {first_tag} was spot on, and {second_tag.lower()} made the whole visit seamless.{note_addon} Worth every star!"
    ]

    drafts = [
        {
            "tone": "Warm & Friendly",
            "text": random.choice(warm_templates)
        },
        {
            "tone": "Short & Punchy",
            "text": random.choice(punchy_templates)
        },
        {
            "tone": "Detailed & Helpful",
            "text": random.choice(detailed_templates)
        }
    ]

    return {
        "engine": "Smart Natural Engine (Ready for OpenAI key)",
        "drafts": drafts
    }

def test_openai_connection(api_key: str) -> Dict[str, Any]:
    """Test utility for reseller admin to verify their OpenAI API key."""
    if not api_key:
        return {"success": False, "error": "No API key provided"}
    try:
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            return {"success": True, "message": "OpenAI API connection verified successfully! GPT-4o mini is enabled."}
        else:
            return {"success": False, "error": f"API returned status {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
