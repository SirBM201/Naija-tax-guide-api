# app/services/web_auth_service.py
from __future__ import annotations

import os
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase
from app.core.auth import token_hash  # ✅ MUST match middleware hashing


# --------------------------------------------------
# Time helpers
# --------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------
# Env helpers
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

WEB_AUTH_TOKEN_TTL_DAYS = int(_env("WEB_AUTH_TOKEN_TTL_DAYS", "30") or "30")

# OTP hash pepper (token hash pepper is handled by app.core.auth.token_hash)
OTP_HASH_PEPPER = _env("OTP_HASH_PEPPER", _env("ADMIN_API_KEY", "dev-otp-pepper"))

WEB_AUTH_COOKIE_NAME = _env("WEB_AUTH_COOKIE_NAME", _env("WEB_COOKIE_NAME", "ntg_session"))

# Tables
WEB_OTPS_TABLE = _env("WEB_OTPS_TABLE", "web_otps")
WEB_TOKENS_TABLE = _env("WEB_TOKENS_TABLE", "web_tokens")
ACCOUNTS_TABLE = _env("ACCOUNTS_TABLE", "accounts")


def _sb():
    return supabase() if callable(supabase) else supabase


# --------------------------------------------------
# Hashing (OTP only)
# --------------------------------------------------
def _hmac_sha256(value: str) -> str:
    pepper = (OTP_HASH_PEPPER or "dev-otp-pepper").encode()
    return hmac.new(pepper, value.encode(), hashlib.sha256).hexdigest()


def _hash_otp(contact: str, purpose: str, otp: str) -> str:
    return _hmac_sha256(f"otp:{purpose}:{contact}:{otp}")


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
# Accounts: canonical GLOBAL id = accounts.account_id
# --------------------------------------------------
def _get_or_create_web_account(contact: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Returns (ok, global_account_id, debug)
    global_account_id = accounts.account_id (preferred)
    fallback: accounts.id, but we repair by backfilling account_id=id.
    """
    debug: Dict[str, Any] = {"contact": contact, "table": ACCOUNTS_TABLE}

    try:
        res = (
            _sb()
            .table(ACCOUNTS_TABLE)
            .select("id,account_id,provider,provider_user_id")
            .eq("provider", "web")
            .eq("provider_user_id", contact)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            row = rows[0] or {}
            pk = (row.get("id") or "").strip() if isinstance(row.get("id"), str) else row.get("id")
            gid = (row.get("account_id") or "").strip() if isinstance(row.get("account_id"), str) else row.get("account_id")

            # Repair missing account_id
            if not gid and pk:
                try:
                    _sb().table(ACCOUNTS_TABLE).update({"account_id": str(pk)}).eq("id", str(pk)).execute()
                    gid = str(pk)
                    debug["repaired_account_id"] = True
                except Exception as e:
                    return False, None, {
                        **debug,
                        "error": "account_id_missing_repair_failed",
                        "exception": f"{type(e).__name__}: {str(e)[:180]}",
                        "fix_sql": "update public.accounts set account_id = id where account_id is null;",
                    }

            if gid:
                return True, str(gid), debug

            return False, None, {**debug, "error": "account_missing_ids", "row_keys": list(row.keys())[:20]}
    except Exception as e:
        return False, None, {**debug, "error": "accounts_lookup_failed", "exception": f"{type(e).__name__}: {str(e)[:180]}"}

    # Create new account
    try:
        created = (
            _sb()
            .table(ACCOUNTS_TABLE)
            .insert({"provider": "web", "provider_user_id": contact, "display_name": contact, "phone_e164": contact})
            .select("id,account_id")
            .execute()
        )
        row = (getattr(created, "data", None) or [{}])[0]
        pk = row.get("id")
        gid = row.get("account_id") or pk

        # Backfill account_id if absent
        if pk and not row.get("account_id"):
            try:
                _sb().table(ACCOUNTS_TABLE).update({"account_id": str(pk)}).eq("id", str(pk)).execute()
                gid = str(pk)
                debug["repaired_account_id"] = True
            except Exception as e:
                return False, None, {
                    **debug,
                    "error": "account_create_missing_account_id",
                    "exception": f"{type(e).__name__}: {str(e)[:180]}",
                    "fix_sql": "update public.accounts set account_id = id where account_id is null;",
                }

        if not gid:
            return False, None, {**debug, "error": "account_create_failed_no_ids", "row": row}

        return True, str(gid), debug
    except Exception as e:
        return False, None, {**debug, "error": "account_create_failed", "exception": f"{type(e).__name__}: {str(e)[:180]}"}


# --------------------------------------------------
# OTP REQUEST
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
    try:
        _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("contact", contact).eq(
            "purpose", purpose
        ).is_("used_at", "null").is_("revoked_at", "null").execute()
    except Exception:
        pass

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
            "debug": {"exception": f"{type(e).__name__}: {str(e)[:200]}", "table": WEB_OTPS_TABLE},
        }

    out: Dict[str, Any] = {"ok": True, "ttl_minutes": int(int(WEB_AUTH_OTP_TTL_SECONDS) / 60)}
    if WEB_AUTH_DEV_OTP_ENABLED:
        out["dev_otp"] = otp
    return out


# --------------------------------------------------
# OTP VERIFY -> TOKEN ISSUE
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
        ok, account_id, dbg = _get_or_create_web_account(contact)
        if not ok:
            return {"ok": False, "error": "account_resolve_failed", "debug": dbg}
        tok = _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id)
        return tok

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
        return {"ok": False, "error": "otp_lookup_failed", "debug": {"exception": f"{type(e).__name__}: {str(e)[:180]}"}}

    rows = getattr(q, "data", None) or []
    if not rows:
        return {"ok": False, "error": "invalid_or_expired_otp"}

    row = rows[0] or {}
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        if now > exp:
            try:
                _sb().table(WEB_OTPS_TABLE).update({"revoked_at": _iso(now)}).eq("id", row["id"]).execute()
            except Exception:
                pass
            return {"ok": False, "error": "otp_expired"}
    except Exception:
        # if expires_at parsing fails, fail safely
        return {"ok": False, "error": "otp_expiry_parse_failed", "debug": {"expires_at": row.get("expires_at")}}

    try:
        _sb().table(WEB_OTPS_TABLE).update({"used_at": _iso(now)}).eq("id", row["id"]).execute()
    except Exception:
        pass

    ok, account_id, dbg = _get_or_create_web_account(contact)
    if not ok:
        return {"ok": False, "error": "account_resolve_failed", "debug": dbg}

    return _create_web_token(account_id, ip=ip, user_agent=user_agent, device_id=device_id, debug_parent=dbg)


# --------------------------------------------------
# TOKEN CREATE (web_tokens)
# --------------------------------------------------
def _create_web_token(
    account_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_id: Optional[str] = None,
    debug_parent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_token = secrets.token_hex(32)
    th = token_hash(raw_token)  # ✅ must match app/core/auth.py
    now = _now_utc()
    expires_at = now + timedelta(days=int(WEB_AUTH_TOKEN_TTL_DAYS))

    payload: Dict[str, Any] = {
        "account_id": account_id,      # ✅ MUST be accounts.account_id
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
        msg = str(e)
        # Failure exposer with the *most useful next-step*
        return {
            "ok": False,
            "error": "token_issue_failed",
            "why": "token_insert_failed",
            "debug": {
                "account_id_used": account_id,
                "token_table": WEB_TOKENS_TABLE,
                "accounts_table": ACCOUNTS_TABLE,
                "exception": f"{type(e).__name__}: {msg[:220]}",
                "root_cause_hint": "If you see ERROR 23503 FK violation, your web_tokens.account_id FK is pointing to a different accounts column than what you're inserting.",
                "recommended_fix": {
                    "make_accounts_account_id_present": "update public.accounts set account_id = id where account_id is null;",
                    "make_accounts_account_id_unique": "create unique index if not exists uq_accounts_account_id on public.accounts(account_id);",
                    "fix_fk": "alter table public.web_tokens drop constraint if exists fk_web_tokens_account; "
                              "alter table public.web_tokens add constraint fk_web_tokens_account foreign key (account_id) references public.accounts(account_id) on delete cascade;",
                },
                "accounts_resolve_debug": debug_parent or {},
            },
        }

    return {
        "ok": True,
        "account_id": account_id,
        "auth_mode": "cookie+bearer",
        "token": raw_token,
        "expires_at": _iso(expires_at),
        "cookie": {"name": WEB_AUTH_COOKIE_NAME, "secure": True, "samesite": "None"},
    }
