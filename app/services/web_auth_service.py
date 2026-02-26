# app/services/web_auth_service.py
from __future__ import annotations

import hashlib
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
# Env helpers (keep light imports to avoid boot surprises)
# --------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug_enabled() -> bool:
    return _truthy(_env("AUTH_DEBUG", "0"))


def _dbg(msg: str) -> None:
    if _debug_enabled():
        print(msg, flush=True)


# --------------------------------------------------
# Feature flags
# --------------------------------------------------
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

# Cookie name
WEB_AUTH_COOKIE_NAME = _env("WEB_AUTH_COOKIE_NAME", _env("WEB_COOKIE_NAME", "ntg_session"))

# Tables
WEB_OTPS_TABLE = _env("WEB_OTPS_TABLE", "web_otps")
WEB_TOKENS_TABLE = _env("WEB_TOKENS_TABLE", "web_tokens")
ACCOUNTS_TABLE = _env("ACCOUNTS_TABLE", "accounts")

# Pepper used for WEB TOKENS (must match app/core/auth.py behavior: sha256(f"{pepper}:{token}"))
WEB_TOKEN_PEPPER = _env("WEB_TOKEN_PEPPER", _env("OTP_HASH_PEPPER", _env("ADMIN_API_KEY", "dev-pepper")))


def _sb():
    return supabase() if callable(supabase) else supabase


def auth_debug_snapshot() -> Dict[str, Any]:
    # Safe debug snapshot (no secrets)
    return {
        "WEB_AUTH_ENABLED": WEB_AUTH_ENABLED,
        "WEB_AUTH_COOKIE_NAME": WEB_AUTH_COOKIE_NAME,
        "WEB_OTPS_TABLE": WEB_OTPS_TABLE,
        "WEB_TOKENS_TABLE": WEB_TOKENS_TABLE,
        "ACCOUNTS_TABLE": ACCOUNTS_TABLE,
        "WEB_AUTH_TOKEN_TTL_DAYS": WEB_AUTH_TOKEN_TTL_DAYS,
        "WEB_AUTH_OTP_TTL_SECONDS": WEB_AUTH_OTP_TTL_SECONDS,
        "pepper_len": len(WEB_TOKEN_PEPPER or ""),
        "pepper_prefix_sha256": hashlib.sha256((WEB_TOKEN_PEPPER or "").encode()).hexdigest()[:12],
        "AUTH_DEBUG": _debug_enabled(),
    }


# --------------------------------------------------
# Hashing
# --------------------------------------------------
def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def token_hash(raw_token: str) -> str:
    """
    MUST match app/core/auth.py token_hash behavior:
      sha256(f"{pepper}:{raw_token}")
    """
    pepper = (WEB_TOKEN_PEPPER or "dev-pepper").strip()
    return _sha256_hex(f"{pepper}:{raw_token}")


def _hash_otp(contact: str, purpose: str, otp: str) -> str:
    """
    OTP hash can also use the same pepper (fine).
    """
    pepper = (WEB_TOKEN_PEPPER or "dev-pepper").strip()
    return _sha256_hex(f"{pepper}:otp:{purpose}:{contact}:{otp}")


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
        return "web_auth_disabled"
    if not WEB_AUTH_DEV_OTP_ENABLED:
        return "dev_otp_disabled"

    if WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST and contact not in WEB_AUTH_DEV_ALLOWED_CONTACTS_LIST:
        return "dev_contact_not_allowed"

    if WEB_AUTH_DEV_SHARED_SECRET:
        if not shared_secret or shared_secret != WEB_AUTH_DEV_SHARED_SECRET:
            return "invalid_shared_secret"

    return None


# --------------------------------------------------
# Account binding (provider=web, provider_user_id=contact)
# CANONICAL ACCOUNT KEY = accounts.account_id
# --------------------------------------------------
def _extract_account_key(row: Dict[str, Any]) -> Optional[str]:
    """
    Canonical account identifier for the whole system is accounts.account_id.
    If account_id is missing, we fall back to id and immediately repair it.
    """
    v = row.get("account_id") or row.get("id")
    return str(v) if v else None


def _ensure_accounts_account_id(row: Dict[str, Any]) -> None:
    """
    Best-effort repair:
      if accounts.account_id is null, set it to accounts.id (stable, unique),
      so FK targets and app identity remain consistent.
    """
    try:
        acc_id = row.get("id")
        account_id = row.get("account_id")
        if acc_id and not account_id:
            _sb().table(ACCOUNTS_TABLE).update({"account_id": str(acc_id)}).eq("id", str(acc_id)).execute()
    except Exception as e:
        _dbg(f"[web_auth] account_id repair skipped: {type(e).__name__}: {str(e)[:160]}")


def _get_or_create_web_account(contact: str) -> Tuple[bool, Optional[str], Optional[str], Dict[str, Any]]:
    """
    Returns: (ok, account_id, err_code, debug)
      account_id = canonical accounts.account_id
    """
    debug: Dict[str, Any] = {"contact": contact}

    try:
        q = (
            _sb()
            .table(ACCOUNTS_TABLE)
            .select("id, account_id, provider, provider_user_id")
            .eq("provider", "web")
            .eq("provider_user_id", contact)
            .limit(1)
            .execute()
        )

        if getattr(q, "data", None):
            row = q.data[0]
            _ensure_accounts_account_id(row)
            key = _extract_account_key(row)
            if not key:
                return False, None, "account_missing_ids", {"row": row}
            return True, key, None, {"found": True, "row": row}

    except Exception as e:
        return False, None, "account_lookup_failed", {
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check accounts table schema and Supabase permissions / RLS.",
        }

    # Create new account
    try:
        ins = (
            _sb()
            .table(ACCOUNTS_TABLE)
            .insert(
                {
                    "provider": "web",
                    "provider_user_id": contact,
                    "display_name": contact,
                    "phone_e164": contact,  # harmless for email-based login
                }
            )
            .select("id, account_id")
            .execute()
        )

        if not getattr(ins, "data", None):
            return False, None, "account_create_failed", {
                "root_cause": "insert_returned_no_rows",
                "fix": "Check accounts table insert permissions / RLS and required columns.",
            }

        row = ins.data[0]
        _ensure_accounts_account_id(row)
        key = _extract_account_key(row)
        if not key:
            return False, None, "account_created_but_no_account_id", {"row": row}

        return True, key, None, {"created": True, "row": row}

    except Exception as e:
        return False, None, "account_create_exception", {
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check accounts table constraints (provider/provider_user_id unique, required columns).",
        }


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
            return {"ok": False, "error": err, "debug": auth_debug_snapshot() if _debug_enabled() else {}}

    now = _now_utc()

    # Revoke old unused OTPs (best-effort)
    try:
        _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("contact", contact).eq(
            "purpose", purpose
        ).is_("used_at", "null").is_("revoked_at", "null").execute()
    except Exception as e:
        _dbg(f"[web_auth] revoke old otps skipped: {type(e).__name__}: {str(e)[:160]}")

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

    try:
        _sb().table(WEB_OTPS_TABLE).insert(payload).execute()
    except Exception as e:
        return {
            "ok": False,
            "error": "otp_insert_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": f"Check table {WEB_OTPS_TABLE} columns, constraints, and RLS permissions.",
            "debug": auth_debug_snapshot() if _debug_enabled() else {},
        }

    out: Dict[str, Any] = {
        "ok": True,
        "ttl_minutes": int(int(WEB_AUTH_OTP_TTL_SECONDS) / 60),
    }
    if WEB_AUTH_DEV_OTP_ENABLED:
        out["dev_otp"] = otp
    return out


# --------------------------------------------------
# OTP VERIFY (web_otps -> web_tokens)
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
        ok, account_id, err, dbg = _get_or_create_web_account(contact)
        if not ok:
            return {"ok": False, "error": err, "debug": dbg}
        tok = _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id)
        if not tok.get("ok"):
            return tok
        return {
            "ok": True,
            "account_id": account_id,
            "auth_mode": "cookie+bearer",
            "token": tok["token"],
            "expires_at": tok["expires_at"],
        }

    code_hash = _hash_otp(contact, purpose, otp)

    try:
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
    except Exception as e:
        return {
            "ok": False,
            "error": "otp_lookup_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": f"Check {WEB_OTPS_TABLE} schema + RLS permissions.",
            "debug": auth_debug_snapshot() if _debug_enabled() else {},
        }

    if not getattr(q, "data", None):
        return {"ok": False, "error": "invalid_or_expired_otp"}

    row = q.data[0]

    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    except Exception:
        return {
            "ok": False,
            "error": "otp_bad_expiry_format",
            "root_cause": "expires_at is not ISO datetime",
            "details": {"expires_at": row.get("expires_at")},
            "fix": "Ensure web_otps.expires_at is timestamptz.",
        }

    if now > exp:
        try:
            _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("id", row["id"]).execute()
        except Exception:
            pass
        return {"ok": False, "error": "otp_expired"}

    # mark used
    try:
        _sb().table(WEB_OTPS_TABLE).update({"used_at": _iso(now)}).eq("id", row["id"]).execute()
    except Exception as e:
        return {
            "ok": False,
            "error": "otp_mark_used_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check update permissions on web_otps (RLS).",
        }

    ok, account_id, err, dbg = _get_or_create_web_account(contact)
    if not ok:
        return {"ok": False, "error": err, "debug": dbg}

    tok = _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id)
    if not tok.get("ok"):
        # bubble with full exposer
        return tok

    return {
        "ok": True,
        "account_id": account_id,
        "auth_mode": "cookie+bearer",
        "token": tok["token"],
        "expires_at": tok["expires_at"],
    }


# --------------------------------------------------
# TOKEN CREATE (web_tokens)
# account_id stored MUST match accounts.account_id (FK target)
# --------------------------------------------------
def _create_web_token(
    account_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Dict[str, Any]:
    raw_token = secrets.token_hex(32)  # 64 hex chars
    th = token_hash(raw_token)
    now = _now_utc()
    expires_at = now + timedelta(days=int(WEB_AUTH_TOKEN_TTL_DAYS))

    payload: Dict[str, Any] = {
        "account_id": account_id,     # ✅ accounts.account_id (canonical)
        "token_hash": th,
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

    try:
        _sb().table(WEB_TOKENS_TABLE).insert(payload).execute()
    except Exception as e:
        # This is where your FK error shows up; expose it clearly.
        return {
            "ok": False,
            "error": "token_insert_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:260]}",
            "details": {
                "account_id_used": account_id,
                "token_hash_prefix": th[:12],
                "table": WEB_TOKENS_TABLE,
            },
            "fix": (
                "Ensure web_tokens.account_id FK references accounts.account_id "
                "AND accounts.account_id is populated + unique. "
                "If account_id is missing in accounts rows, run: update accounts set account_id=id where account_id is null;"
            ),
            "debug": auth_debug_snapshot() if _debug_enabled() else {},
        }

    return {"ok": True, "token": raw_token, "expires_at": _iso(expires_at)}


# --------------------------------------------------
# TOKEN VALIDATION (bearer) -> revoked BOOLEAN
# --------------------------------------------------
def require_web_session(auth_header: str) -> Dict[str, Any]:
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    th = token_hash(token)
    now = _now_utc()

    try:
        q = (
            _sb()
            .table(WEB_TOKENS_TABLE)
            .select("*")
            .eq("token_hash", th)
            .eq("revoked", False)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return {
            "ok": False,
            "error": "token_lookup_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": f"Check table {WEB_TOKENS_TABLE} schema + RLS permissions.",
            "debug": auth_debug_snapshot() if _debug_enabled() else {},
        }

    if not getattr(q, "data", None):
        return {"ok": False, "error": "invalid_token"}

    row = q.data[0]

    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    except Exception:
        return {"ok": False, "error": "token_bad_expiry_format"}

    if now > exp:
        try:
            _sb().table(WEB_TOKENS_TABLE).update({"revoked": True}).eq("token_hash", th).execute()
        except Exception:
            pass
        return {"ok": False, "error": "token_expired"}

    # touch last_seen_at best-effort
    try:
        _sb().table(WEB_TOKENS_TABLE).update({"last_seen_at": _iso(now)}).eq("token_hash", th).execute()
    except Exception:
        pass

    return {"ok": True, "account_id": str(row.get("account_id"))}


# --------------------------------------------------
# AUTH RESOLUTION (cookie OR bearer)
# Exported for app/routes/ask.py
# --------------------------------------------------
def get_account_id_from_request(flask_request) -> Tuple[Optional[str], str]:
    """
    Returns (account_id, source)
      source: 'cookie' | 'bearer' | 'none'
    """
    # 1) Cookie-first (matches your core/auth.py preference)
    raw_cookie = (flask_request.cookies.get(WEB_AUTH_COOKIE_NAME) or "").strip()
    if raw_cookie:
        th = token_hash(raw_cookie)
        now = _now_utc()
        try:
            q = (
                _sb()
                .table(WEB_TOKENS_TABLE)
                .select("*")
                .eq("token_hash", th)
                .eq("revoked", False)
                .limit(1)
                .execute()
            )
            if getattr(q, "data", None):
                row = q.data[0]
                exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if now <= exp:
                    try:
                        _sb().table(WEB_TOKENS_TABLE).update({"last_seen_at": _iso(now)}).eq("token_hash", th).execute()
                    except Exception:
                        pass
                    return str(row.get("account_id")), "cookie"
        except Exception as e:
            _dbg(f"[web_auth] cookie token lookup failed: {type(e).__name__}: {str(e)[:200]}")

    # 2) Bearer fallback
    auth = (flask_request.headers.get("Authorization") or "").strip()
    if auth:
        out = require_web_session(auth)
        if out.get("ok"):
            return str(out.get("account_id")), "bearer"

    return None, "none"


# --------------------------------------------------
# LOGOUT -> revoked BOOLEAN
# --------------------------------------------------
def logout_web_session(auth_header: str) -> Dict[str, Any]:
    token = _normalize_bearer(auth_header)
    if not token:
        return {"ok": False, "error": "missing_token"}

    th = token_hash(token)
    try:
        _sb().table(WEB_TOKENS_TABLE).update({"revoked": True}).eq("token_hash", th).execute()
    except Exception as e:
        return {
            "ok": False,
            "error": "logout_failed",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": f"Check update permissions on {WEB_TOKENS_TABLE} (RLS).",
        }
    return {"ok": True}
