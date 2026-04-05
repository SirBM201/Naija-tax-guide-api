from __future__ import annotations

"""
Accounts service for Naija Tax Guide.

This version preserves the repo's real Supabase import path:
    from app.core.supabase_client import supabase

And adds the helper functions required by the newer WhatsApp/channel-link flow:
- find_account_by_provider_user_id
- find_account_by_auth_user_and_provider
- mark_channel_claimed_for_auth_user

It also keeps the public functions expected elsewhere in the app:
- upsert_account
- lookup_account
- upsert_account_link
"""

from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timezone
import uuid
import os

from app.core.supabase_client import supabase


# ---------------------------------------------------------
# Common helpers
# ---------------------------------------------------------
def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _clip(s: str, n: int = 260) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug_enabled() -> bool:
    return _truthy(os.getenv("ACCOUNTS_DEBUG", "0")) or _truthy(os.getenv("AUTH_DEBUG", "0"))


def _dbg(msg: str) -> None:
    if _debug_enabled():
        print(msg, flush=True)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except Exception:
        return False


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


def _safe_debug_meta() -> Dict[str, Any]:
    if not _debug_enabled():
        return {}
    return {
        "tables": {"accounts": "accounts"},
        "env": (os.getenv("ENV", "prod") or "prod").lower(),
    }


def _select_cols_existing(table: str, cols: List[str]) -> str:
    existing: List[str] = []
    for c in cols:
        if _has_column(table, c):
            existing.append(c)
    for must in ("id", "account_id", "provider", "provider_user_id"):
        if must not in existing and _has_column(table, must):
            existing.append(must)
    return ",".join(existing) if existing else "*"


def _normalize_phone_e164(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


# ---------------------------------------------------------
# Provider normalization
# ---------------------------------------------------------
ALLOWED_PROVIDERS = {"wa", "tg", "msgr", "ig", "email", "web"}

PROVIDER_ALIASES = {
    "wa": "wa",
    "whatsapp": "wa",
    "waba": "wa",
    "tg": "tg",
    "telegram": "tg",
    "msgr": "msgr",
    "messenger": "msgr",
    "facebook_messenger": "msgr",
    "fb_messenger": "msgr",
    "facebook messenger": "msgr",
    "ig": "ig",
    "instagram": "ig",
    "instagram_dm": "ig",
    "email": "email",
    "mail": "email",
    "web": "web",
    "website": "web",
}


def _norm_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    return PROVIDER_ALIASES.get(p, p)


def _validate_provider_and_id(provider: str, provider_user_id: str) -> Optional[str]:
    provider = _norm_provider(provider)
    if provider not in ALLOWED_PROVIDERS:
        return "provider must be one of: wa, tg, msgr, ig, email, web"
    provider_user_id = (provider_user_id or "").strip()
    if not provider_user_id:
        return "provider_user_id required"
    if provider == "email":
        v = provider_user_id.strip().lower()
        if "@" not in v or "." not in v:
            return "provider_user_id must be a valid email address for provider=email"
    return None


# ---------------------------------------------------------
# Read helpers
# ---------------------------------------------------------
def _fetch_one_by_filters(filters: Dict[str, str]) -> Optional[Dict[str, Any]]:
    cols = _select_cols_existing(
        "accounts",
        [
            "id",
            "account_id",
            "provider",
            "provider_user_id",
            "auth_user_id",
            "display_name",
            "phone",
            "phone_e164",
            "email",
            "updated_at",
            "created_at",
        ],
    )
    q = _sb().table("accounts").select(cols)
    for key, value in filters.items():
        q = q.eq(key, value)
    res = q.limit(1).execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def find_account_by_provider_user_id(*, provider: str, provider_user_id: str) -> Optional[Dict[str, Any]]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()
    if not provider or not provider_user_id:
        return None
    try:
        return _fetch_one_by_filters({"provider": provider, "provider_user_id": provider_user_id})
    except Exception:
        return None


def find_account_by_auth_user_and_provider(*, auth_user_id: str, provider: str) -> Optional[Dict[str, Any]]:
    auth_user_id = (auth_user_id or "").strip()
    provider = _norm_provider(provider)
    if not auth_user_id or not provider:
        return None
    try:
        return _fetch_one_by_filters({"auth_user_id": auth_user_id, "provider": provider})
    except Exception:
        return None


# ---------------------------------------------------------
# Canonical account_id helpers
# ---------------------------------------------------------
def _ensure_account_id_on_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row

    account_id = str(row.get("account_id") or "").strip()
    row_id = str(row.get("id") or "").strip()

    if account_id:
        return row

    if not row_id:
        return row

    try:
        _sb().table("accounts").update({"account_id": row_id, "updated_at": _now_iso()}).eq("id", row_id).execute()
        row["account_id"] = row_id
    except Exception as e:
        _dbg(f"[accounts_service] failed to bridge account_id <- id for row {row_id}: {_clip(e)}")

    return row


def _public_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    row = _ensure_account_id_on_row(dict(row))
    return row


# ---------------------------------------------------------
# Public API used elsewhere in app
# ---------------------------------------------------------
def lookup_account(*, provider: str, provider_user_id: str) -> Dict[str, Any]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {
            "ok": False,
            "error": "invalid_identity",
            "message": err,
            "debug": _safe_debug_meta(),
        }

    try:
        row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
        row = _public_row(row)
        if not row:
            return {"ok": True, "linked": False}
        return {
            "ok": True,
            "linked": bool(str(row.get("auth_user_id") or "").strip()),
            "id": row.get("id"),
            "account_id": row.get("account_id") or row.get("id"),
            "auth_user_id": row.get("auth_user_id"),
            "row": row,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "debug": _safe_debug_meta(),
        }


def upsert_account(*, provider: str, provider_user_id: str, display_name: Optional[str] = None, phone: Optional[str] = None, email: Optional[str] = None) -> Dict[str, Any]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {
            "ok": False,
            "error": "invalid_identity",
            "message": err,
            "debug": _safe_debug_meta(),
        }

    payload: Dict[str, Any] = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "updated_at": _now_iso(),
    }
    if _has_column("accounts", "display_name"):
        payload["display_name"] = display_name
    if _has_column("accounts", "phone"):
        payload["phone"] = phone
    if _has_column("accounts", "phone_e164"):
        payload["phone_e164"] = _normalize_phone_e164(phone or provider_user_id)
    if _has_column("accounts", "email") and email is not None:
        payload["email"] = email

    try:
        _sb().table("accounts").upsert(payload, on_conflict="provider,provider_user_id").execute()
        row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
        row = _public_row(row)
        return {"ok": True, "row": row, "account_id": (row or {}).get("account_id")}
    except Exception as e:
        return {
            "ok": False,
            "error": "upsert_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "debug": _safe_debug_meta(),
        }


def mark_channel_claimed_for_auth_user(
    *,
    provider: str,
    provider_user_id: str,
    auth_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()
    auth_user_id = (auth_user_id or "").strip()

    existing = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if not existing:
        return {"ok": False, "reason": "channel_not_found"}

    existing_auth = str(existing.get("auth_user_id") or "").strip()
    if existing_auth and existing_auth != auth_user_id:
        return {"ok": False, "reason": "channel_belongs_to_another_user"}

    patch: Dict[str, Any] = {"auth_user_id": auth_user_id, "updated_at": _now_iso()}
    if _has_column("accounts", "display_name"):
        patch["display_name"] = display_name if display_name is not None else existing.get("display_name")
    if _has_column("accounts", "phone"):
        patch["phone"] = phone if phone is not None else existing.get("phone")
    if _has_column("accounts", "phone_e164"):
        patch["phone_e164"] = _normalize_phone_e164(phone or existing.get("phone") or provider_user_id)

    try:
        _sb().table("accounts").update(patch).eq("id", existing["id"]).execute()
        row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
        row = _public_row(row)
        return {"ok": True, "account_id": (row or {}).get("account_id"), "row": row}
    except Exception as e:
        return {"ok": False, "reason": f"claim_failed:{type(e).__name__}:{_clip(e)}"}


def upsert_account_link(
    *,
    provider: str,
    provider_user_id: str,
    auth_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()
    auth_user_id = (auth_user_id or "").strip()

    by_channel = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if by_channel:
        channel_auth = str(by_channel.get("auth_user_id") or "").strip()
        if channel_auth and channel_auth != auth_user_id:
            return {"ok": False, "reason": "channel_belongs_to_another_user"}

    by_user = find_account_by_auth_user_and_provider(auth_user_id=auth_user_id, provider=provider)
    if by_user:
        patch: Dict[str, Any] = {
            "provider_user_id": provider_user_id,
            "updated_at": _now_iso(),
        }
        if _has_column("accounts", "display_name"):
            patch["display_name"] = display_name if display_name is not None else by_user.get("display_name")
        if _has_column("accounts", "phone"):
            patch["phone"] = phone if phone is not None else by_user.get("phone")
        if _has_column("accounts", "phone_e164"):
            patch["phone_e164"] = _normalize_phone_e164(phone or by_user.get("phone") or provider_user_id)
        try:
            _sb().table("accounts").update(patch).eq("id", by_user["id"]).execute()
            row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id) or find_account_by_auth_user_and_provider(auth_user_id=auth_user_id, provider=provider)
            row = _public_row(row)
            if not row:
                return {"ok": False, "reason": "update_failed"}
            return {"ok": True, "account_id": row.get("account_id"), "row": row}
        except Exception as e:
            return {"ok": False, "reason": f"update_failed:{type(e).__name__}:{_clip(e)}"}

    payload: Dict[str, Any] = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "auth_user_id": auth_user_id,
        "updated_at": _now_iso(),
    }
    if _has_column("accounts", "display_name"):
        payload["display_name"] = display_name
    if _has_column("accounts", "phone"):
        payload["phone"] = phone
    if _has_column("accounts", "phone_e164"):
        payload["phone_e164"] = _normalize_phone_e164(phone or provider_user_id)

    try:
        _sb().table("accounts").upsert(payload, on_conflict="provider,provider_user_id").execute()
        row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
        row = _public_row(row)
        if not row:
            return {"ok": False, "reason": "link_failed"}
        return {"ok": True, "account_id": row.get("account_id"), "row": row}
    except Exception as e:
        return {"ok": False, "reason": f"link_failed:{type(e).__name__}:{_clip(e)}"}
