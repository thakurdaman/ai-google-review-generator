"""
Google Business Profile & Place ID Extraction Helper
Extracts the Google Place ID from Google Business Profile and Google Maps URLs,
and constructs the direct Google review URL in the format:
https://search.google.com/local/writereview?placeid={place_id}
"""

import re
import urllib.parse
import urllib.request
from typing import Optional, Tuple

VALID_EXAMPLE_URL = "https://maps.google.com/?place_id=ChIJN1t_tDeuEmsRUsoyG83frY4"
VALID_WRITEREVIEW_EXAMPLE = "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4"

PLACE_ID_ERROR_MESSAGE = (
    "Could not extract a usable Google Place ID from the link provided. "
    "Please paste your official Google Business Profile or Google Maps link containing your Place ID."
)

# Standard Google Place IDs start with ChIJ or GhIJ, followed by base64-like chars
PLACE_ID_REGEX = re.compile(r'(ChIJ[a-zA-Z0-9_-]{10,45}|GhIJ[a-zA-Z0-9_-]{10,45})')


def extract_google_place_id(input_str: Optional[str]) -> Optional[str]:
    """
    Extracts a Google Place ID from a raw string or URL.
    Supports:
      1. Raw Place IDs (e.g. ChIJN1t_tDeuEmsRUsoyG83frY4)
      2. URLs with query params: placeid, place_id, query_place_id
      3. Google Maps place data URLs containing !1sChIJ...
      4. Standard regex matching across URL path or query
      5. Shortened Google URLs (maps.app.goo.gl, goo.gl/maps) via HTTP redirect resolution
    """
    if not input_str:
        return None

    clean = input_str.strip().strip('"\'')
    if not clean:
        return None

    # 1. Direct match if the string itself is a Place ID
    if re.match(r'^(ChIJ|GhIJ)[a-zA-Z0-9_-]{10,45}$', clean):
        return clean

    # 2. Check query string parameters (placeid, place_id, query_place_id)
    try:
        parsed = urllib.parse.urlparse(clean)
        qs = urllib.parse.parse_qs(parsed.query)
        for param in ['placeid', 'place_id', 'query_place_id']:
            if param in qs and qs[param]:
                val = qs[param][0].strip()
                m = PLACE_ID_REGEX.search(val)
                if m:
                    return m.group(1)
    except Exception:
        pass

    # 3. Direct regex search in the entire URL string (e.g. in /data=!1sChIJ... or /place/ChIJ...)
    match = PLACE_ID_REGEX.search(clean)
    if match:
        return match.group(1)

    # 4. Handle shortened Google share links by following redirects
    lower_clean = clean.lower()
    if any(domain in lower_clean for domain in ['maps.app.goo.gl', 'goo.gl/maps', 'g.page']):
        try:
            req = urllib.request.Request(
                clean,
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'
                    )
                }
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                resolved_url = resp.geturl()
                match = PLACE_ID_REGEX.search(resolved_url)
                if match:
                    return match.group(1)

                # Check query params on resolved url
                parsed_res = urllib.parse.urlparse(resolved_url)
                qs_res = urllib.parse.parse_qs(parsed_res.query)
                for param in ['placeid', 'place_id', 'query_place_id']:
                    if param in qs_res and qs_res[param]:
                        m = PLACE_ID_REGEX.search(qs_res[param][0].strip())
                        if m:
                            return m.group(1)

                # Read first chunk of HTML to inspect embedded place ID
                html_snippet = resp.read(150000).decode('utf-8', errors='ignore')
                match = PLACE_ID_REGEX.search(html_snippet)
                if match:
                    return match.group(1)
        except Exception:
            # Fall through if network resolution fails or times out
            pass

    return None


def construct_direct_review_url(place_id: str) -> str:
    """
    Constructs the official direct Google review URL with place_id parameter
    that opens the star rating and review submission box directly.
    """
    return f"https://search.google.com/local/writereview?placeid={place_id}"


def process_google_profile_link(input_str: Optional[str]) -> Tuple[str, str]:
    """
    Validates the input string, extracts the Place ID, and constructs the
    direct review URL.
    Returns:
        (place_id, direct_review_url)
    Raises:
        ValueError if no usable Place ID can be found.
    """
    place_id = extract_google_place_id(input_str)
    if not place_id:
        raise ValueError(PLACE_ID_ERROR_MESSAGE)

    direct_url = construct_direct_review_url(place_id)
    return place_id, direct_url
