from __future__ import annotations

import os
import re
from typing import Dict
from urllib.parse import quote_plus


def _base_site_url() -> str:
    return (
        os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or "https://naijataxguides.com"
    ).strip().rstrip("/")


def _backend_base_url() -> str:
    return (
        os.getenv("BACKEND_BASE_URL")
        or os.getenv("API_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or ""
    ).strip().rstrip("/")


def _whatsapp_number() -> str:
    raw = (
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("WHATSAPP_BUSINESS_PHONE_NUMBER")
        or os.getenv("WHATSAPP_PHONE_NUMBER")
        or "2347034941158"
    )
    return re.sub(r"\D+", "", raw)


def _telegram_username() -> str:
    return (os.getenv("TELEGRAM_BOT_USERNAME") or "naija_tax_guide_bot").strip().lstrip("@")


def normalize_referral_code(code: str) -> str:
    value = str(code or "").strip().upper()
    value = re.sub(r"[^A-Z0-9_-]", "", value)
    return value[:80]


def build_referral_links(referral_code: str) -> Dict[str, str]:
    code = normalize_referral_code(referral_code)
    site = _base_site_url()
    backend = _backend_base_url()

    website = f"{site}/signup?ref={quote_plus(code)}"
    smart = f"{site}/r/{quote_plus(code)}"
    whatsapp = f"https://wa.me/{_whatsapp_number()}?text={quote_plus('START REF ' + code)}"
    telegram = f"https://t.me/{_telegram_username()}?start=ref_{quote_plus(code)}"

    if backend:
        track_website = f"{backend}/api/referral/track-and-go/{quote_plus(code)}/website"
        track_whatsapp = f"{backend}/api/referral/track-and-go/{quote_plus(code)}/whatsapp"
        track_telegram = f"{backend}/api/referral/track-and-go/{quote_plus(code)}/telegram"
    else:
        track_website = website
        track_whatsapp = whatsapp
        track_telegram = telegram

    return {
        "code": code,
        "smart": smart,
        "website": website,
        "whatsapp": whatsapp,
        "telegram": telegram,
        "track_website": track_website,
        "track_whatsapp": track_whatsapp,
        "track_telegram": track_telegram,
    }


def build_referral_code_message(referral_code: str) -> str:
    links = build_referral_links(referral_code)
    code = links["code"]
    return (
        "🤝 My Referral Code\n\n"
        f"Code: {code}\n"
        f"Smart link: {links['smart']}\n\n"
        "Choose where to continue:\n"
        f"🌐 Website signup: {links['track_website']}\n"
        f"💬 WhatsApp bot: {links['track_whatsapp']}\n"
        f"✈️ Telegram bot: {links['track_telegram']}\n\n"
        "Share this code or the smart link with someone who wants Nigerian tax answers, "
        "calculators, reminders, filing guidance, or chat access.\n"
        "Referral rewards apply according to the active referral policy after a successful paid subscription.\n\n"
        "Reply R2 for only the smart link, R3 for a ready-to-share invitation, or R4 for referral statistics."
    )


def build_referral_smart_link_message(referral_code: str) -> str:
    links = build_referral_links(referral_code)
    return (
        "🔗 Referral Smart Link\n\n"
        f"{links['smart']}\n\n"
        "This one link lets the invited person choose Website, WhatsApp, or Telegram."
    )


def build_referral_invitation_message(referral_code: str) -> str:
    links = build_referral_links(referral_code)
    return (
        "You can join Naija Tax Guide through my referral link.\n\n"
        "It helps with Nigerian tax questions, calculators, deadline reminders, filing guidance, "
        "and support through Website, WhatsApp, or Telegram.\n\n"
        f"Start here: {links['smart']}\n"
        f"Referral code: {links['code']}"
    )
