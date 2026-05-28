# app/services/referral_hub.py
from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote_plus, urlencode, urlsplit, urlunsplit


REFERRAL_HUB_VERSION = "2026-05-28-batch32d-whatsapp-referral-parser-hardening"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_first(*names: str) -> str:
    for name in names:
        value = _clean(os.getenv(name))
        if value:
            return value
    return ""


def _public_site_url() -> str:
    # Important: use frontend/public site first, not backend APP_BASE_URL.
    return (
        _env_first(
            "PUBLIC_SITE_URL",
            "FRONTEND_BASE_URL",
            "NEXT_PUBLIC_SITE_URL",
            "NEXT_PUBLIC_APP_URL",
            "PUBLIC_APP_URL",
        )
        or "https://www.naijataxguides.com"
    ).rstrip("/")


def _backend_base_url() -> str:
    return _env_first(
        "BACKEND_BASE_URL",
        "API_BASE_URL",
        "NEXT_PUBLIC_API_BASE_URL",
        "KOYEB_PUBLIC_URL",
    ).rstrip("/")


def _safe_base_url() -> str:
    return _public_site_url()


def _sanitize_code(code: Any) -> str:
    value = _clean(code).upper()
    value = re.sub(r"[^A-Z0-9_-]+", "", value)
    return value[:80]


def _add_query(url: str, **params: str) -> str:
    url = _clean(url)
    if not url:
        return ""

    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    for key, value in params.items():
        value = _clean(value)
        if value:
            query[key] = value

    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _format_hub_url_from_env(code: str) -> str:
    """
    Optional env support:
      NTG_REFERRAL_HUB_URL=https://www.naijataxguides.com/ref/{code}
      or
      NTG_REFERRAL_HUB_URL=https://www.naijataxguides.com/referral

    Batch 32C default: now that the frontend hub exists, fall back to /ref/{code},
    not /signup?ref=code.
    """
    template = _env_first("NTG_REFERRAL_HUB_URL", "REFERRAL_HUB_URL", "PUBLIC_REFERRAL_HUB_URL")
    if template:
        if "{code}" in template:
            return template.replace("{code}", quote(code))
        return _add_query(template, ref=code, source="referral_hub")

    return f"{_safe_base_url()}/ref/{quote(code)}"


def _web_signup_link(code: str, primary_link: Optional[str] = None) -> str:
    primary = _clean(primary_link)
    if primary.startswith("http"):
        return primary
    if primary.startswith("/"):
        return f"{_safe_base_url()}{primary}"
    return f"{_safe_base_url()}/signup?ref={quote(code)}"


def _whatsapp_bot_phone() -> str:
    phone = _env_first(
        "WHATSAPP_BOT_PHONE_NUMBER",
        "META_WHATSAPP_BOT_PHONE",
        "WHATSAPP_DISPLAY_PHONE_NUMBER",
        "META_WHATSAPP_DISPLAY_PHONE_NUMBER",
        "WHATSAPP_BUSINESS_PHONE_NUMBER",
        "WHATSAPP_PHONE_NUMBER",
    ) or "2347034941158"
    phone = re.sub(r"\D+", "", phone)
    if phone.startswith("00"):
        phone = phone[2:]
    return phone


def _telegram_bot_username() -> str:
    username = (
        _env_first("TELEGRAM_BOT_USERNAME", "TG_BOT_USERNAME", "TELEGRAM_USERNAME", "TG_USERNAME")
        or "naija_tax_guide_bot"
    )
    username = username.lstrip("@").strip()
    username = re.sub(r"[^A-Za-z0-9_]+", "", username)
    return username


def _track_and_go_link(code: str, platform: str, fallback: str) -> str:
    backend = _backend_base_url()
    if not backend:
        return fallback
    return f"{backend}/api/referral/track-and-go/{quote(code)}/{quote(platform)}"


def build_referral_platform_links(code: Any, primary_link: Optional[str] = None) -> dict[str, str]:
    """
    Build safe, shareable platform entry links for the same referral code.

    Batch 32C:
    - `hub`/`smart` now points to the public referral hub page.
    - `web` remains direct signup fallback.
    - `track_*` links go through backend analytics when BACKEND_BASE_URL/API_BASE_URL is configured.
    """
    ref_code = _sanitize_code(code)
    hub_link = _format_hub_url_from_env(ref_code)
    web_link = _web_signup_link(ref_code, primary_link=primary_link)

    wa_phone = _whatsapp_bot_phone()
    wa_text = f"START REF {ref_code}"
    whatsapp_link = f"https://wa.me/{wa_phone}?text={quote(wa_text)}" if wa_phone else ""

    tg_username = _telegram_bot_username()
    telegram_link = f"https://t.me/{tg_username}?start=ref_{quote(ref_code)}" if tg_username else ""

    return {
        "code": ref_code,
        "hub": hub_link,
        "smart": hub_link,
        "web": web_link,
        "website": web_link,
        "whatsapp": whatsapp_link,
        "telegram": telegram_link,
        "track_web": _track_and_go_link(ref_code, "website", web_link),
        "track_website": _track_and_go_link(ref_code, "website", web_link),
        "track_whatsapp": _track_and_go_link(ref_code, "whatsapp", whatsapp_link or web_link),
        "track_telegram": _track_and_go_link(ref_code, "telegram", telegram_link or web_link),
        "whatsapp_fallback_text": wa_text,
        "telegram_fallback_text": f"START REF {ref_code}",
    }


def _platform_lines(links: dict[str, str]) -> str:
    lines = [
        f"🌐 Smart referral hub: {links.get('hub') or links.get('web')}",
        f"🖥️ Website signup: {links.get('web') or links.get('hub')}",
    ]

    if links.get("whatsapp"):
        lines.append(f"💬 WhatsApp bot: {links['whatsapp']}")
    else:
        lines.append(f"💬 WhatsApp bot: open the bot and send: {links.get('whatsapp_fallback_text')}")

    if links.get("telegram"):
        lines.append(f"✈️ Telegram bot: {links['telegram']}")
    else:
        lines.append(f"✈️ Telegram bot: open the bot and send: {links.get('telegram_fallback_text')}")

    return "\n".join(lines)


def format_referral_menu_message(code: Any = "", link: Optional[str] = None, *, channel: str = "generic") -> str:
    links = build_referral_platform_links(code or "YOURCODE", primary_link=link)
    return (
        "🤝 *Referral Centre*\n\n"
        "R1 - My referral code + platform links\n"
        "R2 - Smart referral link\n"
        "R3 - Ready-to-share invitation\n"
        "R4 - Referral statistics\n"
        "R5 - Referral rewards\n"
        "R6 - Payout status\n\n"
        "Platform choice link set:\n"
        f"{_platform_lines(links)}\n\n"
        "Reply with R1, R2, R3, R4, R5, or R6.\n"
        "Reply 0 for main menu."
    )


def format_referral_code_message(
    code: Any,
    link: Optional[str] = None,
    *,
    channel: str = "generic",
    err: Optional[str] = None,
) -> str:
    links = build_referral_platform_links(code, primary_link=link)
    warning = "\n\nNote: I used a safe fallback code because the referral profile could not be fully refreshed." if err else ""

    return (
        "🤝 *My Referral Code*\n\n"
        f"Code: {links['code']}\n"
        f"Smart link: {links['hub']}\n\n"
        "Choose where to continue:\n"
        f"{_platform_lines(links)}\n\n"
        "Share this code or the smart link with someone who wants Nigerian tax answers, calculators, reminders, filing guidance, or chat access.\n"
        "Referral rewards apply according to the active referral policy after a successful paid subscription."
        f"{warning}\n\n"
        "Reply R2 for only the smart link, R3 for a ready-to-share invitation, or R4 for referral statistics."
    )


def format_referral_link_message(
    code: Any,
    link: Optional[str] = None,
    *,
    channel: str = "generic",
) -> str:
    links = build_referral_platform_links(code, primary_link=link)
    return (
        "🔗 *Smart Referral Link*\n\n"
        f"{links['hub']}\n\n"
        "This link lets the invited user choose Website, WhatsApp, or Telegram from one simple hub.\n\n"
        "Reply R1 for all platform links, R3 for a ready-to-share invitation, or 0 for main menu."
    )


def format_referral_invite_message(
    code: Any,
    link: Optional[str] = None,
    *,
    channel: str = "generic",
) -> str:
    links = build_referral_platform_links(code, primary_link=link)

    return (
        "📣 *Referral Invitation*\n\n"
        "Copy and share this message:\n\n"
        "Hi, I use Naija Tax Guide for Nigerian tax questions, calculators, filing guidance, reminders, and chat support.\n\n"
        "Join through this smart referral hub and choose Website, WhatsApp, or Telegram:\n"
        f"{links['hub']}\n\n"
        f"Referral code: {links['code']}\n\n"
        "Direct options are also available:\n"
        f"{_platform_lines(links)}\n\n"
        "After signup, you can use the website, WhatsApp, and Telegram channels where supported."
    )


def format_referral_landing_message(
    code: Any,
    link: Optional[str] = None,
    *,
    channel: str = "generic",
) -> str:
    links = build_referral_platform_links(code, primary_link=link)

    return (
        "🎉 *Welcome to Naija Tax Guide*\n\n"
        f"You were invited with referral code: {links['code']}.\n\n"
        "Choose where you want to continue:\n"
        f"{_platform_lines(links)}\n\n"
        "For the referral reward to track correctly, use the smart hub or website signup link and keep the referral code during signup.\n\n"
        "Reply 0 for the main menu."
    )


def _decode_user_text_once_or_twice(value: str) -> str:
    """
    WhatsApp deep links can deliver:
      START REF NTGR6RKUG
      START%20REF%20NTGR6RKUG
      START+REF+NTGR6RKUG

    Decode gently without breaking normal typed commands.
    """
    text = _clean(value)
    if not text:
        return ""

    decoded = text
    for _ in range(2):
        try:
            candidate = unquote_plus(decoded)
        except Exception:
            break
        if candidate == decoded:
            break
        decoded = candidate

    return decoded.strip()


def _first_valid_referral_code_from_text(value: str) -> str:
    """
    Extract the first valid referral code from a noisy referral start string.

    This intentionally uses search-based matching, not only full-line matching, because
    WhatsApp sometimes duplicates the prefilled text, for example:
      START REF NTGR6RKUGSTART REF NTGR6RKUG
    """
    text = _decode_user_text_once_or_twice(value)
    if not text:
        return ""

    # Normalize common separators while preserving code characters.
    normalized = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Add spacing between a code and an accidentally repeated START token:
    # NTGR6RKUGSTART REF NTGR6RKUG -> NTGR6RKUG START REF NTGR6RKUG
    normalized = re.sub(r"([A-Za-z0-9_-]{4,80})(START\s+REF\b)", r"\1 \2", normalized, flags=re.I)
    normalized = re.sub(r"([A-Za-z0-9_-]{4,80})(REF\b)", r"\1 \2", normalized, flags=re.I)

    patterns = [
        r"(?:^|\b)/start\s+(?:ref|referral|r|ntgref)?[_:\-\s]+([A-Za-z0-9_-]{4,80})(?=\b|$)",
        r"(?:^|\b)(?:start\s+)?(?:ref|referral|ntgref)[_:\-\s]+([A-Za-z0-9_-]{4,80})(?=\b|$)",
        r"(?:^|\b)(?:join|invite)[_:\-\s]+([A-Za-z0-9_-]{4,80})(?=\b|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.I)
        if match:
            code = _sanitize_code(match.group(1))
            if len(code) >= 4:
                return code

    return ""


def extract_referral_start_code(text: Any) -> str:
    """
    Accept direct bot referral starts:
      /start ref_NTGR6RKUG
      /start ref NTGR6RKUG
      START REF NTGR6RKUG
      START%20REF%20NTGR6RKUG
      REF NTGR6RKUG
      JOIN NTGR6RKUG

    Batch 32D:
    - Handles WhatsApp duplicated prefilled text:
        START REF NTGR6RKUGSTART REF NTGR6RKUG
    - Handles URL-encoded and plus-encoded text.
    - Uses first valid referral token only, so duplicate text cannot create a bad code.
    """
    raw = _clean(text)
    if not raw:
        return ""

    return _first_valid_referral_code_from_text(raw)
