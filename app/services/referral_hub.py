# app/services/referral_hub.py
from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl


REFERRAL_HUB_VERSION = "2026-05-28-batch32a-multi-platform-referral-hub"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _env_first(*names: str) -> str:
    for name in names:
        value = _clean(os.getenv(name))
        if value:
            return value
    return ""


def _base_url() -> str:
    return _env_first("APP_BASE_URL", "FRONTEND_BASE_URL", "PUBLIC_APP_URL", "NEXT_PUBLIC_APP_URL") or "https://www.naijataxguides.com"


def _safe_base_url() -> str:
    return _base_url().rstrip("/")


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

    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(query),
            split.fragment,
        )
    )


def _format_hub_url_from_env(code: str) -> str:
    """
    Optional env support:
      NTG_REFERRAL_HUB_URL=https://www.naijataxguides.com/referral
      or
      NTG_REFERRAL_HUB_URL=https://www.naijataxguides.com/referral?ref={code}

    If the frontend hub page is not ready, this service safely falls back to
    the existing signup link so the link never breaks.
    """
    template = _env_first("NTG_REFERRAL_HUB_URL", "REFERRAL_HUB_URL", "PUBLIC_REFERRAL_HUB_URL")
    if not template:
        return ""

    if "{code}" in template:
        return template.replace("{code}", quote(code))

    return _add_query(template, ref=code, source="referral_hub")


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
    )
    phone = re.sub(r"\D+", "", phone)
    if phone.startswith("00"):
        phone = phone[2:]
    return phone


def _telegram_bot_username() -> str:
    username = _env_first("TELEGRAM_BOT_USERNAME", "TG_BOT_USERNAME", "TELEGRAM_USERNAME", "TG_USERNAME")
    username = username.lstrip("@").strip()
    username = re.sub(r"[^A-Za-z0-9_]+", "", username)
    return username


def build_referral_platform_links(code: Any, primary_link: Optional[str] = None) -> dict[str, str]:
    """
    Build safe, shareable platform entry links for the same referral code.

    The web signup link remains the safest universal fallback.
    WhatsApp and Telegram links are only shown when their public bot details
    are available in environment variables.
    """
    ref_code = _sanitize_code(code)
    web_link = _web_signup_link(ref_code, primary_link=primary_link)
    hub_link = _format_hub_url_from_env(ref_code) or web_link

    wa_phone = _whatsapp_bot_phone()
    wa_text = f"START REF {ref_code}"
    whatsapp_link = f"https://wa.me/{wa_phone}?text={quote(wa_text)}" if wa_phone else ""

    tg_username = _telegram_bot_username()
    telegram_link = f"https://t.me/{tg_username}?start=ref_{quote(ref_code)}" if tg_username else ""

    return {
        "code": ref_code,
        "hub": hub_link,
        "web": web_link,
        "whatsapp": whatsapp_link,
        "telegram": telegram_link,
        "whatsapp_fallback_text": wa_text,
        "telegram_fallback_text": f"START REF {ref_code}",
    }


def _platform_lines(links: dict[str, str]) -> str:
    lines = [
        f"🌐 Website signup: {links.get('web') or links.get('hub')}",
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
        "Share this code or any of the links with someone who wants Nigerian tax answers, calculators, reminders, filing guidance, or chat access.\n"
        "Referral rewards apply according to the active referral policy after a successful paid subscription.\n\n"
        "Reply R2 for only the smart link, R3 for a ready-to-share invitation, or R4 for referral statistics."
        f"{warning}"
    )


def format_referral_link_message(
    code: Any,
    link: Optional[str] = None,
    *,
    channel: str = "generic",
    err: Optional[str] = None,
) -> str:
    links = build_referral_platform_links(code, primary_link=link)
    warning = "\n\nNote: I used a safe fallback link because the referral profile could not be fully refreshed." if err else ""

    return (
        "🔗 *My Smart Referral Link*\n\n"
        f"{links['hub']}\n\n"
        f"Referral code: {links['code']}\n\n"
        "Platform options:\n"
        f"{_platform_lines(links)}\n\n"
        "Copy and share with friends, colleagues, small business owners, or tax learners."
        f"{warning}"
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
        "You can join through any platform you prefer:\n"
        f"{_platform_lines(links)}\n\n"
        f"Referral code: {links['code']}\n\n"
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
        "For the referral reward to track correctly, use the website signup link or keep the referral code during signup.\n\n"
        "Reply 0 for the main menu."
    )


def extract_referral_start_code(text: Any) -> str:
    """
    Accept direct bot referral starts:
      /start ref_NTGR6RKUG
      START REF NTGR6RKUG
      REF NTGR6RKUG
      JOIN NTGR6RKUG
    """
    raw = _clean(text)
    if not raw:
        return ""

    patterns = [
        r"^/start\s+(?:ref|referral|r|ntgref)[_:\-\s]+([A-Za-z0-9_-]{4,80})$",
        r"^/start\s+([A-Za-z0-9_-]{4,80})$",
        r"^(?:start\s+)?(?:ref|referral|ntgref)[_:\-\s]+([A-Za-z0-9_-]{4,80})$",
        r"^(?:join|invite)\s+([A-Za-z0-9_-]{4,80})$",
    ]

    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.I)
        if match:
            return _sanitize_code(match.group(1))

    return ""
