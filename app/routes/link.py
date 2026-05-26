# app/routes/link.py
from __future__ import annotations

import hashlib
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple
from uuid import uuid4
from urllib.parse import quote_plus

from flask import Blueprint, jsonify, request, session, g

try:
    from app.core.supabase_client import get_supabase_client
except Exception:
    get_supabase_client = None  # type: ignore


# IMPORTANT:
# Do NOT set url_prefix="/link" here because app/__init__.py registers
# all blueprints with url_prefix="/api", which overrides blueprint-level
# prefixes. Therefore routes below include /link directly.
bp = Blueprint("link", __name__)

LINK_ROUTE_VERSION = "2026-05-26-v6c-telegram-web-verification-status-cleanup"
CODE_LENGTH = int(os.getenv("LINK_CODE_LENGTH", "8"))
CODE_TTL_MINUTES = int(os.getenv("LINK_CODE_TTL_MINUTES", "30"))
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _normalize_provider(provider: Optional[str]) -> Tuple[str, str, str]:
    raw = (provider or "wa").strip().lower()

    if raw in {"wa", "whatsapp", "whats_app", "whats-app"}:
        return "wa", "whatsapp", "WhatsApp"

    if raw in {"tg", "telegram"}:
        return "tg", "telegram", "Telegram"

    return raw, raw, raw.title()


def _random_code(length: int = CODE_LENGTH) -> str:
    return "".join(random.choice(CODE_ALPHABET) for _ in range(max(6, length)))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _rows(resp: Any) -> list[dict[str, Any]]:
    data = getattr(resp, "data", None)

    if data is None:
        return []

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        return [data]

    return []


def _first(resp: Any) -> Optional[dict[str, Any]]:
    rows = _rows(resp)
    return rows[0] if rows else None


def _client(admin: bool = True):
    if get_supabase_client is None:
        raise RuntimeError("get_supabase_client is unavailable")

    try:
        return get_supabase_client(admin=admin)  # type: ignore[misc]
    except TypeError:
        return get_supabase_client()  # type: ignore[operator]


def _safe_exec(builder: Any) -> tuple[bool, Any, Optional[str]]:
    try:
        resp = builder.execute()
        return True, resp, None
    except Exception as exc:
        return False, None, str(exc)


def _json_error(message: str, status: int = 400, **extra: Any):
    payload: dict[str, Any] = {
        "ok": False,
        "error": message,
    }
    payload.update(extra)
    return jsonify(payload), status


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, (int, float)):
        return value != 0

    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "verified", "linked", "active"}


# -----------------------------------------------------------------------------
# Auth/account resolution
# -----------------------------------------------------------------------------

def _extract_account_id(value: Any) -> Optional[str]:
    if not value:
        return None

    if isinstance(value, str):
        return value.strip() or None

    if isinstance(value, dict):
        for key in ("account_id", "id", "user_id", "auth_user_id"):
            val = value.get(key)
            if val:
                return str(val)

    return None


def _resolve_account_id() -> Optional[str]:
    # 1. Flask globals
    for key in ("account_id", "user_id", "auth_user_id"):
        val = getattr(g, key, None)
        account_id = _extract_account_id(val)
        if account_id:
            return account_id

    # 2. Flask session
    for key in ("account_id", "user_id", "auth_user_id"):
        account_id = _extract_account_id(session.get(key))
        if account_id:
            return account_id

    # 3. Current web auth service
    try:
        from app.services import web_auth_service  # type: ignore

        for name in (
            "get_account_id_from_request",
            "resolve_account_id_from_request",
            "get_current_account_id",
        ):
            fn = getattr(web_auth_service, name, None)

            if callable(fn):
                try:
                    account_id = _extract_account_id(fn(request))
                except TypeError:
                    account_id = _extract_account_id(fn())

                if account_id:
                    return account_id
    except Exception:
        pass

    # 4. Middleware fallback
    try:
        from app.middleware import web_auth  # type: ignore

        for name in ("get_account_id_from_request", "resolve_account_id"):
            fn = getattr(web_auth, name, None)

            if callable(fn):
                try:
                    account_id = _extract_account_id(fn(request))
                except TypeError:
                    account_id = _extract_account_id(fn())

                if account_id:
                    return account_id
    except Exception:
        pass

    return None


def _get_account_or_401() -> tuple[Optional[str], Optional[Any]]:
    account_id = _resolve_account_id()

    if not account_id:
        return None, _json_error("Authentication required.", 401)

    return account_id, None


# -----------------------------------------------------------------------------
# Channel status helpers
# -----------------------------------------------------------------------------

def _identity_for_channel(
    db: Any,
    account_id: str,
    channel_type: str,
) -> Optional[dict[str, Any]]:
    ok, resp, _ = _safe_exec(
        db.table("channel_identities")
        .select("*")
        .eq("account_id", account_id)
        .eq("channel_type", channel_type)
        .limit(1)
    )

    if ok:
        row = _first(resp)
        if row:
            return row

    return None


def _fallback_account_for_channel(
    db: Any,
    account_id: str,
    provider: str,
) -> Optional[dict[str, Any]]:
    ok, resp, _ = _safe_exec(
        db.table("accounts")
        .select(
            "id,account_id,provider,provider_user_id,auth_user_id,"
            "display_name,phone,phone_e164,email,updated_at,created_at"
        )
        .eq("auth_user_id", account_id)
        .eq("provider", provider)
        .limit(1)
    )

    if ok:
        return _first(resp)

    return None


def _identity_verified(row: dict[str, Any], channel_type: str) -> bool:
    """
    Batch 27B3:
    Telegram Batch 27B2 may create a minimal channel_identities row because the
    table rejected richer payloads. If the row exists and has provider_user_id,
    the link is already durable and should be shown as verified on the web page.

    WhatsApp remains compatible with richer is_verified/verified columns, but
    also treats a durable identity row with provider_user_id as verified because
    the bot-side link was already confirmed before the row was written.
    """

    if _truthy(row.get("is_verified")) or _truthy(row.get("verified")):
        return True

    if _clean(row.get("provider_user_id")):
        return True

    if channel_type == "telegram" and (_clean(row.get("value")) or _clean(row.get("username"))):
        return True

    if channel_type == "whatsapp" and (_clean(row.get("value")) or _clean(row.get("phone"))):
        return True

    return False


def _status_object(
    db: Any,
    account_id: str,
    provider: str,
    channel_type: str,
) -> dict[str, Any]:
    row = _identity_for_channel(db, account_id, channel_type)

    if row:
        provider_user_id = (
            row.get("provider_user_id")
            or row.get("value")
            or row.get("username")
            or row.get("phone")
        )
        metadata = row.get("metadata") or {}

        if not isinstance(metadata, dict):
            metadata = {}

        verified = _identity_verified(row, channel_type)
        updated_at = (
            row.get("last_seen_at")
            or row.get("linked_at")
            or row.get("updated_at")
            or row.get("created_at")
        )

        phone = None
        username = None

        if channel_type == "whatsapp":
            phone = row.get("phone") or row.get("value") or provider_user_id
        elif channel_type == "telegram":
            username = (
                metadata.get("username")
                or row.get("username")
                or row.get("value")
                or provider_user_id
            )
            phone = metadata.get("phone")

        return {
            "linked": True,
            "verified": verified,
            "is_verified": verified,
            "value": provider_user_id,
            "provider_user_id": provider_user_id,
            "phone": phone,
            "username": username,
            "display_name": metadata.get("display_name") or row.get("display_name"),
            "updated_at": updated_at,
            "last_seen_at": row.get("last_seen_at") or updated_at,
            "status": "linked" if verified else "pending_verification",
            "verification_status": "verified" if verified else "pending",
        }

    fallback = _fallback_account_for_channel(db, account_id, provider)

    if fallback and fallback.get("provider_user_id"):
        provider_user_id = fallback.get("provider_user_id")
        updated_at = fallback.get("updated_at") or fallback.get("created_at")

        return {
            "linked": True,
            "verified": True,
            "is_verified": True,
            "value": provider_user_id,
            "provider_user_id": provider_user_id,
            "phone": fallback.get("phone_e164") or fallback.get("phone") or (
                provider_user_id if channel_type == "whatsapp" else None
            ),
            "username": provider_user_id if channel_type == "telegram" else None,
            "display_name": fallback.get("display_name"),
            "updated_at": updated_at,
            "last_seen_at": updated_at,
            "status": "linked",
            "verification_status": "verified",
        }

    return {
        "linked": False,
        "verified": False,
        "is_verified": False,
        "value": None,
        "provider_user_id": None,
        "phone": None,
        "username": None,
        "display_name": None,
        "updated_at": None,
        "last_seen_at": None,
        "status": "not_linked",
        "verification_status": "not_verified",
    }


def _build_status(account_id: str) -> dict[str, Any]:
    db = _client(admin=True)

    whatsapp = _status_object(db, account_id, "wa", "whatsapp")
    telegram = _status_object(db, account_id, "tg", "telegram")

    return {
        "ok": True,
        "account_id": account_id,

        "whatsapp": whatsapp,
        "whatsapp_linked": bool(whatsapp.get("linked")),
        "whatsapp_verified": bool(whatsapp.get("verified")),
        "whatsapp_number": whatsapp.get("phone") or whatsapp.get("provider_user_id"),
        "whatsapp_updated_at": whatsapp.get("updated_at"),

        "telegram": telegram,
        "telegram_linked": bool(telegram.get("linked")),
        "telegram_verified": bool(telegram.get("verified")),
        "telegram_username": telegram.get("username") or telegram.get("provider_user_id"),
        "telegram_updated_at": telegram.get("updated_at"),

        "route_version": LINK_ROUTE_VERSION,
    }


# -----------------------------------------------------------------------------
# Link-code helpers
# -----------------------------------------------------------------------------

def _expire_open_tokens(db: Any, account_id: str, provider: str) -> None:
    payload = {
        "used_at": _iso(_utc_now()),
    }

    _safe_exec(
        db.table("link_tokens")
        .update(payload)
        .eq("auth_user_id", account_id)
        .eq("provider", provider)
        .is_("used_at", "null")
    )


def _insert_link_token(
    db: Any,
    account_id: str,
    provider: str,
    code: str,
    expires_at: datetime,
) -> dict[str, Any]:
    now = _utc_now()

    common = {
        "id": str(uuid4()),
        "auth_user_id": account_id,
        "account_id": account_id,
        "provider": provider,
        "code": code,
        "token": code,
        "code_hash": _hash_code(code),
        "expires_at": _iso(expires_at),
        "created_at": _iso(now),
        "used_at": None,
        "provider_user_id": None,
        "used_by_channel_type": None,
        "used_by_provider_user_id": None,
    }

    attempts = [
        common,
        {
            k: v
            for k, v in common.items()
            if k not in {
                "account_id",
                "token",
                "used_by_channel_type",
                "used_by_provider_user_id",
            }
        },
        {
            "auth_user_id": account_id,
            "provider": provider,
            "code": code,
            "code_hash": _hash_code(code),
            "expires_at": _iso(expires_at),
            "created_at": _iso(now),
            "used_at": None,
        },
    ]

    last_error: Optional[str] = None

    for payload in attempts:
        ok, resp, err = _safe_exec(
            db.table("link_tokens").insert(payload)
        )

        if ok:
            row = _first(resp)
            if row:
                return row

            return payload

        last_error = err

    raise RuntimeError(last_error or "link_token_insert_failed")


def _whatsapp_open_url(code: str) -> Optional[str]:
    phone = (
        os.getenv("WHATSAPP_OFFICIAL_NUMBER")
        or os.getenv("WHATSAPP_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_PHONE_NUMBER")
        or os.getenv("WA_PHONE_NUMBER")
        or ""
    )

    digits = "".join(ch for ch in phone if ch.isdigit())

    if not digits:
        return None

    text = quote_plus(code)
    return f"https://wa.me/{digits}?text={text}"


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@bp.route("/link/health", methods=["GET", "POST", "HEAD", "OPTIONS"])
def health():
    return jsonify(
        {
            "ok": True,
            "service": "link",
            "version": LINK_ROUTE_VERSION,
            "token_schema": "used_at_only",
            "route_mount": "/api/link/*",
            "status_policy": "durable_channel_identity_is_verified",
        }
    )


@bp.route("/link/status", methods=["GET", "POST", "HEAD", "OPTIONS"])
def status():
    account_id, error = _get_account_or_401()

    if error:
        return error

    assert account_id is not None
    return jsonify(_build_status(account_id))


@bp.route("/link/generate", methods=["POST", "OPTIONS"])
def generate_link_code():
    account_id, error = _get_account_or_401()

    if error:
        return error

    assert account_id is not None

    provider_value = request.args.get("provider")

    if request.is_json and isinstance(request.json, dict):
        provider_value = provider_value or request.json.get("provider")

    provider, channel_type, label = _normalize_provider(provider_value)

    if provider not in {"wa", "tg"}:
        return _json_error("Unsupported provider.", 400, provider=provider)

    db = _client(admin=True)

    _expire_open_tokens(db, account_id, provider)

    expires_at = _utc_now() + timedelta(minutes=CODE_TTL_MINUTES)
    last_error: Optional[str] = None
    row: Optional[dict[str, Any]] = None
    code = ""

    for _ in range(6):
        code = _random_code()

        try:
            row = _insert_link_token(db, account_id, provider, code, expires_at)
            break
        except Exception as exc:
            last_error = str(exc)
            row = None

    if row is None:
        return _json_error(
            "Could not generate link code.",
            500,
            reason=last_error or "insert_failed",
        )

    open_url = _whatsapp_open_url(code) if provider == "wa" else None

    return jsonify(
        {
            "ok": True,
            "provider": provider,
            "channel_type": channel_type,
            "channel_label": label,
            "code": code,
            "link_code": code,
            "token": code,
            "expires_at": row.get("expires_at") or _iso(expires_at),
            "expires_in_minutes": CODE_TTL_MINUTES,
            "open_url": open_url,
            "whatsapp_url": open_url,
            "route_version": LINK_ROUTE_VERSION,
        }
    )


@bp.route("/link/unlink", methods=["POST", "OPTIONS"])
def unlink_channel():
    account_id, error = _get_account_or_401()

    if error:
        return error

    assert account_id is not None

    provider_value = request.args.get("provider")

    if request.is_json and isinstance(request.json, dict):
        provider_value = provider_value or request.json.get("provider")

    provider_value = provider_value or request.form.get("provider") or "wa"
    provider, channel_type, label = _normalize_provider(provider_value)

    if provider not in {"wa", "tg"}:
        return _json_error("Unsupported provider.", 400, provider=provider)

    db = _client(admin=True)
    removed = 0

    ok, resp, _ = _safe_exec(
        db.table("channel_identities")
        .select("id")
        .eq("account_id", account_id)
        .eq("channel_type", channel_type)
    )

    if ok:
        for row in _rows(resp):
            row_id = row.get("id")

            if not row_id:
                continue

            del_ok, _, _ = _safe_exec(
                db.table("channel_identities")
                .delete()
                .eq("id", row_id)
            )

            if del_ok:
                removed += 1

    _safe_exec(
        db.table("accounts")
        .update(
            {
                "auth_user_id": None,
                "updated_at": _iso(_utc_now()),
            }
        )
        .eq("auth_user_id", account_id)
        .eq("provider", provider)
    )

    _expire_open_tokens(db, account_id, provider)

    return jsonify(
        {
            "ok": True,
            "provider": provider,
            "channel_type": channel_type,
            "channel_label": label,
            "unlinked": True,
            "removed_identities": removed,
            "route_version": LINK_ROUTE_VERSION,
        }
    )


@bp.route("/link/generate-code", methods=["POST", "OPTIONS"])
def generate_link_code_alias():
    return generate_link_code()


@bp.route("/link/me", methods=["GET", "POST", "HEAD", "OPTIONS"])
def me_alias():
    return status()
