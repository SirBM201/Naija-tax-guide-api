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

bp = Blueprint("web_auth", __name__)


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

# IMPORTANT: only return dev_otp in dev
WEB_DEV_RETURN_OTP = ENV == "dev"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _otp_hash(contact: str, purpose: str, otp: str) -> str:
    return _sha256_hex(f"{WEB_OTP_PEPPER}:{contact}:{purpose}:{otp}")


def _token_hash(raw_token: str) -> str:
    return _sha256_hex(f"{WEB_TOKEN_PEPPER}:{raw_token}")


def _normalize_contact(v: str) -> str:
    v = (v or "").strip().replace(" ", "")
    if v.startswith("0"):
        return "+234" + v[1:]
    if v.startswith("234"):
        return "+" + v
    return v


def _e164_digits(v: str) -> str:
    # your DB screenshot shows phone_e164 stored WITHOUT "+"
    return (v or "").strip().replace("+", "")


def _upsert_account_for_contact(contact_e164: str) -> Optional[str]:
    """
    Your Supabase accounts table uses:
      - id (uuid) as PK
      - provider (text)
      - provider_user_id (text)
      - phone_e164 (text)
      - phone (text) possibly

    We will:
      - find by (provider='web', provider_user_id=<digits>)
      - else insert WITHOUT id (let DB default generate uuid)
      - return accounts.id
    """
    try:
        provider_user_id = _e164_digits(contact_e164)

        existing = (
            _sb()
            .table("accounts")
            .select("id")
            .eq("provider", "web")
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if rows:
            return rows[0]["id"]

        ins = (
            _sb()
            .table("accounts")
            .insert({
                "provider": "web",
                "provider_user_id": provider_user_id,
                "phone_e164": provider_user_id,
                "phone": provider_user_id,
                "display_name": None,
            })
            .execute()
        )
        inserted = ins.data or []
        if inserted:
            return inserted[0]["id"]

        return None
    except Exception as e:
        print("[web_auth] account upsert failed:", str(e))
        return None


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

    otp = "123456" if ENV == "dev" else f"{secrets.randbelow(1000000):06d}"
    expires_at = _now_utc() + timedelta(minutes=WEB_OTP_TTL_MINUTES)

    try:
        _sb().table(WEB_OTP_TABLE).insert({
            "contact": contact,
            "purpose": purpose,
            "code_hash": _otp_hash(contact, purpose, otp),
            "expires_at": expires_at.isoformat(),
            "used": False,
        }).execute()
    except Exception as e:
        print("[web_auth] otp store error:", str(e))
        return jsonify({"ok": False, "error": "otp_store_failed"}), 500

    resp = {"ok": True}
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
    purpose = (data.get("purpose") or "web_login").strip()
    otp = str(data.get("otp") or "").strip()

    if not contact or not otp:
        return jsonify({"ok": False, "error": "invalid_request"}), 400

    code_hash = _otp_hash(contact, purpose, otp)

    q = (
        _sb()
        .table(WEB_OTP_TABLE)
        .select("id, expires_at")
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
    exp = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if _now_utc() > exp.astimezone(timezone.utc):
        return jsonify({"ok": False, "error": "otp_expired"}), 401

    _sb().table(WEB_OTP_TABLE).update({"used": True}).eq("id", row["id"]).execute()

    account_id = _upsert_account_for_contact(contact)
    if not account_id:
        return jsonify({"ok": False, "error": "account_create_failed"}), 500

    raw_token = secrets.token_hex(32)
    expires_at = _now_utc() + timedelta(days=WEB_SESSION_TTL_DAYS)

    _sb().table(WEB_TOKEN_TABLE).insert({
        "token_hash": _token_hash(raw_token),
        "account_id": str(account_id),  # store uuid as string (your web_tokens.account_id is text)
        "expires_at": expires_at.isoformat(),
        "revoked": False,
    }).execute()

    return jsonify({
        "ok": True,
        "token": raw_token,
        "account_id": str(account_id),
        "expires_at": expires_at.isoformat(),
        "mode": "dev" if ENV == "dev" else "real",
    })


@bp.get("/me")
@bp.get("/web/auth/me")
@require_auth_plus
def me():
    account_id = g.account_id  # accounts.id (uuid as string)

    res = (
        _sb()
        .table("accounts")
        .select("*")
        .eq("id", account_id)
        .limit(1)
        .execute()
    )

    rows = res.data or []
    if not rows:
        return jsonify({"ok": False, "error": "account_not_found"}), 404

    return jsonify({"ok": True, "account": rows[0]})
