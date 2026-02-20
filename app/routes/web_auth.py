# app/routes/web_auth.py
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, g

from app.core.supabase_client import supabase
from app.core.auth import require_auth_plus
from app.core.config import WEB_AUTH_ENABLED, WEB_TOKEN_TABLE, WEB_TOKEN_PEPPER

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

WEB_OTP_TABLE = _env("WEB_OTP_TABLE", "web_otps")
WEB_OTP_PEPPER = _env("WEB_OTP_PEPPER", WEB_TOKEN_PEPPER)
WEB_SESSION_TTL_DAYS = int(_env("WEB_SESSION_TTL_DAYS", "30") or "30")
WEB_OTP_TTL_MINUTES = int(_env("WEB_OTP_TTL_MINUTES", "10") or "10")

# Only return dev_otp when ENV == dev
WEB_DEV_RETURN_OTP = ENV == "dev"


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
    if v.startswith("0"):
        return "+234" + v[1:]
    if v.startswith("234"):
        return "+" + v
    if not v.startswith("+") and v.isdigit():
        return "+" + v
    return v


# -------------------------------------------------
# Account Creation (MATCHES YOUR TABLE)
# accounts:
#   id (uuid)
#   provider (text)
#   provider_user_id (text)
#   phone_e164 (text)
#   display_name (text, nullable)
# -------------------------------------------------
def _upsert_account_for_contact(contact: str) -> Optional[str]:
    try:
        # 1) Find existing by provider + provider_user_id
        res = (
            _sb()
            .table("accounts")
            .select("id")
            .eq("provider", "web")
            .eq("provider_user_id", contact.replace("+", ""))  # your screenshot stores without '+'
            .limit(1)
            .execute()
        )
        rows = (res.data or []) if hasattr(res, "data") else []
        if rows:
            return str(rows[0]["id"])

        # 2) Insert new (do NOT provide id, let DB default uuid generate)
        insert_res = (
            _sb()
            .table("accounts")
            .insert(
                {
                    "provider": "web",
                    # store provider_user_id like your screenshot (digits only)
                    "provider_user_id": contact.replace("+", ""),
                    # store phone_e164 like your screenshot (digits only, no '+')
                    "phone_e164": contact.replace("+", ""),
                    "display_name": None,
                }
            )
            .execute()
        )
        inserted = (insert_res.data or []) if hasattr(insert_res, "data") else []
        if inserted:
            return str(inserted[0]["id"])

        return None
    except Exception as e:
        print("[web_auth] account upsert/insert failed:", str(e))
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

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400

    # DEV returns constant OTP to make testing easy
    otp = "123456" if ENV == "dev" else f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(minutes=WEB_OTP_TTL_MINUTES)

    try:
        _sb().table(WEB_OTP_TABLE).insert(
            {
                "contact": contact,
                "purpose": purpose,
                "code_hash": _otp_hash(contact, purpose, otp),
                "expires_at": expires_at.isoformat(),
                "used": False,
            }
        ).execute()
    except Exception as e:
        print("[web_auth] otp store error:", str(e))
        return jsonify({"ok": False, "error": "otp_store_failed"}), 500

    resp: Dict[str, Any] = {"ok": True}
    if WEB_DEV_RETURN_OTP:
        resp["dev_otp"] = otp

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

    rows = (q.data or []) if hasattr(q, "data") else []
    if not rows:
        return jsonify({"ok": False, "error": "invalid_otp"}), 401

    row = rows[0]
    exp = row.get("expires_at")
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00")) if isinstance(exp, str) else None
    except Exception:
        exp_dt = None

    if exp_dt and _now_utc() > exp_dt:
        return jsonify({"ok": False, "error": "otp_expired"}), 401

    # mark used
    _sb().table(WEB_OTP_TABLE).update({"used": True}).eq("id", row["id"]).execute()

    # create/find account
    account_id = _upsert_account_for_contact(contact)
    if not account_id:
        return jsonify(
            {
                "ok": False,
                "error": "account_create_failed",
                "hint": "accounts insert/upsert failed; ensure accounts has columns: id(uuid), provider, provider_user_id, phone_e164",
            }
        ), 500

    # create session token
    raw_token = secrets.token_hex(32)
    expires_at = _now_utc() + timedelta(days=WEB_SESSION_TTL_DAYS)

    _sb().table(WEB_TOKEN_TABLE).insert(
        {
            "token_hash": _token_hash(raw_token),
            "account_id": account_id,  # <-- this is accounts.id (uuid)
            "expires_at": expires_at.isoformat(),
            "revoked": False,
        }
    ).execute()

    return jsonify(
        {
            "ok": True,
            "token": raw_token,
            "account_id": account_id,
            "expires_at": expires_at.isoformat(),
            "mode": "dev" if ENV == "dev" else "real",
        }
    )


# -------------------------------------------------
# ME
# -------------------------------------------------
@bp.get("/me")
@bp.get("/web/auth/me")
@require_auth_plus
def me():
    account_id = g.account_id

    res = _sb().table("accounts").select("*").eq("id", account_id).limit(1).execute()
    rows = (res.data or []) if hasattr(res, "data") else []

    if not rows:
        return jsonify({"ok": False, "error": "not_found"}), 404

    return jsonify({"ok": True, "account": rows[0]})
