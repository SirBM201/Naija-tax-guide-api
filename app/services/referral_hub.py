from __future__ import annotations

import os
import re
from urllib.parse import quote


ROUTE_VERSION = "2026-05-28-v32a-multiplatform-referral-hub"


def _clean_base_url(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "https://naijataxguides.com"
    return raw.rstrip("/")


def public_web_base_url() -> str:
    """
    Public frontend URL used inside WhatsApp/Telegram referral messages.

    Priority:
    1. PUBLIC_WEB_BASE_URL
    2. FRONTEND_BASE_URL
    3. NEXT_PUBLIC_SITE_URL
    4. https://naijataxguides.com
    """
    return _clean_base_url(
        os.getenv("PUBLIC_WEB_BASE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or "https://naijataxguides.com"
    )


def normalize_referral_code(value: str | None) -> str:
    """
    Keeps referral codes clean and safe for links/messages.
    """
    code = (value or "").strip().upper()
    return re.sub(r"[^A-Z0-9_-]", "", code)


def referral_hub_url(referral_code: str | None) -> str:
    code = normalize_referral_code(referral_code)
    return f"{public_web_base_url()}/ref/{quote(code)}"


def signup_referral_url(referral_code: str | None) -> str:
    code = normalize_referral_code(referral_code)
    return f"{public_web_base_url()}/signup?ref={quote(code)}"


def whatsapp_referral_start_text(referral_code: str | None) -> str:
    code = normalize_referral_code(referral_code)
    return f"START REF_{code}"


def telegram_referral_start_payload(referral_code: str | None) -> str:
    code = normalize_referral_code(referral_code)
    return f"ref_{code}"


def format_referral_code_message(referral_code: str | None) -> str:
    """
    R1 message: shows the user's referral code and the new multi-platform hub link.
    """
    code = normalize_referral_code(referral_code)
    hub = referral_hub_url(code)

    if not code:
        return (
            "🤝 *My Referral Code*\n\n"
            "Your referral code is not available yet.\n\n"
            "Please try again later or contact support.\n\n"
            "Reply 0 for main menu."
        )

    return (
        "🤝 *My Referral Code*\n\n"
        f"Code: {code}\n"
        f"Invite link: {hub}\n\n"
        "Your invitee can join through Website, WhatsApp, or Telegram from this link.\n\n"
        "Referral rewards apply according to the active referral policy after successful paid subscription.\n\n"
        "Reply R3 to get a ready-to-share invitation message, R4 for referral stats, or 0 for main menu."
    )


def format_referral_invite_message(referral_code: str | None) -> str:
    """
    R3 message: ready-to-share message users can forward.
    """
    code = normalize_referral_code(referral_code)
    hub = referral_hub_url(code)

    if not code:
        return (
            "Your referral code is not available yet. "
            "Please try again later or contact support."
        )

    return (
        "📢 *Ready-to-share invitation*\n\n"
        "You can now use Naija Tax Guide on Website, WhatsApp, or Telegram.\n\n"
        "Use my referral link to join:\n"
        f"{hub}\n\n"
        "Naija Tax Guide helps you ask Nigerian tax questions, check tax deadlines, "
        "use calculators, and get filing guidance.\n\n"
        f"Referral code: {code}"
    )


def extract_referral_code_from_text(text: str | None) -> str | None:
    """
    Supports future WhatsApp/Telegram referral entry formats:

    START REF_NTGR6RKUG
    /start ref_NTGR6RKUG
    JOIN NTGR6RKUG
    REF_NTGR6RKUG
    NTGR6RKUG

    This does not save anything by itself. It only extracts the code safely.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    upper = raw.upper()

    patterns = [
        r"\bREF[_\-\s]+([A-Z0-9_-]{4,40})\b",
        r"\bSTART[_\-\s]+REF[_\-\s]+([A-Z0-9_-]{4,40})\b",
        r"\bJOIN[_\-\s]+([A-Z0-9_-]{4,40})\b",
        r"^/START\s+REF[_\-\s]+([A-Z0-9_-]{4,40})\b",
        r"^/START\s+([A-Z0-9_-]{4,40})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, upper)
        if match:
            return normalize_referral_code(match.group(1))

    # Fallback: allow direct referral code only if it looks like NTG referral format.
    if re.fullmatch(r"NTG[A-Z0-9_-]{4,40}", upper):
        return normalize_referral_code(upper)

    return None
