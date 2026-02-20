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


def _token_hash(raw_token: str) -> str:
    return _sha256_hex(f"{WEB_TOKEN_PEPPER}:{raw_token}")


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


# -------------------------------------------------
# Account Creation (web accounts)
# -------------------------------------------------

def _upsert_account_for_contact(contact: str) -> Optional[str]:
    try:
        # Check if exists
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

        # Insert (let DB generate UUID)
        insert_res = (
            _sb()
            .table("accounts")
            .insert({
                "provider": "web",
                "provider_user_id": contact,
                "display_name": contact,
                # Keep phone column for phones; for email this will just store email string
                "phone": contact,
            })
            .execute()
        )
        inserted = insert_res.data or []
        if inserted:
            return inserted[0].get("account_id")
        return None

    except Exception as e:
        print("Account creation error:", str(e))
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

    # contact can be phone or email
    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip()

    # Optional explicit email destination (useful if contact is phone but you want email delivery)
    email_to = (str(data.get("email") or "") or "").strip().lower()

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400

    # OTP generation
    otp = f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(minutes=WEB_OTP_TTL_MINUTES)

    # Store OTP hash in DB
    try:
        _sb().table(WEB_OTP_TABLE).insert({
            "contact": contact,
            "purpose": purpose,
            "code_hash": _otp_hash(contact, purpose, otp),
            "expires_at": expires_at.isoformat(),
            "used": False,
        }).execute()
    except Exception as e:
        print("OTP store error:", str(e))
        return jsonify({"ok": False, "error": "otp_store_failed"}), 500

    # Decide where to send OTP (email sandbox/trial)
    # - If contact itself is an email => email that address
    # - Else if request provides email => email that address
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

    resp: Dict[str, Any] = {"ok": True}

    # Helpful status flags (safe in prod)
    resp["email_sent"] = bool(sent_email)
    if dest_email:
        resp["email_to"] = dest_email
    if dest_email and email_err:
        resp["email_error"] = email_err

    # DEV ONLY: return otp directly
    if WEB_DEV_RETURN_OTP:
        resp["dev_otp"] = otp
        resp["smtp_configured"] = smtp_is_configured()

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
    _sb().table(WEB_OTP_TABLE).update({"used": True}).eq("id", row["id"]).execute()

    account_id = _upsert_account_for_contact(contact)
    if not account_id:
        return jsonify({"ok": False, "error": "account_create_failed"}), 500

    raw_token = secrets.token_hex(32)
    expires_at = _now_utc() + timedelta(days=WEB_SESSION_TTL_DAYS)

    _sb().table(WEB_TOKEN_TABLE).insert({
        "token_hash": _token_hash(raw_token),
        "account_id": account_id,
        "expires_at": expires_at.isoformat(),
        "revoked": False,
    }).execute()

    return jsonify({
        "ok": True,
        "token": raw_token,
        "account_id": account_id,
        "expires_at": expires_at.isoformat(),
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
