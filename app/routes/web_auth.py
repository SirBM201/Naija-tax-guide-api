# app/routes/web_auth.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..core.config import ENV
from ..services.web_otp_service import request_web_login_otp, verify_web_login_otp
from ..services.web_sessions_service import validate_web_session, touch_session_best_effort

bp = Blueprint("web_auth", __name__)


def _is_prod() -> bool:
    return (ENV or "").strip().lower() == "prod"


@bp.post("/web/auth/request-otp")
def web_request_otp():
    """
    Request OTP for web login.

    Expected JSON:
      {
        "contact": "+2348012345678" | "2348012345678" | "...",
        "purpose": "web_login" (optional)
      }

    Response:
      PROD:
        { "ok": true, "contact": "...", "purpose": "web_login" }
      DEV/NON-PROD:
        { "ok": true, "contact": "...", "purpose": "web_login", "dev_otp": "123456" }
    """
    data = request.get_json(silent=True) or {}
    contact = (data.get("contact") or "").strip()
    purpose = (data.get("purpose") or "web_login").strip() or "web_login"

    if not contact:
        return jsonify({"ok": False, "error": "missing_contact"}), 400

    # This generates + stores OTP (hash) in DB; in DEV it may return plain OTP for local testing
    result = request_web_login_otp(contact=contact, purpose=purpose) or {}

    # IMPORTANT: Only return dev_otp in non-prod
    if not _is_prod():
        return jsonify(
            {
                "ok": True,
                "contact": contact,
                "purpose": purpose,
                "dev_otp": result.get("dev_otp"),
            }
        )

    return jsonify({"ok": True, "contact": contact, "purpose": purpose})


@bp.post("/web/auth/verify-otp")
def web_verify_otp():
    """
    Verify OTP for web login.

    Expected JSON (accepts BOTH otp and code for compatibility):
      {
        "contact": "+2348012345678",
        "otp": "123456"   (OR)
        "code": "123456",
        "purpose": "web_login" (optional)
      }

    Response:
      { "ok": true, "token": "<web_session_token>", ... }  on success
      { "ok": false, "error": "..." }                     on failure
    """
    data = request.get_json(silent=True) or {}
    contact = (data.get("contact") or "").strip()
    # Frontend currently sends "code", older backend used "otp"
    otp = (data.get("otp") or data.get("code") or "").strip()
    purpose = (data.get("purpose") or "web_login").strip() or "web_login"

    if not contact or not otp:
        return jsonify({"ok": False, "error": "missing_contact_or_otp"}), 400

    # returns token if ok
    res = verify_web_login_otp(contact=contact, otp=otp, purpose=purpose) or {}
    if not res.get("ok"):
        # keep 401 so frontend can treat as auth failure
        return jsonify(res), 401

    return jsonify(res)


@bp.get("/web/auth/me")
def web_me():
    """
    Validate current web session token.

    Header:
      Authorization: Bearer <token>

    Response:
      { "ok": true, "account_id": "<uuid>" } if valid
    """
    auth = (request.headers.get("Authorization") or "").strip()
    token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else None
    if not token:
        return jsonify({"ok": False, "error": "missing_token"}), 401

    ok, account_id, reason = validate_web_session(token)
    if not ok or not account_id:
        return jsonify({"ok": False, "error": reason or "invalid_token"}), 401

    touch_session_best_effort(token)

    return jsonify({"ok": True, "account_id": account_id})
