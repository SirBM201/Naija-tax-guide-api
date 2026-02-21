# app/routes/web_auth.py
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request, g

from app.core.supabase_client import supabase
from app.core.auth import require_auth_plus
from app.core.config import (
    WEB_AUTH_ENABLED,
    WEB_TOKEN_TABLE,
    WEB_TOKEN_PEPPER,
)

from app.services.email_service import send_email_otp, smtp_is_configured

bp = Blueprint("web_auth", __name__)

# -------------------------------------------------
# Helpers
# -------------------------------------------------

def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


ENV = _env("ENV", "prod").lower()
WEB_AUTH_DEBUG = (_env("WEB_AUTH_DEBUG", "0") == "1") or (ENV == "dev")

WEB_OTP_TABLE = _env("WEB_OTP_TABLE", "web_otps")
WEB_OTP_PEPPER = _env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)

WEB_SESSION_TTL_DAYS = int(_env("WEB_SESSION_TTL_DAYS", "30") or "30")
WEB_OTP_TTL_MINUTES = int(_env("WEB_OTP_TTL_MINUTES", "10") or "10")

# Dev-only return OTP to API caller (disable in prod)
WEB_DEV_RETURN_OTP = (ENV == "dev")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _otp_hash(contact: str, purpose: str, otp: str) -> str:
    return _sha256_hex(f"{WEB_OTP_PEPPER}:{contact}:{purpose}:{otp}")


def _effective_token_pepper() -> str:
    """
    IMPORTANT: Must match app/core/auth.py behavior.
    auth.py does: os.getenv("WEB_TOKEN_PEPPER", WEB_TOKEN_PEPPER)
    """
    return (_env("WEB_TOKEN_PEPPER", WEB_TOKEN_PEPPER) or "").strip()


def _token_hash(raw_token: str) -> str:
    pepper = _effective_token_pepper()
    return _sha256_hex(f"{pepper}:{raw_token}")


def _normalize_contact(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    # email contact: do not force +234 formatting
    if "@" in v:
        return v.lower()
    # phone normalization
    if v.startswith("0"):
        return "+234" + v[1:]
    if v.startswith("234"):
        return "+" + v
    return v


def _is_email(v: str) -> bool:
    v = (v or "").strip()
    return ("@" in v) and ("." in v)


def _dbg_payload(extra: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safe debug payload: never return raw secrets like pepper or token hashes fully.
    """
    if not WEB_AUTH_DEBUG:
        return {}
    out = dict(extra or {})
    pep = _effective_token_pepper()
    out["debug"] = {
        "env": ENV,
        "web_token_table": WEB_TOKEN_TABLE,
        "web_otp_table": WEB_OTP_TABLE,
        "smtp_configured": bool(smtp_is_configured()),
        "pepper_len": len(pep),
        "pepper_prefix_sha256": _sha256_hex(pep)[:12] if pep else None,
    }
    return out


# -------------------------------------------------
# Column probing (to avoid silent insert failures)
# -------------------------------------------------

def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


def _insert_web_token_row(token_hash: str, account_id: str, expires_at_iso: str) -> Tuple[bool, Optional[str]]:
    """
    Insert token row in the most compatible way, and return (ok, error).
    If table schema differs, we only insert columns that exist.
    """
    payload: Dict[str, Any] = {
        "token_hash": token_hash,
        "account_id": account_id,
        "expires_at": expires_at_iso,
    }

    # Optional columns (insert only if they exist)
    if _has_column(WEB_TOKEN_TABLE, "revoked"):
        payload["revoked"] = False
    if _has_column(WEB_TOKEN_TABLE, "created_at"):
        payload["created_at"] = _now_utc().isoformat()
    if _has_column(WEB_TOKEN_TABLE, "last_seen_at"):
        payload["last_seen_at"] = _now_utc().isoformat()

    try:
        _sb().table(WEB_TOKEN_TABLE).insert(payload).execute()
        return True, None
    except Exception as e:
        # Return full error only in debug mode
        err = f"{type(e).__name__}: {str(e)[:500]}"
        return False, err


def _confirm_token_exists(token_hash: str) -> Tuple[bool, Optional[str]]:
    try:
        res = (
            _sb()
            .table(WEB_TOKEN_TABLE)
            .select("token_hash, account_id, expires_at, revoked")
            .eq("token_hash", token_hash)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return False, "token_row_not_found_after_insert"
        return True, None
    except Exception as e:
        return False, f"confirm_failed:{type(e).__name__}:{str(e)[:300]}"


# -------------------------------------------------
# Account Creation (web accounts)
# -------------------------------------------------

def _upsert_account_for_contact(contact: str) -> Optional[str]:
    try:
        res = (
            _sb()
            .table("accounts")
            .select("account_id")
            .eq("provider", "web")
            .eq("provider_user_id", contact)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            return rows[0].get("account_id")

        insert_res = (
            _sb()
            .table("accounts")
            .insert({
                "provider": "web",
                "provider_user_id": contact,
                "display_name": contact,
                "phone": contact,  # ok even if contact is email; keep simple
            })
            .execute()
        )
        inserted = insert_res.data or []
        if inserted:
            return inserted[0].get("account_id")
        return None

    except Exception as e:
        # Debug print in server logs (safe)
        print("[web_auth] Account creation error:", type(e).__name__, str(e), flush=True)
        return None


# -------------------------------------------------
# REQUEST OTP
# -------------------------------------------------

@bp.post("/request-otp")
@bp.post("/web/auth/request-otp")
def request_otp():
    if not WEB_AUTH_ENABLED:
        return jsonify({"ok": False, "error": "web_auth_disabled"}), 403

    data: Dict[str, Any] = request.get_json(silent=True) or {}

    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip()

    email_to = (str(data.get("email") or "") or "").strip().lower()

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400

    otp = f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(minutes=WEB_OTP_TTL_MINUTES)

    try:
        _sb().table(WEB_OTP_TABLE).insert({
            "contact": contact,
            "purpose": purpose,
            "code_hash": _otp_hash(contact, purpose, otp),
            "expires_at": expires_at.isoformat(),
            "used": False,
            # created_at optional
            **({"created_at": _now_utc().isoformat()} if _has_column(WEB_OTP_TABLE, "created_at") else {}),
        }).execute()
    except Exception as e:
        print("[web_auth] OTP store error:", type(e).__name__, str(e), flush=True)
        base = {"ok": False, "error": "otp_store_failed"}
        return jsonify({**base, **_dbg_payload({"otp_store_err": f"{type(e).__name__}:{str(e)[:400]}"})}), 500

    sent_email = False
    email_err: Optional[str] = None

    dest_email = ""
    if _is_email(contact):
        dest_email = contact
    elif _is_email(email_to):
        dest_email = email_to

    if dest_email:
        email_err = send_email_otp(
            to_email=dest_email,
            otp=otp,
            purpose=purpose,
            ttl_minutes=WEB_OTP_TTL_MINUTES,
        )
        sent_email = (email_err is None)

    resp: Dict[str, Any] = {
        "ok": True,
        "email_sent": bool(sent_email),
        "email_to": dest_email or (contact if _is_email(contact) else None),
        "ttl_minutes": WEB_OTP_TTL_MINUTES,
    }

    if dest_email and email_err:
        resp["email_error"] = email_err

    if WEB_DEV_RETURN_OTP:
        resp["dev_otp"] = otp

    # Always attach safe debug payload only in dev/WEB_AUTH_DEBUG
    resp.update(_dbg_payload({}))

    return jsonify(resp)


# -------------------------------------------------
# VERIFY OTP
# -------------------------------------------------

@bp.post("/verify-otp")
@bp.post("/web/auth/verify-otp")
def verify_otp():
    if not WEB_AUTH_ENABLED:
        return jsonify({"ok": False, "error": "web_auth_disabled"}), 403

    data: Dict[str, Any] = request.get_json(silent=True) or {}

    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip()
    otp = str(data.get("otp") or "").strip()

    if not contact or not otp:
        return jsonify({"ok": False, "error": "invalid_request"}), 400

    code_hash = _otp_hash(contact, purpose, otp)

    q = (
        _sb()
        .table(WEB_OTP_TABLE)
        .select("*")
        .eq("contact", contact)
        .eq("purpose", purpose)
        .eq("code_hash", code_hash)
        .eq("used", False)
        .limit(1)
        .execute()
    )

    rows = q.data or []
    if not rows:
        return jsonify({"ok": False, "error": "invalid_otp"}), 401

    row = rows[0]
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if _now_utc() > exp.astimezone(timezone.utc):
            return jsonify({"ok": False, "error": "otp_expired"}), 401
    except Exception:
        return jsonify({"ok": False, "error": "otp_expired"}), 401

    # mark used
    try:
        _sb().table(WEB_OTP_TABLE).update({
            "used": True,
            **({"used_at": _now_utc().isoformat()} if _has_column(WEB_OTP_TABLE, "used_at") else {}),
        }).eq("id", row["id"]).execute()
    except Exception as e:
        # not fatal, but log it
        print("[web_auth] mark-used failed:", type(e).__name__, str(e), flush=True)

    account_id = _upsert_account_for_contact(contact)
    if not account_id:
        base = {"ok": False, "error": "account_create_failed"}
        return jsonify({**base, **_dbg_payload({"contact": contact})}), 500

    raw_token = secrets.token_hex(32)
    expires_at = _now_utc() + timedelta(days=WEB_SESSION_TTL_DAYS)

    th = _token_hash(raw_token)

    inserted, insert_err = _insert_web_token_row(
        token_hash=th,
        account_id=account_id,
        expires_at_iso=expires_at.isoformat(),
    )

    if not inserted:
        base = {"ok": False, "error": "token_store_failed"}
        return jsonify({**base, **_dbg_payload({
            "token_store_err": insert_err,
            "token_hash_prefix": th[:12],
            "account_id": account_id,
        })}), 500

    confirmed, confirm_err = _confirm_token_exists(th)
    if not confirmed:
        base = {"ok": False, "error": "token_store_unconfirmed"}
        return jsonify({**base, **_dbg_payload({
            "confirm_err": confirm_err,
            "token_hash_prefix": th[:12],
            "account_id": account_id,
        })}), 500

    return jsonify({
        "ok": True,
        "token": raw_token,
        "account_id": account_id,
        "expires_at": expires_at.isoformat(),
        **_dbg_payload({"token_hash_prefix": th[:12]}),
    })


# -------------------------------------------------
# ME
# -------------------------------------------------

@bp.get("/me")
@bp.get("/web/auth/me")
@require_auth_plus
def me():
    account_id = g.account_id

    res = (
        _sb()
        .table("accounts")
        .select("*")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )

    rows = res.data or []
    if not rows:
        return jsonify({"ok": False}), 404

    return jsonify({"ok": True, "account": rows[0]})
