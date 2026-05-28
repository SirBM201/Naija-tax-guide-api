from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from flask import Blueprint, jsonify, redirect, request

try:
    from supabase import create_client
except Exception:  # pragma: no cover
    create_client = None  # type: ignore


referral_hub_bp = Blueprint("referral_hub", __name__, url_prefix="/api/referral")

ROUTE_VERSION = "2026-05-28-v32b-referral-hub-tracking"


# ------------------------------------------------------------
# Environment / client helpers
# ------------------------------------------------------------
def _clean_base_url(value: Optional[str], fallback: str) -> str:
    base = (value or fallback or "").strip()
    return base.rstrip("/")


def _public_site_url() -> str:
    return _clean_base_url(
        os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or os.getenv("APP_PUBLIC_URL"),
        "https://naijataxguides.com",
    )


def _backend_base_url() -> str:
    return _clean_base_url(
        os.getenv("BACKEND_BASE_URL")
        or os.getenv("API_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or os.getenv("KOYEB_PUBLIC_URL"),
        "",
    )


def _whatsapp_bot_number() -> str:
    raw = (
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("WHATSAPP_BUSINESS_PHONE_NUMBER")
        or os.getenv("WHATSAPP_PHONE_NUMBER")
        or "2347034941158"
    )
    digits = re.sub(r"\D+", "", raw)
    return digits


def _telegram_bot_username() -> str:
    raw = os.getenv("TELEGRAM_BOT_USERNAME") or os.getenv("TELEGRAM_BOT_NAME") or "naija_tax_guide_bot"
    return raw.strip().lstrip("@")


def _normalize_ref_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    code = re.sub(r"[^A-Z0-9_-]", "", code)
    return code[:80]


def _request_ip() -> str:
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _get_supabase_client():
    """
    Tries existing project helpers first, then falls back to direct Supabase client.
    This keeps the route compatible with different versions of your backend.
    """
    for module_path, attr_name in (
        ("app.core.supabase_client", "get_supabase_client"),
        ("app.core.supabase_client", "supabase"),
        ("app.core.supabase_client", "client"),
    ):
        try:
            module = __import__(module_path, fromlist=[attr_name])
            attr = getattr(module, attr_name, None)
            if callable(attr):
                client = attr()
                if client is not None:
                    return client
            if attr is not None:
                return attr
        except Exception:
            pass

    if create_client is None:
        return None

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    )
    if not url or not key:
        return None
    return create_client(url, key)


# ------------------------------------------------------------
# Public link builder
# ------------------------------------------------------------
def build_referral_links(ref_code: str) -> Dict[str, str]:
    code = _normalize_ref_code(ref_code)
    site = _public_site_url()
    backend = _backend_base_url()
    wa_number = _whatsapp_bot_number()
    tg_username = _telegram_bot_username()

    website = f"{site}/signup?ref={quote_plus(code)}"
    smart = f"{site}/r/{quote_plus(code)}"
    whatsapp_text = quote_plus(f"START REF {code}")
    whatsapp = f"https://wa.me/{wa_number}?text={whatsapp_text}"
    telegram = f"https://t.me/{tg_username}?start=ref_{quote_plus(code)}"

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


def _platform_destination(ref_code: str, platform: str) -> str:
    links = build_referral_links(ref_code)
    key = (platform or "website").strip().lower()
    if key in {"web", "website", "site", "signup"}:
        return links["website"]
    if key in {"wa", "whatsapp"}:
        return links["whatsapp"]
    if key in {"tg", "telegram"}:
        return links["telegram"]
    if key in {"smart", "hub"}:
        return links["smart"]
    return links["website"]


# ------------------------------------------------------------
# Tracking
# ------------------------------------------------------------
def _safe_insert_referral_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    sb = _get_supabase_client()
    if sb is None:
        return {"ok": False, "error": "supabase_client_unavailable"}

    try:
        row = {
            "referral_code": _normalize_ref_code(payload.get("referral_code")),
            "event_type": str(payload.get("event_type") or "hub_view")[:80],
            "selected_platform": (payload.get("selected_platform") or None),
            "visitor_token": (payload.get("visitor_token") or None),
            "channel_type": (payload.get("channel_type") or None),
            "provider_user_id": (payload.get("provider_user_id") or None),
            "account_id": (payload.get("account_id") or None),
            "landing_url": (payload.get("landing_url") or None),
            "user_agent": (payload.get("user_agent") or request.headers.get("user-agent") or None),
            "ip_address": (payload.get("ip_address") or _request_ip() or None),
            "metadata": payload.get("metadata") or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Remove blank account_id to avoid UUID cast failures.
        if not row.get("account_id"):
            row.pop("account_id", None)
        result = sb.table("referral_hub_events").insert(row).execute()
        return {"ok": True, "inserted": True, "data": getattr(result, "data", None)}
    except Exception as exc:
        # Tracking must never break referral routing.
        return {"ok": False, "error": str(exc)[:500]}


# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------
@referral_hub_bp.get("/health")
def referral_hub_health():
    return jsonify({"ok": True, "route_version": ROUTE_VERSION})


@referral_hub_bp.get("/hub/<ref_code>")
def referral_hub_payload(ref_code: str):
    code = _normalize_ref_code(ref_code)
    if not code:
        return jsonify({"ok": False, "error": "missing_referral_code", "route_version": ROUTE_VERSION}), 400

    links = build_referral_links(code)
    tracking_result = _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": "hub_view",
            "selected_platform": request.args.get("source") or request.args.get("platform"),
            "visitor_token": request.args.get("visitor_token"),
            "landing_url": request.args.get("landing_url") or request.referrer,
            "metadata": {
                "query": dict(request.args),
                "route": "/api/referral/hub/<ref_code>",
            },
        }
    )

    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "referral_code": code,
            "links": links,
            "tracking": {"ok": bool(tracking_result.get("ok"))},
        }
    )


@referral_hub_bp.post("/track")
def referral_hub_track():
    body = request.get_json(silent=True) or {}
    code = _normalize_ref_code(body.get("referral_code") or body.get("code"))
    platform = str(body.get("selected_platform") or body.get("platform") or "").strip().lower()

    if not code:
        return jsonify({"ok": False, "error": "missing_referral_code", "route_version": ROUTE_VERSION}), 400

    event_type = str(body.get("event_type") or "platform_select")[:80]
    tracking_result = _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": event_type,
            "selected_platform": platform or None,
            "visitor_token": body.get("visitor_token"),
            "channel_type": body.get("channel_type"),
            "provider_user_id": body.get("provider_user_id"),
            "account_id": body.get("account_id"),
            "landing_url": body.get("landing_url") or request.referrer,
            "metadata": {
                "body": body,
                "route": "/api/referral/track",
            },
        }
    )

    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "referral_code": code,
            "selected_platform": platform,
            "destination": _platform_destination(code, platform),
            "tracking": {"ok": bool(tracking_result.get("ok"))},
        }
    )


@referral_hub_bp.get("/track-and-go/<ref_code>/<platform>")
def referral_track_and_go(ref_code: str, platform: str):
    code = _normalize_ref_code(ref_code)
    chosen = str(platform or "website").strip().lower()
    if not code:
        return redirect(_public_site_url(), code=302)

    _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": "platform_click",
            "selected_platform": chosen,
            "visitor_token": request.args.get("visitor_token"),
            "landing_url": request.referrer,
            "metadata": {
                "query": dict(request.args),
                "route": "/api/referral/track-and-go/<ref_code>/<platform>",
            },
        }
    )

    return redirect(_platform_destination(code, chosen), code=302)
