# app/routes/referral_hub.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, quote_plus

from flask import Blueprint, jsonify, redirect, request

try:
    from app.core.supabase_client import get_supabase_client
except Exception:  # pragma: no cover
    get_supabase_client = None  # type: ignore

try:
    from app.core.supabase_client import supabase as shared_supabase
except Exception:  # pragma: no cover
    shared_supabase = None  # type: ignore


bp = Blueprint("referral_hub", __name__)

ROUTE_VERSION = "2026-05-28-v32c-referral-tracking-landing-url-and-smart-hub"


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------
def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_base_url(value: Optional[str], fallback: str) -> str:
    raw = _clean(value) or fallback
    return raw.rstrip("/")


def _public_site_url() -> str:
    """
    Public frontend domain used for signup and smart referral hub links.
    Prefer www because the live web app is usually opened on www.naijataxguides.com.
    """
    return _clean_base_url(
        os.getenv("PUBLIC_SITE_URL")
        or os.getenv("FRONTEND_BASE_URL")
        or os.getenv("NEXT_PUBLIC_SITE_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or os.getenv("PUBLIC_APP_URL"),
        "https://www.naijataxguides.com",
    )


def _backend_base_url() -> str:
    """
    Public backend base used for track-and-go redirect links.
    If not set, build from the current request so local and production both work.
    """
    configured = _clean_base_url(
        os.getenv("BACKEND_BASE_URL")
        or os.getenv("API_BASE_URL")
        or os.getenv("NEXT_PUBLIC_API_BASE_URL")
        or os.getenv("KOYEB_PUBLIC_URL"),
        "",
    )
    if configured:
        return configured

    try:
        return request.url_root.rstrip("/")
    except Exception:
        return ""


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
        or os.getenv("TELEGRAM_USERNAME")
        or "naija_tax_guide_bot"
    )
    username = raw.strip().lstrip("@")
    username = re.sub(r"[^A-Za-z0-9_]+", "", username)
    return username or "naija_tax_guide_bot"


def _normalize_ref_code(value: Any) -> str:
    code = _clean(value).upper()
    code = re.sub(r"[^A-Z0-9_-]+", "", code)
    return code[:80]


def _normalize_platform(value: Any) -> str:
    platform = _clean(value).lower()
    platform = re.sub(r"[^a-z0-9_-]+", "", platform)
    aliases = {
        "web": "website",
        "site": "website",
        "signup": "website",
        "wa": "whatsapp",
        "whats": "whatsapp",
        "tg": "telegram",
        "telegrambot": "telegram",
    }
    return aliases.get(platform, platform)[:40]


def _request_ip() -> str:
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _sb():
    if get_supabase_client is not None:
        try:
            return get_supabase_client(admin=True)
        except TypeError:
            try:
                return get_supabase_client()
            except Exception:
                pass
        except Exception:
            pass

    if shared_supabase is not None:
        return shared_supabase

    return None


# ---------------------------------------------------------------------
# Link builder
# ---------------------------------------------------------------------
def build_referral_links(ref_code: Any) -> Dict[str, str]:
    code = _normalize_ref_code(ref_code)
    site = _public_site_url()
    backend = _backend_base_url()
    encoded_code_path = quote(code, safe="")
    encoded_code_query = quote_plus(code)

    website = f"{site}/signup?ref={encoded_code_query}"

    # Primary smart hub path. We also keep /r for backward compatibility.
    smart = f"{site}/ref/{encoded_code_path}"
    smart_r = f"{site}/r/{encoded_code_path}"

    wa_number = _whatsapp_bot_number()
    whatsapp_text = quote_plus(f"START REF {code}")
    whatsapp = f"https://wa.me/{wa_number}?text={whatsapp_text}" if wa_number else website

    tg_username = _telegram_bot_username()
    telegram = f"https://t.me/{tg_username}?start=ref_{encoded_code_query}" if tg_username else website

    if backend:
        track_website = f"{backend}/api/referral/track-and-go/{encoded_code_path}/website"
        track_whatsapp = f"{backend}/api/referral/track-and-go/{encoded_code_path}/whatsapp"
        track_telegram = f"{backend}/api/referral/track-and-go/{encoded_code_path}/telegram"
    else:
        track_website = website
        track_whatsapp = whatsapp
        track_telegram = telegram

    return {
        "code": code,
        "smart": smart,
        "smart_ref": smart,
        "smart_r": smart_r,
        "website": website,
        "web": website,
        "whatsapp": whatsapp,
        "telegram": telegram,
        "track_website": track_website,
        "track_web": track_website,
        "track_whatsapp": track_whatsapp,
        "track_telegram": track_telegram,
    }


def _platform_destination(ref_code: Any, platform: Any) -> str:
    links = build_referral_links(ref_code)
    chosen = _normalize_platform(platform)

    if chosen == "website":
        return links["website"]
    if chosen == "whatsapp":
        return links["whatsapp"]
    if chosen == "telegram":
        return links["telegram"]
    if chosen in {"smart", "hub", "ref", "r"}:
        return links["smart"]

    return links["website"]


def _safe_landing_url(value: Any, fallback: str = "") -> str:
    text = _clean(value)
    if text.startswith("http://") or text.startswith("https://"):
        return text[:1200]
    return _clean(fallback)[:1200]


# ---------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------
def _safe_insert_referral_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tracking must never break the public referral hub.
    If the SQL table is missing or Supabase is unavailable, the route still works.
    Full insert is attempted first; if old columns are missing, we retry using the
    minimum columns already proven in production.
    """
    sb = _sb()
    if sb is None:
        return {"ok": False, "error": "supabase_client_unavailable"}

    code = _normalize_ref_code(payload.get("referral_code"))
    if not code:
        return {"ok": False, "error": "missing_referral_code"}

    now_iso = datetime.now(timezone.utc).isoformat()
    landing_url = _safe_landing_url(payload.get("landing_url"))
    selected_platform = _normalize_platform(payload.get("selected_platform")) or None

    full_row: Dict[str, Any] = {
        "referral_code": code,
        "event_type": _clean(payload.get("event_type") or "hub_view")[:80],
        "selected_platform": selected_platform,
        "landing_url": landing_url or None,
        "visitor_token": _clean(payload.get("visitor_token")) or None,
        "channel_type": _clean(payload.get("channel_type")) or None,
        "provider_user_id": _clean(payload.get("provider_user_id")) or None,
        "account_id": _clean(payload.get("account_id")) or None,
        "user_agent": _clean(payload.get("user_agent") or request.headers.get("user-agent"))[:1000] or None,
        "ip_address": _clean(payload.get("ip_address") or _request_ip())[:120] or None,
        "metadata": payload.get("metadata") or {},
        "created_at": now_iso,
    }

    if not full_row.get("account_id"):
        full_row.pop("account_id", None)

    minimal_row: Dict[str, Any] = {
        "referral_code": full_row["referral_code"],
        "event_type": full_row["event_type"],
        "selected_platform": full_row.get("selected_platform"),
        "landing_url": full_row.get("landing_url"),
        "created_at": full_row["created_at"],
    }

    try:
        res = sb.table("referral_hub_events").insert(full_row).execute()
        return {"ok": True, "inserted": True, "mode": "full", "data": getattr(res, "data", None)}
    except Exception as exc_full:
        try:
            res = sb.table("referral_hub_events").insert(minimal_row).execute()
            return {
                "ok": True,
                "inserted": True,
                "mode": "minimal_fallback",
                "full_error": repr(exc_full)[:500],
                "data": getattr(res, "data", None),
            }
        except Exception as exc_min:
            return {
                "ok": False,
                "error": repr(exc_min)[:700],
                "full_error": repr(exc_full)[:700],
            }


# ---------------------------------------------------------------------
# Routes
# Important:
# This project registers route modules under /api from app/__init__.py.
# Therefore this blueprint has NO url_prefix.
# Final URLs:
#   GET  /api/referral/health
#   GET  /api/referral/hub/<code>
#   GET  /api/referral/track
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
            "public_site_url": _public_site_url(),
            "backend_base_url": _backend_base_url(),
            "expected_urls": [
                "/api/referral/health",
                "/api/referral/hub/NTGR6RKUG",
                "/api/referral/track?code=NTGR6RKUG&platform=website",
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
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "missing_referral_code"}), 400

    links = build_referral_links(code)
    current_url = _safe_landing_url(request.url)

    tracking = _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": "hub_view",
            "selected_platform": request.args.get("source") or request.args.get("platform"),
            "visitor_token": request.args.get("visitor_token"),
            "landing_url": current_url,
            "metadata": {
                "query": dict(request.args),
                "route": "/api/referral/hub/<ref_code>",
                "referrer": request.referrer,
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
                "mode": tracking.get("mode"),
                "non_blocking": True,
            },
        }
    ), 200


@bp.route("/referral/track", methods=["GET", "POST"])
def referral_hub_track():
    if request.method == "GET":
        body: Dict[str, Any] = {}
        code = _normalize_ref_code(request.args.get("referral_code") or request.args.get("code"))
        platform = _normalize_platform(request.args.get("selected_platform") or request.args.get("platform"))
        event_type = _clean(request.args.get("event_type") or "platform_select")[:80]
    else:
        body = request.get_json(silent=True) or {}
        code = _normalize_ref_code(body.get("referral_code") or body.get("code"))
        platform = _normalize_platform(body.get("selected_platform") or body.get("platform"))
        event_type = _clean(body.get("event_type") or "platform_select")[:80]

    if not code:
        return jsonify({"ok": False, "route_version": ROUTE_VERSION, "error": "missing_referral_code"}), 400

    destination = _platform_destination(code, platform)
    provided_landing = body.get("landing_url") if body else request.args.get("landing_url")
    landing_url = _safe_landing_url(provided_landing, fallback=destination)

    tracking = _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": event_type,
            "selected_platform": platform or None,
            "visitor_token": body.get("visitor_token") if body else request.args.get("visitor_token"),
            "channel_type": body.get("channel_type") if body else request.args.get("channel_type"),
            "provider_user_id": body.get("provider_user_id") if body else request.args.get("provider_user_id"),
            "account_id": body.get("account_id") if body else request.args.get("account_id"),
            "landing_url": landing_url,
            "metadata": {
                "method": request.method,
                "query": dict(request.args),
                "body": body,
                "route": "/api/referral/track",
                "destination": destination,
                "referrer": request.referrer,
            },
        }
    )

    return jsonify(
        {
            "ok": True,
            "route_version": ROUTE_VERSION,
            "referral_code": code,
            "selected_platform": platform,
            "destination": destination,
            "tracking": {
                "ok": bool(tracking.get("ok")),
                "mode": tracking.get("mode"),
                "non_blocking": True,
            },
        }
    ), 200


@bp.get("/referral/track-and-go/<ref_code>/<platform>")
def referral_track_and_go(ref_code: str, platform: str):
    code = _normalize_ref_code(ref_code)
    chosen = _normalize_platform(platform)

    if not code:
        return redirect(_public_site_url(), code=302)

    destination = _platform_destination(code, chosen)

    _safe_insert_referral_event(
        {
            "referral_code": code,
            "event_type": "platform_click",
            "selected_platform": chosen,
            "visitor_token": request.args.get("visitor_token"),
            "landing_url": destination,
            "metadata": {
                "query": dict(request.args),
                "route": "/api/referral/track-and-go/<ref_code>/<platform>",
                "destination": destination,
                "referrer": request.referrer,
            },
        }
    )

    return redirect(destination, code=302)
