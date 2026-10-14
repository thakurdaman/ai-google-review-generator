"""
Email Validation & Verification Module
Validates email addresses for:
1. Strict RFC-compliant syntax and structure
2. Disposable / temporary throwaway email domain blocking
3. Common domain typo detection with helpful suggestions
"""

import re
from typing import Optional, Tuple

EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
)

# Blocklist of known temporary / disposable fake email services
DISPOSABLE_DOMAINS = {
    'mailinator.com', 'tempmail.com', '10minutemail.com', 'throwawaymail.com',
    'guerrillamail.com', 'guerrillamailblock.com', 'sharklasers.com', 'grr.la',
    'yopmail.com', 'yopmail.fr', 'yopmail.net', 'getnada.com', 'abv.bg',
    'trashmail.com', 'trashmail.net', 'temp-mail.org', 'temp-mail.io',
    'fakemailgenerator.com', 'dispostable.com', 'burnermail.io', 'dropmail.me',
    'crazymailing.com', 'armyspy.com', 'cuvox.de', 'dayrep.com', 'einrot.com',
    'fleckens.hu', 'gustr.com', 'jourrapide.com', 'rhyta.com', 'superrito.com',
    'teleworm.us', 'fakemail.net', 'mytemp.email', 'mohmal.com', 'emailondeck.com',
    'fakeinbox.com', 'trashmail.de', 'maildrop.cc', 'inboxkitten.com'
}

# Common mobile keyboard typos for popular email providers
COMMON_DOMAIN_TYPOS = {
    'gmai.com': 'gmail.com',
    'gamil.com': 'gmail.com',
    'gmial.com': 'gmail.com',
    'gmaill.com': 'gmail.com',
    'gmai.co': 'gmail.com',
    'gmal.com': 'gmail.com',
    'gmaild.com': 'gmail.com',
    'gmail.cpm': 'gmail.com',
    'gmail.con': 'gmail.com',
    'gmail.co': 'gmail.com',
    'yaho.com': 'yahoo.com',
    'yahooo.com': 'yahoo.com',
    'yaho.co.in': 'yahoo.co.in',
    'yahoo.con': 'yahoo.com',
    'hotmial.com': 'hotmail.com',
    'hotmaill.com': 'hotmail.com',
    'outlok.com': 'outlook.com',
    'outloo.com': 'outlook.com',
    'redifmail.com': 'rediffmail.com',
    'rediff.com': 'rediffmail.com'
}


def validate_email_address(email: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Validates an email address.
    Returns:
        (is_valid: bool, error_message: Optional[str])
    """
    if not email or not isinstance(email, str):
        return False, "Email address cannot be empty."

    clean = email.strip()
    if not clean:
        return False, "Email address cannot be empty."

    if len(clean) > 254:
        return False, f"The email address is too long ({len(clean)} characters). Maximum is 254."

    if "@" not in clean:
        return False, f"The email address '{clean}' is not valid (missing '@' symbol)."

    parts = clean.rsplit("@", 1)
    if len(parts) != 2:
        return False, f"The email address '{clean}' is not valid."

    local_part, domain = parts[0], parts[1]
    if not local_part or not domain:
        return False, f"The email address '{clean}' is not valid."

    # Validate local part
    if local_part.startswith(".") or local_part.endswith("."):
        return False, f"The email address '{clean}' is not valid (cannot start or end with a dot)."

    if ".." in local_part or ".." in domain:
        return False, f"The email address '{clean}' is not valid (cannot contain consecutive dots)."

    # Validate domain and TLD
    domain_lower = domain.lower()
    if "." not in domain_lower:
        return False, f"The email address '{clean}' is not valid (missing domain extension like .com or .in)."

    tld = domain_lower.rsplit(".", 1)[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False, f"The email address '{clean}' is not valid (invalid domain extension '.{tld}')."

    # Regex test
    if not EMAIL_REGEX.match(clean):
        return False, f"The email address '{clean}' is not valid. Please check the spelling."

    # Disposable domains check
    if domain_lower in DISPOSABLE_DOMAINS:
        return False, f"The email address '{clean}' is not valid. Temporary or disposable email addresses are not allowed."

    # Common domain typo check with suggestion
    if domain_lower in COMMON_DOMAIN_TYPOS:
        suggested = f"{local_part}@{COMMON_DOMAIN_TYPOS[domain_lower]}"
        return False, f"The email address '{clean}' is not valid. Did you mean '{suggested}'?"

    return True, None


def clean_and_verify_email(email: Optional[str]) -> str:
    """
    Cleans and verifies an email address.
    Returns the sanitized lowercase email string.
    Raises ValueError with a user-friendly error message if not valid.
    """
    is_valid, error_msg = validate_email_address(email)
    if not is_valid:
        raise ValueError(error_msg or f"The email address '{email}' is not valid.")
    return email.strip().lower()
