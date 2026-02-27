# app/services/web_auth_service.py
from __future__ import annotations

import os
import time
import secrets
import hashlib
from typing import Any, Dict, Optional, Tuple

from flask import Request

from app.core.supabase_client import get_supabase_client


# -----------------------------
# Exported constants (routes import these)
# -----------------------------
WEB_AUTH_COOKIE_NAME = os.getenv("WEB_SESSION_COOKIE_NAME", "ntg_session")
WEB_AUTH_OTP_TABLE = os.getenv("WEB_OTP_TABLE", "web_otps")
WEB_AUTH_TOKEN_TABLE = os.getenv("WEB_TOKEN_TABLE", "web_tokens")
WEB_AUTH_ACCOUNTS_TABLE = os.getenv("ACCOUNTS_TABLE", "accounts")

OTP_TABLE = WEB_AUTH_OTP_TABLE
TOKEN_TABLE = WEB_AUTH_TOKEN_TABLE
ACCOUNTS_TABLE = WEB_AUTH_ACCOUNTS_TABLE

OTP_PURPOSE_DEFAULT = os.getenv("WEB_OTP_PURPOSE", "web_login")
OTP_TTL_SECONDS = int(os.getenv("WEB_OTP_TTL_SECONDS", "600"))  # 10 mins
OTP_LENGTH = int(os.getenv("WEB_OTP_LENGTH", "6"))
MAX_ATTEMPTS = int(os.getenv("WEB_OTP_MAX_ATTEMPTS", "5"))

SESSION_COOKIE_NAME = WEB_AUTH_COOKIE_NAME

TOKEN_TTL_SECONDS = int(os.getenv("WEB_TOKEN_TTL_SECONDS", "2592000"))  # 30 days
TOKEN_LENGTH_BYTES = int(os.getenv("WEB_TOKEN_BYTES", "32"))

BYPASS_TOKEN = (os.getenv("BYPASS_TOKEN") or "").strip()


# -----------------------------
# Helpers
# -----------------------------
def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ts_plus(seconds: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _generate_numeric_code(n: int) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(n))


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _extract_bearer(req: Request) -> Optional[str]:
    h = (req.headers.get("Authorization") or "").strip()
    if not h:
        return None
    if h.lower().startswith("bearer "):
        return h.split(" ", 1)[1].strip() or None
    return None


def _extract_dev_bypass(req: Request) -> bool:
    if not BYPASS_TOKEN:
        return False
    bearer = _extract_bearer(req)
    if bearer and bearer == BYPASS_TOKEN:
        return True
    x = (req.headers.get("X-Auth-Token") or "").strip()
    if x and x == BYPASS_TOKEN:
        return True
    return False


def _random_token() -> str:
    return secrets.token_urlsafe(TOKEN_LENGTH_BYTES)


def _sb_error_blob(res: Any, op: str, table: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Root-cause exposer for Supabase/PostgREST failures.

    We try to safely include whatever the supabase client exposes (error, status_code, data),
    without leaking secrets. This makes debugging "schema cache" / missing columns obvious.
    """
    blob: Dict[str, Any] = {
        "op": op,
        "table": table,
    }

    if extra:
        blob["extra"] = extra

    err = getattr(res, "error", None)
    if err is not None:
        blob["error"] = str(err)

    # Best-effort extra details (varies by client version)
    sc = getattr(res, "status_code", None)
    if sc is not None:
        blob["status_code"] = sc

    data = getattr(res, "data", None)
    if data is not None:
        # data can be huge; keep it small and safe
        try:
            if isinstance(data, list) and len(data) > 3:
                blob["data_preview"] = data[:3]
                blob["data_count"] = len(data)
            else:
                blob["data_preview"] = data
        except Exception:
            pass

    return blob


def _safe_insert(sb: Any, table: str, row: Dict[str, Any], op: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    try:
        res = sb.table(table).insert(row).execute()
        if getattr(res, "error", None):
            return False, _sb_error_blob(res, op=op, table=table, extra={"row_keys": sorted(list(row.keys()))})
        return True, None
    except Exception as e:
        return False, {"op": op, "table": table, "error": repr(e), "extra": {"row_keys": sorted(list(row.keys()))}}


def _safe_update(sb: Any, table: str, updates: Dict[str, Any], where_col: str, where_val: Any, op: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    try:
        res = sb.table(table).update(updates).eq(where_col, where_val).execute()
        if getattr(res, "error", None):
            return False, _sb_error_blob(
                res,
                op=op,
                table=table,
                extra={"updates_keys": sorted(list(updates.keys())), "where": {where_col: str(where_val)}},
            )
        return True, None
    except Exception as e:
        return False, {
            "op": op,
            "table": table,
            "error": repr(e),
            "extra": {"updates_keys": sorted(list(updates.keys())), "where": {where_col: str(where_val)}},
        }


# -----------------------------
# OTP API
# -----------------------------
def request_email_otp(
    email: str,
    purpose: str | None = None,
    request_ip: str | None = None,
    device_id: str | None = None,
    user_agent: str | None = None,
) -> Dict[str, Any]:
    """
    Generates OTP, stores hash in DB, returns a server-only _otp_plain so the route can email it.
    """
    sb = get_supabase_client(admin=True)

    contact = (email or "").strip().lower()
    if not contact:
        return {"ok": False, "error": "email_required"}

    purpose = (purpose or OTP_PURPOSE_DEFAULT).strip().lower()

    otp_plain = _generate_numeric_code(OTP_LENGTH)
    code_hash = _sha256_hex(otp_plain)
    expires_at = _ts_plus(OTP_TTL_SECONDS)

    row: Dict[str, Any] = {
        "contact": contact,
        "purpose": purpose,
        "code_hash": code_hash,
        "expires_at": expires_at,
        "used": False,
        "used_at": None,
        "attempts": 0,
        "last_attempt_at": None,
        "locked_until": None,
        "request_ip": request_ip,
        "channel": "email",
        # Note: device_id + user_agent are not stored unless your table has those columns.
        # We still capture them in debug for troubleshooting.
    }

    ok, root = _safe_insert(sb, OTP_TABLE, row, op="insert_otp")
    if not ok:
        return {"ok": False, "error": "otp_insert_failed", "root_cause": root}

    return {
        "ok": True,
        "contact": contact,
        "purpose": purpose,
        "expires_at": expires_at,
        "_otp_plain": otp_plain,
        "debug": {
            "tables": {"otp_table": OTP_TABLE, "token_table": TOKEN_TABLE},
            "received": {
                "ip": request_ip,
                "device_id": device_id,
                "user_agent_present": bool(user_agent),
            },
        },
    }


def verify_email_otp(email: str, otp_code: str, purpose: str | None = None) -> Dict[str, Any]:
    sb = get_supabase_client(admin=True)

    contact = (email or "").strip().lower()
    if not contact:
        return {"ok": False, "error": "email_required"}

    otp_code = (otp_code or "").strip()
    if not otp_code:
        return {"ok": False, "error": "otp_required"}

    purpose = (purpose or OTP_PURPOSE_DEFAULT).strip().lower()
    otp_hash = _sha256_hex(otp_code)

    try:
        res = (
            sb.table(OTP_TABLE)
            .select("id, code_hash, expires_at, used, used_at, attempts, locked_until, created_at")
            .eq("contact", contact)
            .eq("purpose", purpose)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        if getattr(res, "error", None):
            return {"ok": False, "error": "otp_lookup_failed", "root_cause": _sb_error_blob(res, op="select_otp", table=OTP_TABLE)}
        rows = res.data or []
    except Exception as e:
        return {"ok": False, "error": "otp_lookup_failed", "root_cause": {"op": "select_otp", "table": OTP_TABLE, "error": repr(e)}}

    chosen = None
    for r in rows:
        if r.get("used") is True or r.get("used_at"):
            continue
        if r.get("locked_until"):
            return {"ok": False, "error": "otp_locked", "locked_until": r["locked_until"]}
        chosen = r
        break

    if not chosen:
        return {"ok": False, "error": "otp_not_found"}

    expires_at = (chosen.get("expires_at") or "").strip()
    if expires_at and expires_at < _now_ts():
        return {"ok": False, "error": "otp_expired"}

    if (chosen.get("code_hash") or "") != otp_hash:
        attempts = int(chosen.get("attempts") or 0) + 1
        updates: Dict[str, Any] = {"attempts": attempts, "last_attempt_at": _now_ts()}

        if attempts >= MAX_ATTEMPTS:
            lock_seconds = int(os.getenv("WEB_OTP_LOCK_SECONDS", "600"))
            updates["locked_until"] = _ts_plus(lock_seconds)

        _safe_update(sb, OTP_TABLE, updates, where_col="id", where_val=chosen["id"], op="update_otp_attempts")
        return {"ok": False, "error": "otp_invalid"}

    ok, root = _safe_update(sb, OTP_TABLE, {"used": True, "used_at": _now_ts()}, where_col="id", where_val=chosen["id"], op="mark_otp_used")
    if not ok:
        return {"ok": False, "error": "otp_mark_used_failed", "root_cause": root}

    return {"ok": True, "contact": contact, "purpose": purpose}


# -----------------------------
# Account + token issuance
# -----------------------------
def _ensure_web_account(contact_email: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    sb = get_supabase_client(admin=True)

    provider = "web"
    provider_user_id = contact_email

    try:
        res = (
            sb.table(ACCOUNTS_TABLE)
            .select("id")
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        if getattr(res, "error", None):
            return None, _sb_error_blob(res, op="select_account", table=ACCOUNTS_TABLE)

        row = (res.data or [None])[0]
        if row and row.get("id"):
            return row["id"], None

        ins = {
            "provider": provider,
            "provider_user_id": provider_user_id,
            "created_at": _now_ts(),
            "updated_at": _now_ts(),
        }
        ok, root = _safe_insert(sb, ACCOUNTS_TABLE, ins, op="insert_account")
        if not ok:
            return None, root

        # Re-select for id (more reliable across client versions)
        res2 = (
            sb.table(ACCOUNTS_TABLE)
            .select("id")
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        if getattr(res2, "error", None):
            return None, _sb_error_blob(res2, op="reselect_account", table=ACCOUNTS_TABLE)

        row2 = (res2.data or [None])[0]
        if row2 and row2.get("id"):
            return row2["id"], None

        return None, {"op": "insert_account", "table": ACCOUNTS_TABLE, "error": "account_create_failed"}
    except Exception as e:
        return None, {"op": "ensure_account", "table": ACCOUNTS_TABLE, "error": repr(e)}


def _issue_web_token(account_id: str) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    sb = get_supabase_client(admin=True)

    token = _random_token()
    expires_at = _ts_plus(TOKEN_TTL_SECONDS)

    row = {
        "token": token,
        "account_id": account_id,
        "created_at": _now_ts(),
        "expires_at": expires_at,
        "revoked_at": None,  # IMPORTANT: must exist in DB OR PostgREST cache must be reloaded
    }

    ok, root = _safe_insert(sb, TOKEN_TABLE, row, op="insert_token")
    if not ok:
        # Return a rich, explicit root cause to UI
        return None, None, root

    return token, expires_at, None


def verify_web_otp_and_issue_token(contact: str, otp: str, purpose: str | None = None) -> Dict[str, Any]:
    contact_email = (contact or "").strip().lower()
    if not contact_email:
        return {"ok": False, "error": "contact_required"}

    v = verify_email_otp(contact_email, otp_code=otp, purpose=purpose)
    if not v.get("ok"):
        return v

    account_id, root = _ensure_web_account(contact_email)
    if root or not account_id:
        return {"ok": False, "error": "account_error", "root_cause": root}

    token, expires_at, root2 = _issue_web_token(account_id)
    if root2 or not token:
        return {"ok": False, "error": "token_issue_failed", "root_cause": root2}

    return {
        "ok": True,
        "account_id": account_id,
        "token": token,
        "expires_at": expires_at,
        "cookie_name": SESSION_COOKIE_NAME,
    }


def logout_web_session(req: Request) -> Dict[str, Any]:
    sb = get_supabase_client(admin=True)

    bearer = _extract_bearer(req)
    if bearer:
        ok, root = _safe_update(sb, TOKEN_TABLE, {"revoked_at": _now_ts()}, where_col="token", where_val=bearer, op="revoke_token_bearer")
        if not ok:
            return {"ok": False, "error": "logout_failed", "root_cause": root}
        return {"ok": True, "logged_out": True, "source": "bearer"}

    cookie_token = (req.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        ok, root = _safe_update(sb, TOKEN_TABLE, {"revoked_at": _now_ts()}, where_col="token", where_val=cookie_token, op="revoke_token_cookie")
        if not ok:
            return {"ok": False, "error": "logout_failed", "root_cause": root}
        return {"ok": True, "logged_out": True, "source": "cookie"}

    return {"ok": True, "logged_out": True, "source": "none"}


# Backwards-compatible exports expected by routes
def request_web_otp(
    contact: str,
    purpose: str | None = None,
    device_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    return request_email_otp(contact, purpose=purpose, request_ip=ip, device_id=device_id, user_agent=user_agent)


def verify_web_otp(
    contact: str,
    otp: str,
    purpose: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    return verify_email_otp(contact, otp_code=otp, purpose=purpose)


def get_account_id_from_request(req: Request) -> Tuple[Optional[str], Dict[str, Any]]:
    debug: Dict[str, Any] = {
        "cookie": {"name": SESSION_COOKIE_NAME},
        "tables": {"token_table": TOKEN_TABLE, "accounts_table": ACCOUNTS_TABLE},
    }

    if _extract_dev_bypass(req):
        debug["source"] = "bypass"
        debug["bypass"] = True
        return None, debug

    sb = get_supabase_client(admin=True)

    bearer = _extract_bearer(req)
    if bearer:
        debug["source"] = "bearer"
        try:
            res = (
                sb.table(TOKEN_TABLE)
                .select("account_id, expires_at, revoked_at")
                .eq("token", bearer)
                .limit(1)
                .execute()
            )
            if getattr(res, "error", None):
                debug["token_lookup_error"] = _sb_error_blob(res, op="select_token_bearer", table=TOKEN_TABLE)
            else:
                row = (res.data or [None])[0]
                if row and row.get("account_id") and not row.get("revoked_at"):
                    return row["account_id"], debug
        except Exception as e:
            debug["token_error"] = repr(e)

    cookie_token = (req.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        debug["source"] = "cookie"
        try:
            res = (
                sb.table(TOKEN_TABLE)
                .select("account_id, expires_at, revoked_at")
                .eq("token", cookie_token)
                .limit(1)
                .execute()
            )
            if getattr(res, "error", None):
                debug["cookie_lookup_error"] = _sb_error_blob(res, op="select_token_cookie", table=TOKEN_TABLE)
            else:
                row = (res.data or [None])[0]
                if row and row.get("account_id") and not row.get("revoked_at"):
                    return row["account_id"], debug
        except Exception as e:
            debug["cookie_error"] = repr(e)

    debug["source"] = "none"
    return None, debug
