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

try:
    from app.services.promo_service import (
        bootstrap_account_promo_state,
        validate_promo_code,
    )
except Exception:  # pragma: no cover
    bootstrap_account_promo_state = None  # type: ignore
    validate_promo_code = None  # type: ignore

import logging

logger = logging.getLogger(__name__)

bp = Blueprint("web_auth", __name__)

WEB_AUTH_ROUTE_VERSION = "2026-05-30-batch35B2-logout-request-fix"


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


def _clean_code(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum() or ch in {"_", "-"})[:80]


def _extract_referral_code(body: Dict[str, Any]) -> str:
    return _clean_code(
        body.get("referral_code")
        or body.get("ref")
        or body.get("invite_code")
        or ""
    )


def _extract_promo_code(body: Dict[str, Any]) -> str:
    return _clean_code(
        body.get("promo_code")
        or body.get("promo")
        or body.get("partner_code")
        or ""
    )


def _extract_signup_code(body: Dict[str, Any]) -> str:
    return _clean_code(
        body.get("signup_code")
        or body.get("acquisition_code")
        or body.get("code")
        or ""
    )


def _classify_signup_source(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-practice Batch 35A rule:
    - One signup = one acquisition source.
    - Explicit promo and explicit referral cannot both be used.
    - Generic signup_code is classified as promo first; if not active promo, it is treated as referral.
    """
    explicit_referral = _extract_referral_code(body)
    explicit_promo = _extract_promo_code(body)
    generic_code = _extract_signup_code(body)

    if explicit_referral and explicit_promo:
        return {
            "ok": False,
            "error": "multiple_acquisition_sources_not_allowed",
            "message": "Use either a referral code or a promo code during signup, not both.",
        }

    if explicit_promo:
        return {"ok": True, "source_type": "promo", "code": explicit_promo, "explicit": True}

    if explicit_referral:
        return {"ok": True, "source_type": "referral", "code": explicit_referral, "explicit": True}

    if generic_code:
        if validate_promo_code is not None:
            try:
                promo_check = validate_promo_code(generic_code)  # type: ignore[misc]
                if bool((promo_check or {}).get("valid")):
                    return {
                        "ok": True,
                        "source_type": "promo",
                        "code": generic_code,
                        "explicit": False,
                        "classification": "matched_active_promo_code",
                    }
            except Exception:
                pass

        return {
            "ok": True,
            "source_type": "referral",
            "code": generic_code,
            "explicit": False,
            "classification": "fallback_referral_code",
        }

    return {"ok": True, "source_type": None, "code": "", "explicit": False}


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
                "web_auth_route_version": WEB_AUTH_ROUTE_VERSION,
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
        "web_auth_route_version": WEB_AUTH_ROUTE_VERSION,
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

    acquisition = _classify_signup_source(body)
    if not acquisition.get("ok"):
        return jsonify({**acquisition, "web_auth_route_version": WEB_AUTH_ROUTE_VERSION}), 400

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
    acquisition_bootstrap: Dict[str, Any] | None = None
    referral_bootstrap: Dict[str, Any] | None = None
    promo_bootstrap: Dict[str, Any] | None = None

    if account_id:
        source_type = str(acquisition.get("source_type") or "").strip().lower()
        source_code = str(acquisition.get("code") or "").strip().upper()

        try:
            if source_type == "promo" and source_code:
                if bootstrap_account_promo_state is None:
                    promo_bootstrap = {
                        "ok": False,
                        "captured": False,
                        "error": "promo_service_unavailable",
                    }
                else:
                    promo_bootstrap = bootstrap_account_promo_state(  # type: ignore[misc]
                        account_id=account_id,
                        promo_code=source_code,
                        source="web_auth_verify_otp_promo",
                    )
                acquisition_bootstrap = {
                    "ok": bool((promo_bootstrap or {}).get("ok")),
                    "source_type": "promo",
                    "code": source_code,
                    "promo": promo_bootstrap,
                }
            elif source_type == "referral" and source_code:
                referral_bootstrap = bootstrap_account_referral_state(
                    account_id=account_id,
                    referral_code=source_code,
                    source="web_auth_verify_otp",
                )
                acquisition_bootstrap = {
                    "ok": bool((referral_bootstrap or {}).get("ok")),
                    "source_type": "referral",
                    "code": source_code,
                    "referral": referral_bootstrap,
                }
            else:
                referral_bootstrap = bootstrap_account_referral_state(
                    account_id=account_id,
                    referral_code=None,
                    source="web_auth_verify_otp",
                )
                acquisition_bootstrap = {
                    "ok": bool((referral_bootstrap or {}).get("ok")),
                    "source_type": None,
                    "code": "",
                    "referral": referral_bootstrap,
                }
        except Exception as e:
            acquisition_bootstrap = {
                "ok": False,
                "error": "acquisition_bootstrap_failed",
                "source_type": acquisition.get("source_type"),
                "code": acquisition.get("code"),
                "root_cause": repr(e),
            }

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

    if acquisition_bootstrap is not None:
        r = {**r, "acquisition": acquisition_bootstrap}
        if referral_bootstrap is not None:
            r["referral"] = referral_bootstrap
        if promo_bootstrap is not None:
            r["promo"] = promo_bootstrap

    r['session_set'] = True
    r['session_user_id'] = session.get('user_id')
    r['web_auth_route_version'] = WEB_AUTH_ROUTE_VERSION

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
def web_auth_me():
    """Get current authenticated user info - for web auth routes"""
    account_id, _debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "account_id": account_id,
        "user_id": account_id,
        "email": session.get('user_email'),
        "session_active": True,
        "web_auth_route_version": WEB_AUTH_ROUTE_VERSION,
    }), 200


@bp.post("/web/auth/logout")
def logout():
    """Logout and clear session"""
    account_id = session.get('account_id')
    session.clear()

    r = logout_web_session(request)
    resp = make_response(jsonify({
        "ok": True,
        "message": "Logged out successfully",
        "account_id": account_id,
        "web_auth_route_version": WEB_AUTH_ROUTE_VERSION,
"logout_result": r,
    }))

    resp.delete_cookie(WEB_AUTH_COOKIE_NAME, path="/", domain=_cookie_domain())
    return resp


@bp.get("/web/auth/debug")
def auth_debug():
    """Debug endpoint to check session state"""
    return jsonify({
        "ok": True,
        "session_keys": list(session.keys()),
        "session_data": dict(session),
        "cookies": dict(request.cookies),
        "account_id_from_session": session.get('account_id'),
        "user_id_from_session": session.get('user_id'),
        "web_auth_route_version": WEB_AUTH_ROUTE_VERSION,
    })
