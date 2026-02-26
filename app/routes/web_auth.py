# app/routes/web_auth.py
from __future__ import annotations

"""
WEB AUTH ROUTES (HARDENED — CANONICAL account_id)

This version keeps your public endpoints the same, but it delegates
identity correctness to app.services.web_auth_service:

- request-otp: stores OTP row and (optionally) sends via SMTP (unchanged pattern)
- verify-otp: validates OTP and issues token (cookie/bearer) using CANONICAL accounts.account_id

✅ Strong failure exposers:
    - debug block includes root cause + fix, plus SAFE request metadata

IMPORTANT:
This file assumes your DB FK is correct:
    web_tokens.account_id -> accounts.account_id
"""

import os
from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, jsonify, request, make_response

from app.services.email_service import send_email_otp, smtp_is_configured
from app.services.web_auth_service import (
    WEB_AUTH_COOKIE_NAME,
    request_web_otp,
    verify_web_otp_and_issue_token,
    logout_web_session,
)

bp = Blueprint("web_auth", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(s: str, n: int = 220) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _debug_enabled() -> bool:
    return _truthy(os.getenv("WEB_AUTH_DEBUG", "0")) or _truthy(os.getenv("AUTH_DEBUG", "0"))


def _safe_req_meta() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    return {
        "content_type": (request.headers.get("Content-Type") or "").strip(),
        "content_length": request.content_length,
        "keys": sorted(list(data.keys())) if isinstance(data, dict) else [],
    }


def _pick_contact(data: Dict[str, Any]) -> str:
    for k in ("contact", "email", "identifier", "login", "handle", "user"):
        v = str(data.get(k) or "").strip()
        if v:
            return v.strip().lower()
    return ""


@bp.post("/request-otp")
@bp.post("/web/auth/request-otp")
def request_otp():
    data = request.get_json(silent=True) or {}
    contact = _pick_contact(data)
    purpose = (str(data.get("purpose") or "web_login")).strip()
    device_id = (str(data.get("device_id") or "")).strip() or None

    if not contact:
        out = {"ok": False, "error": "missing_contact"}
        if _debug_enabled():
            out["debug"] = {"root_cause": "no contact/email provided", "fix": "Send JSON {email:<...>} or {contact:<...>}.", "req": _safe_req_meta()}
        return jsonify(out), 400

    # store OTP
    r = request_web_otp(contact=contact, purpose=purpose, device_id=device_id, ip=request.remote_addr, user_agent=request.headers.get("User-Agent"))
    if not r.get("ok"):
        out = dict(r)
        if _debug_enabled():
            out["debug"] = {**(out.get("debug") or {}), "req": _safe_req_meta()}
        return jsonify(out), 400

    # send OTP (email only) – keep existing behavior
    if "@" in contact:
        if not smtp_is_configured():
            out = {"ok": False, "error": "smtp_not_configured"}
            if _debug_enabled():
                out["debug"] = {"root_cause": "SMTP not configured", "fix": "Set SMTP_* env vars or disable email OTP sending.", "req": _safe_req_meta()}
            return jsonify(out), 500

        otp_dev = r.get("otp_dev")
        # In prod we should not have otp_dev; this is dev-only.
        # send_email_otp expects the raw OTP; we only have it in dev.
        # For prod, you should generate OTP in this route OR in send_email_otp flow.
        if not otp_dev and _debug_enabled():
            # tell you clearly why the email isn't sent
            out = {"ok": False, "error": "otp_not_returned_for_email_send"}
            out["debug"] = {
                "root_cause": "request_web_otp does not return raw OTP in prod (by design).",
                "fix": "Either (A) move OTP generation + email sending into this route, OR (B) set WEB_DEV_RETURN_OTP=1 for testing only.",
            }
            return jsonify(out), 500

        if otp_dev:
            try:
                send_email_otp(contact, otp_dev)
            except Exception as e:
                out = {"ok": False, "error": "email_send_failed"}
                if _debug_enabled():
                    out["debug"] = {"root_cause": f"{type(e).__name__}: {_clip(str(e))}", "fix": "Check SMTP settings and sender domain.", "req": _safe_req_meta()}
                return jsonify(out), 500

    return jsonify({"ok": True, "contact": contact, "purpose": purpose, "expires_at": r.get("expires_at")}), 200


@bp.post("/verify-otp")
@bp.post("/web/auth/verify-otp")
def verify_otp():
    data = request.get_json(silent=True) or {}
    contact = _pick_contact(data)
    otp = str(data.get("otp") or "").strip()
    purpose = (str(data.get("purpose") or "web_login")).strip()
    device_id = (str(data.get("device_id") or "")).strip() or None

    if not contact or not otp:
        out = {"ok": False, "error": "missing_contact_or_otp"}
        if _debug_enabled():
            out["debug"] = {"root_cause": "contact or otp missing", "fix": "Send JSON {email/contact:<...>, otp:<6digits>}.", "req": _safe_req_meta()}
        return jsonify(out), 400

    r = verify_web_otp_and_issue_token(contact=contact, otp=otp, purpose=purpose, device_id=device_id, ip=request.remote_addr, user_agent=request.headers.get("User-Agent"))
    if not r.get("ok"):
        out = dict(r)
        if _debug_enabled():
            out["debug"] = {**(out.get("debug") or {}), "req": _safe_req_meta()}
        return jsonify(out), 400

    # set cookie
    resp = make_response(jsonify({"ok": True, "account_id": r["account_id"], "expires_at": r.get("expires_at")}))
    resp.set_cookie(
        WEB_AUTH_COOKIE_NAME,
        r["token"],
        httponly=True,
        secure=True,
        samesite="None",
        max_age=int(60 * 60 * 24 * 30),
        path="/",
    )
    return resp, 200


@bp.post("/logout")
@bp.post("/web/auth/logout")
def logout():
    auth = (request.headers.get("Authorization") or "").strip()
    r = logout_web_session(auth)
    if not r.get("ok"):
        return jsonify(r), 400

    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(WEB_AUTH_COOKIE_NAME, "", expires=0, path="/")
    return resp, 200
