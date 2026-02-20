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

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------

def _sb():
    return supabase() if callable(supabase) else supabase


def _now():
    return datetime.now(timezone.utc)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _token_hash(token: str) -> str:
    return _sha(f"{WEB_TOKEN_PEPPER}:{token}")


def _otp_hash(contact: str, purpose: str, otp: str) -> str:
    return _sha(f"{contact}:{purpose}:{otp}")


def _normalize_contact(v: str) -> str:
    v = (v or "").strip()
    if v.startswith("+"):
        return v
    if v.startswith("0"):
        return "+234" + v[1:]
    return v


# ---------------------------------------------------
# Account Handling (USES id NOT account_id)
# ---------------------------------------------------

def _ensure_account(contact: str) -> str:

    # 1️⃣ Try find existing
    res = (
        _sb()
        .table("accounts")
        .select("id")
        .eq("provider", "web")
        .eq("provider_user_id", contact)
        .limit(1)
        .execute()
    )

    rows = res.data or []
    if rows:
        return rows[0]["id"]

    # 2️⃣ Create new account
    account_id = str(uuid.uuid4())

    ins = (
        _sb()
        .table("accounts")
        .insert({
            "id": account_id,
            "provider": "web",
            "provider_user_id": contact,
            "display_name": contact,
            "phone": contact,
        })
        .execute()
    )

    if not ins.data:
        raise Exception("Account insert failed")

    return account_id


# ---------------------------------------------------
# VERIFY OTP
# ---------------------------------------------------

@bp.post("/verify-otp")
@bp.post("/web/auth/verify-otp")
def verify_otp():

    data: Dict[str, Any] = request.get_json(silent=True) or {}
    contact = _normalize_contact(str(data.get("contact") or ""))
    purpose = (data.get("purpose") or "web_login").strip()
    otp = str(data.get("otp") or "").strip()

    if not contact or not otp:
        return jsonify({"ok": False, "error": "missing_data"}), 400

    code_hash = _otp_hash(contact, purpose, otp)

    q = (
        _sb()
        .table("web_otps")
        .select("id, expires_at, used")
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

    exp = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    if _now() > exp:
        return jsonify({"ok": False, "error": "otp_expired"}), 401

    # mark used
    _sb().table("web_otps").update({"used": True}).eq("id", row["id"]).execute()

    # ensure account
    account_id = _ensure_account(contact)

    # create session
    raw_token = secrets.token_hex(32)
    expires_at = _now() + timedelta(days=30)

    _sb().table(WEB_TOKEN_TABLE).insert({
        "token_hash": _token_hash(raw_token),
        "account_id": account_id,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "revoked": False,
        "last_seen_at": _now().isoformat().replace("+00:00", "Z"),
    }).execute()

    return jsonify({
        "ok": True,
        "token": raw_token,
        "account_id": account_id,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    })


# ---------------------------------------------------
# ME
# ---------------------------------------------------

@bp.get("/me")
@bp.get("/web/auth/me")
@require_auth_plus
def me():

    account_id = getattr(g, "account_id", None)

    res = (
        _sb()
        .table("accounts")
        .select("id, provider, provider_user_id, display_name, phone, created_at")
        .eq("id", account_id)
        .limit(1)
        .execute()
    )

    rows = res.data or []
    if not rows:
        return jsonify({"ok": False, "error": "account_not_found"}), 404

    return jsonify({"ok": True, "account": rows[0]})
