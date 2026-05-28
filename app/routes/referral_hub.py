# app/routes/referral_hub.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from flask import Blueprint, jsonify, redirect, request

try:
    from app.core.supabase_client import get_supabase_client
except Exception:  # pragma: no cover
    get_supabase_client = None  # type: ignore


bp = Blueprint("referral_hub", __name__)

ROUTE_VERSION = "2026-05-28-v32b1-referral-hub-get-method-fix"


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------
def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_base_url(value: Optional[str], fallback: str) -> str:
    raw = _clean(value) or fallback
    return raw.rstrip("/")


def _public_site_url() -> str:
    return _clean_base_url(
        os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or os.getenv("APP_PUBLIC_URL")
        or os.getenv("APP_BASE_URL"),
        "https://naijataxguides.com",
    )


def _backend_base_url() -> str:
    # Used only for optional tracking redirects.
    return _clean_base_url(
        os.getenv("BACKEND_BASE_URL")
        or os.getenv("API_BASE_URL")
        or os.getenv("KOYEB_PUBLIC_URL"),
        "",
    )


def _whatsapp_bot_number() -> str:
    raw = (
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("WHATSAPP_BUSINESS_PHONE_NUMBER")
        or os.getenv("WHATSAPP_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_DISPLAY_PHONE_NUMBER")
        or "2347034941158"
    )
    digits = re.sub(r"\D+", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def _telegram_bot_username() -> str:
    raw = (
        os.getenv("TELEGRAM_BOT_USERNAME")
        or os.getenv("TELEGRAM_BOT_NAME")
        or os.getenv("TG_BOT_USERNAME")
        or "naija_tax_guide_bot"
    )
    username = raw.strip().lstrip("@")
    username = re.sub(r"[^A-Za-z0-9_]+", "", username)
    return username or "naija_tax_guide_bot"


def _normalize_ref_code(value: Any) -> str:
    code = _clean(value).upper()
    code = re.sub(r"[^A-Z0-9_-]+", "", code)
    return code[:80]


def _request_ip() -> str:
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _sb():
    if get_supabase_client is None:
        return None
    try:
        return get_supabase_client(admin=True)
    except TypeError:
        try:
            return get_supabase_client()
        except Exception:
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------
# Link builder
# ---------------------------------------------------------------------
def build_referral_links(ref_code: Any) -> Dict[str, str]:
    code = _normalize_ref_code(ref_code)
    site = _public_site_url()
    backend = _backend_base_url()

    website = f"{site}/signup?ref={quote_plus(code)}"
    smart = f"{site}/r/{quote_plus(code)}"

    wa_number = _whatsapp_bot_number()
    whatsapp_text = quote_plus(f"START REF {code}")
    whatsapp = f"https://wa.me/{wa_number}?text={whatsapp_text}" if wa_number else website

    tg_username = _telegram_bot_username()
    telegram = f"https://t.me/{tg_username}?start=ref_{quote_plus(code)}" if tg_username else website

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


def _platform_destination(ref_code: Any, platform: Any) -> str:
    links = build_referral_links(ref_code)
    chosen = _clean(platform).lower()

    if chosen in {"web", "website", "site", "signup"}:
        return links["website"]
    if chosen in {"wa", "whatsapp"}:
        return links["whatsapp"]
    if chosen in {"tg", "telegram"}:
        return links["telegram"]
    if chosen in {"smart", "hub"}:
        return links["smart"]

    return links["website"]


# ---------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------
def _safe_insert_referral_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tracking must never break the public referral hub.
    If the SQL table is missing or Supabase is unavailable, the route still works.
    """
    sb = _sb()
    if sb is None:
        return {"ok": False, "error": "supabase_client_unavailable"}

    try:
        row: Dict[str, Any] = {
            "referral_code": _normalize_ref_code(payload.get("referral_code")),
            "event_type": _clean(payload.get("event_type") or "hub_view")[:80],
            "selected_platform": _clean(payload.get("selected_platform")) or None,
            "visitor_token": _clean(payload.get("visitor_token")) or None,
            "channel_type": _clean(payload.get("channel_type")) or None,
            "provider_user_id": _clean(payload.get("provider_user_id")) or None,
            "account_id": _clean(payload.get("account_id")) or None,
            "landing_url": _clean(payload.get("landing_url")) or None,
            "user_agent": _clean(payload.get("user_agent") or request.headers.get("user-agent")) or None,
            "ip_address": _clean(payload.get("ip_address") or _request_ip()) or None,
            "metadata": payload.get("metadata") or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Avoid UUID cast failures when blank.
        if not row.get("account_id"):
            row.pop("account_id", None)

        res = sb.table("referral_hub_events").insert(row).execute()
        return {"ok": True, "inserted": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)[:700]}


# ---------------------------------------------------------------------
# Routes
# Important:
# This project registers route modules under /api from app/__init__.py.
# Therefore this blueprint has NO url_prefix.
# Final URLs:
#   GET  /api/referral/health
#   GET  /api/referral/hub/<code>
#   POST /api/referral/track
#   GET  /api/referral/track-and-go/<code>/<platform>
# ---------------------------------------------------------------------
@bp.get("/referral/health")
def referral_hub_health():
    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "message": "Referral hub route is active.",
            "expected_urls": [
                "/api/referral/health",
                "/api/referral/hub/NTGR6RKUG",
                "/api/referral/track-and-go/NTGR6RKUG/website",
                "/api/referral/track-and-go/NTGR6RKUG/whatsapp",
                "/api/referral/track-and-go/NTGR6RKUG/telegram",
            ],
        }
    ), 200


@bp.get("/referral/hub/<ref_code>")
def referral_hub_payload(ref_code: str):
    code = _normalize_ref_code(ref_code)
    if not code:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "missing_referral_code",
            }
        ), 400

    links = build_referral_links(code)

    tracking = _safe_insert_referral_event(
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
            "tracking": {
                "ok": bool(tracking.get("ok")),
                "non_blocking": True,
            },
        }
    ), 200


@bp.route("/referral/track", methods=["GET", "POST"])
def referral_hub_track():
    if request.method == "GET":
        code = _normalize_ref_code(request.args.get("referral_code") or request.args.get("code"))
        platform = _clean(request.args.get("selected_platform") or request.args.get("platform")).lower()
        event_type = _clean(request.args.get("event_type") or "platform_select")[:80]
        body: Dict[str, Any] = {}
    else:
        body = request.get_json(silent=True) or {}
        code = _normalize_ref_code(body.get("referral_code") or body.get("code"))
        platform = _clean(body.get("selected_platform") or body.get("platform")).lower()
        event_type = _clean(body.get("event_type") or "platform_select")[:80]

    if not code:
        return jsonify(
            {
                "ok": False,
                "route_version": ROUTE_VERSION,
                "error": "missing_referral_code",
            }
        ), 400

    tracking = _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": event_type,
            "selected_platform": platform or None,
            "visitor_token": body.get("visitor_token") if body else request.args.get("visitor_token"),
            "channel_type": body.get("channel_type") if body else request.args.get("channel_type"),
            "provider_user_id": body.get("provider_user_id") if body else request.args.get("provider_user_id"),
            "account_id": body.get("account_id") if body else request.args.get("account_id"),
            "landing_url": (body.get("landing_url") if body else request.args.get("landing_url")) or request.referrer,
            "metadata": {
                "method": request.method,
                "query": dict(request.args),
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
            "tracking": {
                "ok": bool(tracking.get("ok")),
                "non_blocking": True,
            },
        }
    ), 200


@bp.get("/referral/track-and-go/<ref_code>/<platform>")
def referral_track_and_go(ref_code: str, platform: str):
    code = _normalize_ref_code(ref_code)
    chosen = _clean(platform).lower()

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

    return redirect(_platform_destination(code, chosen), code=302
    )
