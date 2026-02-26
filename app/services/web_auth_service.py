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
    return dt.astimezone(timezone.utc).isoformat()


# --------------------------------------------------
# Env helpers (NO app.core.config imports, to prevent boot crashes)
# --------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


WEB_AUTH_ENABLED = _truthy(_env("WEB_AUTH_ENABLED", "1"))

# DEV OTP controls
WEB_AUTH_DEV_OTP_ENABLED = _truthy(_env("WEB_AUTH_DEV_OTP_ENABLED", "0"))
WEB_AUTH_OTP_TTL_SECONDS = int(_env("WEB_AUTH_OTP_TTL_SECONDS", "600") or "600")
WEB_AUTH_MASTER_OTP = _env("WEB_AUTH_MASTER_OTP", "")
WEB_AUTH_DEV_SHARED_SECRET = _env("WEB_AUTH_DEV_SHARED_SECRET", "")
WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST = [
    x.strip()
    for x in (_env("WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST", "")).split(",")
    if x.strip()
]

# Token lifetime (web_tokens.expires_at)
WEB_AUTH_TOKEN_TTL_DAYS = int(_env("WEB_AUTH_TOKEN_TTL_DAYS", "30") or "30")

# Hash pepper
HASH_PEPPER = _env("OTP_HASH_PEPPER", _env("ADMIN_API_KEY", "dev-pepper"))

# Cookie name
WEB_AUTH_COOKIE_NAME = _env("WEB_AUTH_COOKIE_NAME", _env("WEB_COOKIE_NAME", "ntg_session"))

# Tables
WEB_OTPS_TABLE = _env("WEB_OTPS_TABLE", "web_otps")
WEB_TOKENS_TABLE = _env("WEB_TOKENS_TABLE", "web_tokens")
ACCOUNTS_TABLE = _env("ACCOUNTS_TABLE", "accounts")

# Accounts PK (your schema shows accounts.id is uuid and is the real FK target)
ACCOUNTS_PK_FIELD = _env("ACCOUNTS_PK_FIELD", "id")  # default to "id"


def _sb():
    return supabase() if callable(supabase) else supabase


# --------------------------------------------------
# Supabase response helpers
# --------------------------------------------------
def _sb_data(resp) -> Any:
    return getattr(resp, "data", None)


def _sb_err(resp) -> Any:
    return getattr(resp, "error", None)


def _err_text(err: Any) -> str:
    if not err:
        return ""
    # supabase-py errors often have .message
    msg = getattr(err, "message", None)
    if msg:
        return str(msg)
    return str(err)


def _is_conflict(err: Any) -> bool:
    """
    Best-effort detection of uniqueness conflict.
    PostgREST often returns 409 for unique violations; supabase-py may surface message text.
    """
    t = _err_text(err).lower()
    return ("409" in t) or ("conflict" in t) or ("duplicate key" in t) or ("unique constraint" in t) or ("23505" in t)


# --------------------------------------------------
# Hashing
# --------------------------------------------------
def _hmac_sha256(value: str) -> str:
    pepper = (HASH_PEPPER or "dev-pepper").encode()
    return hmac.new(pepper, value.encode(), hashlib.sha256).hexdigest()


def _hash_otp(contact: str, purpose: str, otp: str) -> str:
    return _hmac_sha256(f"otp:{purpose}:{contact}:{otp}")


def _hash_token(raw_token: str) -> str:
    return _hmac_sha256(f"token:{raw_token}")


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
def _dev_guard(contact: str, shared_secret: Optional[str]) -> Optional[str]:
    if not WEB_AUTH_ENABLED:
        return "Web auth is disabled"

    if not WEB_AUTH_DEV_OTP_ENABLED:
        return "DEV OTP is disabled"

    if WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST and contact not in WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST:
        return "Contact is not allowed in DEV mode"

    if WEB_AUTH_DEV_SHARED_SECRET:
        if not shared_secret or shared_secret != WEB_AUTH_DEV_SHARED_SECRET:
            return "Invalid shared_secret"

    return None


# --------------------------------------------------
# Account binding (provider=web, provider_user_id=contact)
# IMPORTANT: Use accounts.id as the FK target (matches your schema + FK constraints)
# --------------------------------------------------
def _get_or_create_web_account(contact: str) -> Tuple[bool, Optional[str], Optional[str]]:
    pk = ACCOUNTS_PK_FIELD or "id"

    q = (
        _sb()
        .table(ACCOUNTS_TABLE)
        .select(pk)
        .eq("provider", "web")
        .eq("provider_user_id", contact)
        .limit(1)
        .execute()
    )

    if _sb_err(q):
        return False, None, f"accounts_select_failed: {_err_text(_sb_err(q))}"

    if _sb_data(q):
        return True, str(q.data[0][pk]), None

    ins = (
        _sb()
        .table(ACCOUNTS_TABLE)
        .insert(
            {
                "provider": "web",
                "provider_user_id": contact,
                "display_name": contact,
                # keep compatibility: some schemas reuse phone_e164 for email
                "phone_e164": contact,
            }
        )
        .select(pk)
        .execute()
    )

    if _sb_err(ins):
        return False, None, f"accounts_insert_failed: {_err_text(_sb_err(ins))}"

    if not _sb_data(ins):
        return False, None, "Failed to create account"

    return True, str(ins.data[0][pk]), None


# --------------------------------------------------
# OTP REQUEST (web_otps)
# --------------------------------------------------
def request_web_otp(
    contact: str,
    purpose: str,
    device_id: Optional[str] = None,
    shared_secret: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    contact = (contact or "").strip()
    purpose = (purpose or "").strip() or "web_login"

    if not contact:
        return {"ok": False, "error": "missing_contact"}
    if not WEB_AUTH_ENABLED:
        return {"ok": False, "error": "web_auth_disabled"}

    if WEB_AUTH_DEV_OTP_ENABLED:
        err = _dev_guard(contact, shared_secret)
        if err:
            return {"ok": False, "error": err}

    now = _now_utc()

    # revoke old unused OTPs
    _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("contact", contact).eq(
        "purpose", purpose
    ).is_("used_at", "null").is_("revoked_at", "null").execute()

    otp = f"{secrets.randbelow(1000000):06d}"
    expires_at = now + timedelta(seconds=int(WEB_AUTH_OTP_TTL_SECONDS))
    code_hash = _hash_otp(contact, purpose, otp)

    payload: Dict[str, Any] = {
        "contact": contact,
        "purpose": purpose,
        "code_hash": code_hash,
        "expires_at": _iso(expires_at),
        "used_at": None,
        "revoked_at": None,
    }
    if device_id:
        payload["device_id"] = device_id
    if ip:
        payload["ip"] = ip
    if user_agent:
        payload["user_agent"] = user_agent

    ins = _sb().table(WEB_OTPS_TABLE).insert(payload).execute()
    if _sb_err(ins):
        return {"ok": False, "error": f"otp_insert_failed: {_err_text(_sb_err(ins))}"}

    out: Dict[str, Any] = {
        "ok": True,
        "ttl_minutes": int(int(WEB_AUTH_OTP_TTL_SECONDS) / 60),
    }
    if WEB_AUTH_DEV_OTP_ENABLED:
        out["dev_otp"] = otp

    return out


# --------------------------------------------------
# TOKEN CREATE (web_tokens) -> revoked BOOLEAN
# Handles 409 conflicts by revoking old tokens then retrying once.
# --------------------------------------------------
def _create_web_token(
    account_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = _now_utc()
    expires_at = now + timedelta(days=int(WEB_AUTH_TOKEN_TTL_DAYS))

    def _attempt_insert() -> Tuple[bool, Optional[str], Optional[str]]:
        raw_token = secrets.token_hex(32)  # 64 hex chars
        token_hash = _hash_token(raw_token)

        payload: Dict[str, Any] = {
            "account_id": account_id,
            "token_hash": token_hash,
            "expires_at": _iso(expires_at),
            "revoked": False,
            "last_seen_at": _iso(now),
        }
        if ip:
            payload["ip"] = ip
        if user_agent:
            payload["user_agent"] = user_agent
        if device_id:
            payload["device_id"] = device_id

        resp = _sb().table(WEB_TOKENS_TABLE).insert(payload).execute()
        if _sb_err(resp):
            return False, None, _err_text(_sb_err(resp))
        return True, raw_token, None

    # First try
    ok, raw, err = _attempt_insert()
    if ok and raw:
        return {"token": raw, "expires_at": _iso(expires_at)}

    # If conflict, revoke existing active tokens for this account and retry once
    if err and ("conflict" in err.lower() or "duplicate" in err.lower() or "unique" in err.lower() or "409" in err):
        _sb().table(WEB_TOKENS_TABLE).update({"revoked": True}).eq("account_id", account_id).eq("revoked", False).execute()
        ok2, raw2, err2 = _attempt_insert()
        if ok2 and raw2:
            return {"token": raw2, "expires_at": _iso(expires_at)}
        raise RuntimeError(f"web_token_insert_failed_after_retry: {err2 or err}")

    raise RuntimeError(f"web_token_insert_failed: {err or 'unknown_error'}")


# --------------------------------------------------
# OTP VERIFY (web_otps -> web_tokens)
# IMPORTANT: Do NOT consume OTP (used_at) until token creation succeeds.
# --------------------------------------------------
def verify_web_otp(
    contact: str,
    purpose: str,
    otp: str,
    device_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    contact = (contact or "").strip()
    purpose = (purpose or "").strip() or "web_login"
    otp = (otp or "").strip()

    if not contact or not otp:
        return {"ok": False, "error": "missing_contact_or_otp"}

    now = _now_utc()

    # Master OTP bypass
    if WEB_AUTH_MASTER_OTP and otp == WEB_AUTH_MASTER_OTP:
        ok, account_id, err = _get_or_create_web_account(contact)
        if not ok:
            return {"ok": False, "error": err}
        tok = _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id)
        return {
            "ok": True,
            "account_id": account_id,
            "auth_mode": "cookie+bearer",
            "token": tok["token"],
            "expires_at": tok["expires_at"],
        }

    code_hash = _hash_otp(contact, purpose, otp)

    q = (
        _sb()
        .table(WEB_OTPS_TABLE)
        .select("*")
        .eq("contact", contact)
        .eq("purpose", purpose)
        .eq("code_hash", code_hash)
        .is_("used_at", "null")
        .is_("revoked_at", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if _sb_err(q):
        return {"ok": False, "error": f"otp_lookup_failed: {_err_text(_sb_err(q))}"}

    if not _sb_data(q):
        return {"ok": False, "error": "invalid_or_expired_otp"}

    row = q.data[0]
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    except Exception:
        return {"ok": False, "error": "otp_bad_expires_at"}

    if now > exp:
        _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("id", row["id"]).execute()
        return {"ok": False, "error": "otp_expired"}

    # 1) ensure account exists
    ok, account_id, err = _get_or_create_web_account(contact)
    if not ok:
        return {"ok": False, "error": err}

    # 2) create token FIRST (so we don't consume OTP on failure)
    try:
        tok = _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id)
    except Exception as e:
        # leave OTP unused so user can retry; optionally revoke to be strict:
        # _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("id", row["id"]).execute()
        return {"ok": False, "error": f"token_create_failed: {str(e)}"}

    # 3) now mark OTP used
    _sb().table(WEB_OTPS_TABLE).update({"used_at": _iso(now)}).eq("id", row["id"]).execute()

    return {
        "ok": True,
        "account_id": account_id,
        "auth_mode": "cookie+bearer",
        "token": tok["token"],
        "expires_at": tok["expires_at"],
    }


# --------------------------------------------------
# TOKEN VALIDATION (bearer token) -> revoked BOOLEAN
# --------------------------------------------------
def require_web_session(auth_header: str) -> Dict[str, Any]:
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    token_hash = _hash_token(token)
    now = _now_utc()

    q = (
        _sb()
        .table(WEB_TOKENS_TABLE)
        .select("*")
        .eq("token_hash", token_hash)
        .eq("revoked", False)
        .limit(1)
        .execute()
    )

    if _sb_err(q):
        return {"ok": False, "error": f"token_lookup_failed: {_err_text(_sb_err(q))}"}

    if not _sb_data(q):
        return {"ok": False, "error": "invalid_token"}

    row = q.data[0]
    exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if now > exp:
        _sb().table(WEB_TOKENS_TABLE).update({"revoked": True}).eq("token_hash", token_hash).execute()
        return {"ok": False, "error": "session_expired"}

    _sb().table(WEB_TOKENS_TABLE).update({"last_seen_at": _iso(now)}).eq("token_hash", token_hash).execute()

    return {"ok": True, "account_id": str(row["account_id"])}


# --------------------------------------------------
# AUTH RESOLUTION (cookie OR bearer) — PREFER BEARER FIRST
# --------------------------------------------------
def get_account_id_from_request(flask_request) -> Tuple[Optional[str], str]:
    # 1) Bearer first
    auth = (flask_request.headers.get("Authorization") or "").strip()
    if auth:
        out = require_web_session(auth)
        if out.get("ok"):
            return str(out.get("account_id")), "bearer"

    # 2) Cookie fallback
    raw_cookie = (flask_request.cookies.get(WEB_AUTH_COOKIE_NAME) or "").strip()
    if raw_cookie:
        token_hash = _hash_token(raw_cookie)
        now = _now_utc()

        q = (
            _sb()
            .table(WEB_TOKENS_TABLE)
            .select("*")
            .eq("token_hash", token_hash)
            .eq("revoked", False)
            .limit(1)
            .execute()
        )

        if _sb_data(q):
            row = q.data[0]
            try:
                exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if now <= exp:
                    _sb().table(WEB_TOKENS_TABLE).update({"last_seen_at": _iso(now)}).eq(
                        "token_hash", token_hash
                    ).execute()
                    return str(row["account_id"]), "cookie"
            except Exception:
                pass

    return None, "none"


# --------------------------------------------------
# LOGOUT -> revoked BOOLEAN
# --------------------------------------------------
def logout_web_session(auth_header: str) -> Dict[str, Any]:
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    token_hash = _hash_token(token)
    _sb().table(WEB_TOKENS_TABLE).update({"revoked": True}).eq("token_hash", token_hash).execute()
    return {"ok": True}
