# app/routes/web_auth.py
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, g

from app.core.supabase_client import supabase
from app.core.auth import require_auth_plus
from app.core.config import (
    WEB_AUTH_ENABLED,
    WEB_TOKEN_TABLE,
    WEB_TOKEN_PEPPER,
)

bp = Blueprint("web_auth", __name__)

# -----------------------------
# Helpers
# -----------------------------
def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "y", "on")


ENV = _env("ENV", "prod").lower()
WEB_AUTH_DEBUG = _truthy(_env("WEB_AUTH_DEBUG", "0"))

WEB_OTP_TABLE = _env("WEB_OTP_TABLE", "web_otps")
WEB_OTP_PEPPER = _env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)
WEB_SESSION_TTL_DAYS = int(_env("WEB_SESSION_TTL_DAYS", "30") or "30")
WEB_OTP_TTL_MINUTES = int(_env("WEB_OTP_TTL_MINUTES", "10") or "10")

WEB_DEV_RETURN_OTP = _truthy(_env("WEB_DEV_RETURN_OTP", "0")) or (ENV == "dev")

# --- Column mapping (match your Supabase schema) ---
OTP_COL_CONTACT = _env("WEB_OTP_COL_CONTACT", "contact")
OTP_COL_PURPOSE = _env("WEB_OTP_COL_PURPOSE", "purpose")
OTP_COL_CODE_HASH = _env("WEB_OTP_COL_CODE_HASH", "code_hash")
OTP_COL_CODE_PLAIN = _env("WEB_OTP_COL_CODE_PLAIN", "code_plain")
OTP_COL_PHONE_E164 = _env("WEB_OTP_COL_PHONE_E164", "phone_e164")
OTP_COL_OTP_CODE = _env("WEB_OTP_COL_OTP_CODE", "otp_code")
OTP_COL_EXPIRES_AT = _env("WEB_OTP_COL_EXPIRES_AT", "expires_at")
OTP_COL_USED = _env("WEB_OTP_COL_USED", "used")
OTP_COL_USED_AT = _env("WEB_OTP_COL_USED_AT", "used_at")


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _otp_hash(contact: str, purpose: str, otp: str) -> str:
    return _sha256_hex(f"{WEB_OTP_PEPPER}:{contact}:{purpose}:{otp}")


def _token_hash(raw_token: str) -> str:
    return _sha256_hex(f"{WEB_TOKEN_PEPPER}:{raw_token}")


def _normalize_contact(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    if v.startswith("+"):
        return v
    if v.startswith("234"):
        return "+" + v
    if v.startswith("0"):
        return "+234" + v[1:]
    return v


def _extract_supabase_error(e: Exception) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "type": e.__class__.__name__,
        "message": str(e),
    }
    for k in ("code", "details", "hint", "status", "status_code"):
        if hasattr(e, k):
            try:
                info[k] = getattr(e, k)
            except Exception:
                pass
    try:
        if getattr(e, "args", None):
            info["args"] = [str(a) for a in e.args[:3]]
    except Exception:
        pass
    try:
        first = e.args[0] if getattr(e, "args", None) else None
        if isinstance(first, dict):
            info["payload"] = first
    except Exception:
        pass
    return info


def _upsert_account_for_contact(contact: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Returns (account_id, error_info_if_any)

    This will:
      1) select existing by (provider='web', provider_user_id=contact)
      2) try insert WITHOUT account_id (best if DB has default uuid)
      3) fallback: insert WITH uuid
    """
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
        rows = (res.data or []) if hasattr(res, "data") else []
        if rows and rows[0].get("account_id"):
            return rows[0].get("account_id"), None
    except Exception as e:
        # even lookup failed -> return details in debug
        err = _extract_supabase_error(e)
        print(f"[web_auth] accounts lookup failed: {err}")
        return None, err

    # Try insert WITHOUT account_id (let DB default generate UUID)
    try:
        payload = {
            "provider": "web",
            "provider_user_id": contact,
            "display_name": contact,
            "phone": contact,
        }
        ins = _sb().table("accounts").insert(payload).execute()
        data = (ins.data or []) if hasattr(ins, "data") else []
        if data and isinstance(data, list) and data[0].get("account_id"):
            return data[0].get("account_id"), None

        # if insert didn't return row, re-select
        res2 = (
            _sb()
            .table("accounts")
            .select("account_id")
            .eq("provider", "web")
            .eq("provider_user_id", contact)
            .limit(1)
            .execute()
        )
        rows2 = (res2.data or []) if hasattr(res2, "data") else []
        if rows2 and rows2[0].get("account_id"):
            return rows2[0].get("account_id"), None

    except Exception as e:
        err = _extract_supabase_error(e)
        print(f"[web_auth] accounts insert (no account_id) failed: {err}")

        # Fallback: try insert WITH uuid (some schemas require explicit account_id)
        try:
            account_id = str(uuid.uuid4())
            payload2 = {
                "account_id": account_id,
                "provider": "web",
                "provider_user_id": contact,
                "display_name": contact,
                "phone": contact,
            }
            _sb().table("accounts").insert(payload2).execute()
            return account_id, None
        except Exception as e2:
            err2 = _extract_supabase_error(e2)
            print(f"[web_auth] accounts insert (with uuid) failed: {err2}")
            return None, err2

    return None, {"type": "UnknownError", "message": "account_insert_failed_without_exception"}


# -----------------------------
# Routes
# -----------------------------
@bp.post("/request-otp")
@bp.post("/web/auth/request-otp")
def request_otp():
    if not WEB_AUTH_ENABLED:
        return jsonify({"ok": False, "error": "web_auth_disabled"}), 403

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip() or "web_login"

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400

    otp = "123456" if ENV == "dev" else f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(minutes=WEB_OTP_TTL_MINUTES)
    code_hash = _otp_hash(contact, purpose, otp)

    payload = {
        OTP_COL_CONTACT: contact,
        OTP_COL_PURPOSE: purpose,
        OTP_COL_CODE_HASH: code_hash,
        OTP_COL_CODE_PLAIN: otp if WEB_DEV_RETURN_OTP else None,
        OTP_COL_PHONE_E164: contact,
        OTP_COL_OTP_CODE: otp if WEB_DEV_RETURN_OTP else None,
        OTP_COL_EXPIRES_AT: expires_at.isoformat().replace("+00:00", "Z"),
        OTP_COL_USED: False,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        _sb().table(WEB_OTP_TABLE).insert(payload).execute()
    except Exception as e:
        err = _extract_supabase_error(e)
        print(f"[web_auth] request_otp insert failed: {err}")
        if ENV == "dev" or WEB_AUTH_DEBUG:
            return jsonify({"ok": False, "error": "otp_store_failed", "supabase": err}), 500
        return jsonify({"ok": False, "error": "otp_store_failed"}), 500

    resp = {"ok": True, "contact": contact, "purpose": purpose}
    if WEB_DEV_RETURN_OTP:
        resp["dev_otp"] = otp
    return jsonify(resp)


@bp.post("/verify-otp")
@bp.post("/web/auth/verify-otp")
def verify_otp():
    if not WEB_AUTH_ENABLED:
        return jsonify({"ok": False, "error": "web_auth_disabled"}), 403

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip() or "web_login"
    otp = str(data.get("otp") or "").strip()

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400
    if not otp:
        return jsonify({"ok": False, "error": "missing_otp"}), 400

    code_hash = _otp_hash(contact, purpose, otp)

    try:
        q = (
            _sb()
            .table(WEB_OTP_TABLE)
            .select(f"id,{OTP_COL_EXPIRES_AT},{OTP_COL_USED}")
            .eq(OTP_COL_CONTACT, contact)
            .eq(OTP_COL_PURPOSE, purpose)
            .eq(OTP_COL_CODE_HASH, code_hash)
            .eq(OTP_COL_USED, False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = (q.data or []) if hasattr(q, "data") else []
    except Exception as e:
        err = _extract_supabase_error(e)
        print(f"[web_auth] verify_otp lookup failed: {err}")
        if ENV == "dev" or WEB_AUTH_DEBUG:
            return jsonify({"ok": False, "error": "otp_lookup_failed", "supabase": err}), 500
        return jsonify({"ok": False, "error": "otp_lookup_failed"}), 500

    if not rows:
        return jsonify({"ok": False, "error": "invalid_otp"}), 401

    row = rows[0]
    exp_raw = (row.get(OTP_COL_EXPIRES_AT) or "").replace("Z", "+00:00")
    try:
        exp = datetime.fromisoformat(exp_raw)
    except Exception:
        return jsonify({"ok": False, "error": "otp_expiry_parse_failed"}), 500

    if _now_utc() > exp:
        try:
            _sb().table(WEB_OTP_TABLE).update(
                {OTP_COL_USED: True, OTP_COL_USED_AT: _now_utc().isoformat().replace("+00:00", "Z")}
            ).eq("id", row.get("id")).execute()
        except Exception:
            pass
        return jsonify({"ok": False, "error": "otp_expired"}), 401

    try:
        _sb().table(WEB_OTP_TABLE).update(
            {OTP_COL_USED: True, OTP_COL_USED_AT: _now_utc().isoformat().replace("+00:00", "Z")}
        ).eq("id", row.get("id")).execute()
    except Exception:
        pass

    # Ensure account exists (NOW returns error detail)
    account_id, acct_err = _upsert_account_for_contact(contact)
    if not account_id:
        if ENV == "dev" or WEB_AUTH_DEBUG:
            return jsonify(
                {
                    "ok": False,
                    "error": "account_create_failed",
                    "supabase": acct_err,
                    "hint": "Most common causes: (1) accounts table schema mismatch (missing provider/provider_user_id/account_id), (2) account_id is UUID but insert is failing due to constraints/defaults, (3) RLS/policies if not using service role.",
                }
            ), 500
        return jsonify({"ok": False, "error": "account_create_failed"}), 500

    raw_token = secrets.token_hex(32)
    expires_at = _now_utc() + timedelta(days=WEB_SESSION_TTL_DAYS)

    try:
        _sb().table(WEB_TOKEN_TABLE).insert(
            {
                "token_hash": _token_hash(raw_token),
                "account_id": account_id,
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "revoked": False,
                "last_seen_at": _now_utc().isoformat().replace("+00:00", "Z"),
            }
        ).execute()
    except Exception as e:
        err = _extract_supabase_error(e)
        print(f"[web_auth] session insert failed: {err}")
        if ENV == "dev" or WEB_AUTH_DEBUG:
            return jsonify({"ok": False, "error": "session_store_failed", "supabase": err}), 500
        return jsonify({"ok": False, "error": "session_store_failed"}), 500

    return jsonify(
        {
            "ok": True,
            "mode": "real",
            "token": raw_token,
            "account_id": account_id,
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        }
    )


@bp.get("/me")
@bp.get("/web/auth/me")
@require_auth_plus
def me():
    account_id = getattr(g, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account"}), 401

    try:
        res = (
            _sb()
            .table("accounts")
            .select("account_id, provider, provider_user_id, display_name, phone, created_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if not rows:
            return jsonify({"ok": False, "error": "account_not_found"}), 404
        return jsonify({"ok": True, "account": rows[0]})
    except Exception as e:
        err = _extract_supabase_error(e)
        print(f"[web_auth] /me account lookup failed: {err}")
        if ENV == "dev" or WEB_AUTH_DEBUG:
            return jsonify({"ok": False, "error": "account_lookup_failed", "supabase": err}), 500
        return jsonify({"ok": False, "error": "account_lookup_failed"}), 500
