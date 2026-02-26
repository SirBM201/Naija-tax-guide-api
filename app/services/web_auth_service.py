# app/services/web_auth_service.py
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase


# --------------------------------------------------
# Time helpers
# --------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------
# Env helpers (NO app.core.config imports, to prevent boot crashes)
# --------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


WEB_AUTH_ENABLED = _truthy(_env("WEB_AUTH_ENABLED", "1"))
WEB_AUTH_DEV_OTP_ENABLED = _truthy(_env("WEB_AUTH_DEV_OTP_ENABLED", "0"))
WEB_AUTH_OTP_TTL_SECONDS = int(_env("WEB_AUTH_OTP_TTL_SECONDS", "600") or "600")
WEB_AUTH_MASTER_OTP = _env("WEB_AUTH_MASTER_OTP", "")
WEB_AUTH_DEV_SHARED_SECRET = _env("WEB_AUTH_DEV_SHARED_SECRET", "")
WEB_AUTH_DEV_ALLOWED_PHONES_LIST = [
    x.strip() for x in (_env("WEB_AUTH_DEV_ALLOWED_PHONES_LIST", "")).split(",") if x.strip()
]

OTP_HASH_PEPPER = _env("OTP_HASH_PEPPER", _env("ADMIN_API_KEY", "dev-pepper"))

WEB_AUTH_COOKIE_NAME = _env("WEB_AUTH_COOKIE_NAME", _env("WEB_COOKIE_NAME", "ntg_session"))
WEB_SESSIONS_TABLE = _env("WEB_SESSIONS_TABLE", "web_sessions")


def _sb():
    return supabase() if callable(supabase) else supabase


# --------------------------------------------------
# Hashing
# --------------------------------------------------
def _hash(value: str) -> str:
    pepper = (OTP_HASH_PEPPER or "dev-pepper").encode()
    return hmac.new(pepper, value.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------
# Bearer normalize
# --------------------------------------------------
def _normalize_bearer(auth_header: str) -> str:
    if not auth_header:
        return ""
    v = auth_header.strip()
    if v.lower().startswith("bearer "):
        return v[7:].strip()
    return ""


# --------------------------------------------------
# DEV guard
# --------------------------------------------------
def _dev_guard(phone_e164: str, shared_secret: Optional[str]) -> Optional[str]:
    if not WEB_AUTH_ENABLED:
        return "Web auth is disabled"

    if not WEB_AUTH_DEV_OTP_ENABLED:
        return "DEV OTP is disabled"

    if WEB_AUTH_DEV_ALLOWED_PHONES_LIST and phone_e164 not in WEB_AUTH_DEV_ALLOWED_PHONES_LIST:
        return "Phone is not allowed in DEV mode"

    if WEB_AUTH_DEV_SHARED_SECRET:
        if not shared_secret or shared_secret != WEB_AUTH_DEV_SHARED_SECRET:
            return "Invalid shared_secret"

    return None


# --------------------------------------------------
# Account binding
# --------------------------------------------------
def _get_or_create_account_by_phone(phone_e164: str) -> Tuple[bool, Optional[str], Optional[str]]:
    q = (
        _sb()
        .table("accounts")
        .select("id")
        .eq("phone_e164", phone_e164)
        .limit(1)
        .execute()
    )

    if getattr(q, "data", None):
        account_id = q.data[0]["id"]
    else:
        ins = (
            _sb()
            .table("accounts")
            .insert({"phone_e164": phone_e164})
            .select("id")
            .execute()
        )
        if not getattr(ins, "data", None):
            return False, None, "Failed to create account"
        account_id = ins.data[0]["id"]

    return True, account_id, None


# --------------------------------------------------
# OTP REQUEST
# --------------------------------------------------
def request_web_otp(phone_e164: str, device_id: Optional[str], shared_secret: Optional[str]):
    err = _dev_guard(phone_e164, shared_secret)
    if err:
        return {"ok": False, "error": err}

    # Soft revoke instead of reject
    _sb().table("web_otps").update({"revoked": True}).eq("phone_e164", phone_e164).eq("revoked", False).execute()

    otp = f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(seconds=int(WEB_AUTH_OTP_TTL_SECONDS))

    otp_hash = _hash(f"{phone_e164}:{otp}")

    _sb().table("web_otps").insert(
        {
            "phone_e164": phone_e164,
            "device_id": device_id,
            "otp_hash": otp_hash,
            "expires_at": _iso(expires_at),
            "attempts": 0,
            "revoked": False,
        }
    ).execute()

    return {"ok": True, "dev": True, "otp": otp, "expires_at": _iso(expires_at)}


# --------------------------------------------------
# OTP VERIFY
# --------------------------------------------------
def verify_web_otp(phone_e164: str, otp: str, device_id: Optional[str]):
    if not phone_e164 or not otp:
        return {"ok": False, "error": "Missing phone_e164 or otp"}

    if WEB_AUTH_MASTER_OTP and otp == WEB_AUTH_MASTER_OTP:
        ok, account_id, error = _get_or_create_account_by_phone(phone_e164)
        if not ok:
            return {"ok": False, "error": error}
        return _create_session(account_id, phone_e164, device_id)

    q = (
        _sb()
        .table("web_otps")
        .select("*")
        .eq("phone_e164", phone_e164)
        .eq("revoked", False)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not getattr(q, "data", None):
        return {"ok": False, "error": "OTP not found"}

    row = q.data[0]

    incoming_hash = _hash(f"{phone_e164}:{otp}")
    if incoming_hash != row.get("otp_hash"):
        return {"ok": False, "error": "Invalid OTP"}

    _sb().table("web_otps").update({"revoked": True}).eq("id", row["id"]).execute()

    ok, account_id, error = _get_or_create_account_by_phone(phone_e164)
    if not ok:
        return {"ok": False, "error": error}

    return _create_session(account_id, phone_e164, device_id)


# --------------------------------------------------
# SESSION CREATE
# --------------------------------------------------
def _create_session(account_id: str, phone_e164: str, device_id: Optional[str]):
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash(f"session:{raw_token}")

    expires_at = _now_utc() + timedelta(days=30)

    _sb().table(WEB_SESSIONS_TABLE).insert(
        {
            "account_id": account_id,
            "phone_e164": phone_e164,
            "device_id": device_id,
            "token_hash": token_hash,
            "expires_at": _iso(expires_at),
            "revoked": False,
        }
    ).execute()

    return {"ok": True, "account_id": account_id, "token": raw_token, "expires_at": _iso(expires_at)}


# --------------------------------------------------
# SESSION VALIDATION (bearer token)
# --------------------------------------------------
def require_web_session(auth_header: str):
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    token_hash = _hash(f"session:{token}")

    q = (
        _sb()
        .table(WEB_SESSIONS_TABLE)
        .select("*")
        .eq("token_hash", token_hash)
        .eq("revoked", False)
        .limit(1)
        .execute()
    )

    if not getattr(q, "data", None):
        return {"ok": False, "error": "invalid_token"}

    row = q.data[0]

    exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if _now_utc() > exp:
        return {"ok": False, "error": "session_expired"}

    return {"ok": True, "account_id": row["account_id"]}


# --------------------------------------------------
# SESSION VALIDATION (cookie OR bearer)
# --------------------------------------------------
def get_account_id_from_request(flask_request) -> Tuple[Optional[str], str]:
    """
    Returns (account_id, source) where source is "cookie" | "bearer" | "none".
    """
    raw_cookie = (flask_request.cookies.get(WEB_AUTH_COOKIE_NAME) or "").strip()
    if raw_cookie:
        token_hash = _hash(f"session:{raw_cookie}")
        q = (
            _sb()
            .table(WEB_SESSIONS_TABLE)
            .select("*")
            .eq("token_hash", token_hash)
            .eq("revoked", False)
            .limit(1)
            .execute()
        )
        if getattr(q, "data", None):
            row = q.data[0]
            try:
                exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if _now_utc() <= exp:
                    return str(row["account_id"]), "cookie"
            except Exception:
                pass

    auth = (flask_request.headers.get("Authorization") or "").strip()
    out = require_web_session(auth)
    if out.get("ok"):
        return str(out.get("account_id")), "bearer"

    return None, "none"


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------
def logout_web_session(auth_header: str):
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    token_hash = _hash(f"session:{token}")
    _sb().table(WEB_SESSIONS_TABLE).update({"revoked": True}).eq("token_hash", token_hash).execute()
    return {"ok": True}
