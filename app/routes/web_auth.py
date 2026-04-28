# app/routes/web_auth.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request, make_response, session

from app.core.config import WEB_AUTH_COOKIE_NAME
from app.services.account_referral_bootstrap_service import bootstrap_account_referral_state
from app.services.web_auth_service import (
    request_web_otp,
    verify_web_otp_and_issue_token,
    logout_web_session,
    get_account_id_from_request,
)
from app.services.mail_service import send_otp_email
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("web_auth", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _cookie_mode_enabled() -> bool:
    v = _env("COOKIE_AUTH_ENABLED", "")
    if v:
        return _truthy(v)
    return True


def _cookie_secure() -> bool:
    v = _env("WEB_AUTH_COOKIE_SECURE", "")
    if v:
        return _truthy(v)
    return _truthy(_env("COOKIE_SECURE", "1"))


def _cookie_samesite() -> str:
    v = _env("WEB_AUTH_COOKIE_SAMESITE", "")
    if v:
        return v
    return _env("COOKIE_SAMESITE", "Lax")


def _cookie_domain() -> Optional[str]:
    v = _env("WEB_AUTH_COOKIE_DOMAIN", "")
    if v:
        return v or None
    d = _env("COOKIE_DOMAIN", "")
    return d or None


def _cookie_max_age() -> int:
    v = _env("WEB_AUTH_COOKIE_MAX_AGE", "")
    if v:
        return int(v or "2592000")
    return int(_env("COOKIE_MAX_AGE", "2592000") or "2592000")


def _return_bearer_in_json() -> bool:
    return _truthy(_env("WEB_AUTH_RETURN_BEARER", "0"))


def _dev_return_plain_otp() -> bool:
    return _truthy(_env("WEB_OTP_RETURN_PLAIN", "0"))


def _extract_referral_code(body: Dict[str, Any]) -> str:
    return str(
        body.get("referral_code")
        or body.get("ref")
        or body.get("invite_code")
        or ""
    ).strip().upper()


@bp.post("/web/auth/request-otp")
def request_otp():
    body = request.get_json(silent=True) or {}

    contact = (body.get("contact") or body.get("email") or "").strip().lower()
    purpose = (body.get("purpose") or "web_login").strip().lower()
    device_id = (body.get("device_id") or "").strip()

    if not contact:
        return jsonify({"ok": False, "error": "contact_required"}), 400

    r = request_web_otp(
        contact=contact,
        purpose=purpose,
        device_id=device_id or None,
        ip=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )

    if not r.get("ok"):
        return jsonify(r), 400

    otp_plain = r.get("_otp_plain")
    delivery: Dict[str, Any] = {"mode": "email", "sent": False}

    if otp_plain:
        print("[web_auth.request_otp] about_to_send_email", flush=True)
        mail_res = send_otp_email(contact, otp_plain)
        print(f"[web_auth.request_otp] mail_result={mail_res}", flush=True)

        if mail_res.get("ok"):
            delivery["sent"] = True
            delivery["provider"] = "smtp"
        else:
            delivery["sent"] = False
            delivery["error"] = mail_res.get("error") or "email_send_failed"
            delivery["root_cause"] = mail_res.get("root_cause")
            delivery["debug"] = mail_res.get("debug")

            out = {
                "ok": False,
                "error": "otp_email_send_failed",
                "message": "OTP was generated but email delivery failed.",
                "contact": r.get("contact"),
                "purpose": r.get("purpose"),
                "expires_at": r.get("expires_at"),
                "delivery": delivery,
                "debug": r.get("debug", {}),
            }

            if _dev_return_plain_otp() and otp_plain:
                out["otp"] = otp_plain

            resp = make_response(jsonify(out), 502)
            resp.headers["Cache-Control"] = "no-store"
            return resp

    out = {
        "ok": True,
        "contact": r.get("contact"),
        "purpose": r.get("purpose"),
        "expires_at": r.get("expires_at"),
        "delivery": delivery,
        "debug": r.get("debug", {}),
    }

    if _dev_return_plain_otp() and otp_plain:
        out["otp"] = otp_plain

    resp = make_response(jsonify(out), 200)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/web/auth/verify-otp")
def verify_otp():
    body = request.get_json(silent=True) or {}

    contact = (body.get("contact") or body.get("email") or "").strip().lower()
    otp = (body.get("otp") or body.get("code") or "").strip()
    purpose = (body.get("purpose") or "web_login").strip().lower()
    referral_code = _extract_referral_code(body)

    if not contact or not otp:
        return jsonify({"ok": False, "error": "contact_and_otp_required"}), 400

    r = verify_web_otp_and_issue_token(
        contact=contact,
        otp=otp,
        purpose=purpose,
        ip=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )

    if not r.get("ok"):
        return jsonify(r), 400

    account_id = str(r.get("account_id") or "").strip()
    referral_bootstrap: Dict[str, Any] | None = None

    if account_id:
        try:
            referral_bootstrap = bootstrap_account_referral_state(
                account_id=account_id,
                referral_code=referral_code or None,
                source="web_auth_verify_otp",
            )
        except Exception as e:
            referral_bootstrap = {
                "ok": False,
                "error": "referral_bootstrap_failed",
                "root_cause": repr(e),
            }

        # ✅ CRITICAL: Set Flask session for the tax endpoint
        session['user_id'] = account_id
        session['user_email'] = contact
        session['account_id'] = account_id
        session.permanent = True
        session.modified = True
        
        print(f"[web_auth.verify_otp] Session set for user: {account_id}", flush=True)
        print(f"[web_auth.verify_otp] Session keys: {list(session.keys())}", flush=True)

    token = (r.get("token") or "").strip()

    if _cookie_mode_enabled() and not _return_bearer_in_json():
        r = {**r}
        r.pop("token", None)

    if referral_bootstrap is not None:
        r = {**r, "referral": referral_bootstrap}

    # Add session info to response for debugging
    r['session_set'] = True
    r['session_user_id'] = session.get('user_id')

    resp = make_response(jsonify(r), 200)
    resp.headers["Cache-Control"] = "no-store"

    if _cookie_mode_enabled() and token:
        secure = _cookie_secure()
        samesite = _cookie_samesite()

        if samesite.lower() == "none" and not secure:
            return jsonify(
                {
                    "ok": False,
                    "error": "cookie_config_invalid",
                    "message": "SameSite=None requires Secure cookies (WEB_AUTH_COOKIE_SECURE=1).",
                    "debug": {
                        "WEB_AUTH_COOKIE_SAMESITE": samesite,
                        "WEB_AUTH_COOKIE_SECURE": secure,
                    },
                }
            ), 500

        max_age = _cookie_max_age()
        domain = _cookie_domain()

        resp.set_cookie(
            WEB_AUTH_COOKIE_NAME,
            token,
            max_age=max_age,
            httponly=True,
            secure=secure,
            samesite=samesite,
            path="/",
            domain=domain,
        )

    return resp


@bp.get("/web/auth/me")
def me():
    """Get current authenticated user info - returns format expected by frontend"""
    try:
        # First check Flask session
        session_user_id = session.get('user_id')
        session_email = session.get('user_email')
        session_account_id = session.get('account_id')
        
        if session_user_id:
            logger.info(f"me: Found authenticated user in session: {session_user_id}")
            resp_data = {
                "ok": True,
                "authenticated": True,
                "account_id": session_account_id or session_user_id,
                "user": {
                    "id": session_user_id,
                    "email": session_email,
                    "account_id": session_account_id or session_user_id
                }
            }
            resp = make_response(jsonify(resp_data), 200)
            resp.headers["Cache-Control"] = "no-store"
            return resp
        
        # Fallback to cookie/token method
        account_id, debug = get_account_id_from_request(request)
        if account_id:
            logger.info(f"me: Found authenticated user via token: {account_id}")
            resp_data = {
                "ok": True,
                "authenticated": True,
                "account_id": account_id,
                "user": {
                    "id": account_id,
                    "account_id": account_id
                }
            }
            resp = make_response(jsonify(resp_data), 200)
            resp.headers["Cache-Control"] = "no-store"
            return resp
        
        # Not authenticated
        logger.warning(f"me: No authenticated user found. Debug: {debug}")
        resp = make_response(jsonify({
            "ok": False,
            "authenticated": False,
            "error": "unauthorized"
        }), 401)
        resp.headers["Cache-Control"] = "no-store"
        return resp
        
    except Exception as e:
        logger.error(f"me: Error in /me endpoint: {e}")
        resp = make_response(jsonify({
            "ok": False,
            "authenticated": False,
            "error": str(e)
        }), 500)
        resp.headers["Cache-Control"] = "no-store"
        return resp


@bp.post("/web/auth/logout")
def logout():
    # Clear Flask session
    session.clear()
    logger.info("logout: Session cleared")
    
    r = logout_web_session(request)

    resp = make_response(jsonify(r), 200)
    resp.headers["Cache-Control"] = "no-store"

    domain = _cookie_domain()
    resp.delete_cookie(WEB_AUTH_COOKIE_NAME, path="/", domain=domain)

    return resp
