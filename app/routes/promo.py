# app/routes/promo.py
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, redirect, request

from app.core.supabase_client import supabase
from app.services.web_auth_service import get_account_id_from_request
from app.services.promo_service import (
    PROMO_SERVICE_VERSION,
    build_promo_links,
    calculate_promo_checkout_preview,
    qualify_promo_after_successful_payment,
    record_promo_checkout_started,
    track_promo_event,
    validate_promo_code,
)

bp = Blueprint("promo", __name__)
logger = logging.getLogger(__name__)

PROMO_ROUTE_VERSION = "2026-05-30-batch35C-promo-admin-visibility-owner"


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        raw = str(value).replace(",", "").strip()
        if not raw:
            return default
        return int(Decimal(raw))
    except Exception:
        return default


def _to_decimal_string(value: Any, default: str = "0") -> str:
    try:
        if value is None or str(value).strip() == "":
            return default
        return str(Decimal(str(value).replace(",", "").strip()))
    except Exception:
        return default


def _response_data(resp: Any):
    data = getattr(resp, "data", None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _first(resp: Any) -> Optional[Dict[str, Any]]:
    rows = _response_data(resp)
    return rows[0] if rows else None


def _normalize_code(value: Any) -> str:
    code = _upper(value)
    return "".join(ch for ch in code if ch.isalnum() or ch in {"_", "-"})[:80]


def _get_expected_admin_key() -> str:
    return (
        os.getenv("ADMIN_API_KEY")
        or os.getenv("INTERNAL_ADMIN_API_KEY")
        or os.getenv("PROMO_ADMIN_API_KEY")
        or os.getenv("REFERRAL_ADMIN_API_KEY")
        or ""
    ).strip()


def _get_supplied_admin_key() -> str:
    header_key = (request.headers.get("X-Admin-Key") or "").strip()
    if header_key:
        return header_key

    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    query_key = (request.args.get("admin_key") or "").strip()
    if query_key:
        return query_key

    body = request.get_json(silent=True) or {}
    return str(body.get("admin_key") or "").strip()


def _require_admin():
    expected = _get_expected_admin_key()
    supplied = _get_supplied_admin_key()

    if not expected:
        return jsonify({
            "ok": False,
            "error": "admin_api_key_not_configured",
            "message": "Set ADMIN_API_KEY on Koyeb before using promo admin endpoints.",
            "route_version": PROMO_ROUTE_VERSION,
        }), 500

    if not supplied or supplied != expected:
        return jsonify({
            "ok": False,
            "error": "invalid_or_missing_admin_key",
            "message": "Provide X-Admin-Key header or admin_key query/body value.",
            "route_version": PROMO_ROUTE_VERSION,
        }), 403

    return None


def _get_code_row(code: str) -> Optional[Dict[str, Any]]:
    code = _normalize_code(code)
    if not code:
        return None
    try:
        resp = _sb().table("promo_codes").select("*").eq("code", code).limit(1).execute()
        return _first(resp)
    except Exception:
        return None


def _update_code_by_id(row_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = _sb().table("promo_codes").update(payload).eq("id", row_id).execute()
    return _first(resp) or payload


def _insert_code(payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = _sb().table("promo_codes").insert(payload).execute()
    return _first(resp) or payload


# ---------------------------------------------------------------------
# Batch 35B/35C billing interceptor: keeps promo discount logic active
# without replacing the large existing app/routes/billing.py file.
# ---------------------------------------------------------------------

def _billing():
    from app.routes import billing as billing_route
    return billing_route


def _qualify_promo_after_billing_application(reference: str, paystack_data: Dict[str, Any], application: Dict[str, Any]) -> Dict[str, Any]:
    if not application or not application.get("applied"):
        return {"ok": True, "qualified": False, "reason": "payment_not_applied"}

    account_id = _clean(application.get("account_id"))
    plan_code = _clean(application.get("plan_code"))
    metadata = paystack_data.get("metadata") if isinstance(paystack_data.get("metadata"), dict) else {}

    if not account_id:
        return {"ok": True, "qualified": False, "reason": "application_missing_account_id"}

    try:
        result = qualify_promo_after_successful_payment(
            paying_account_id=account_id,
            payment_reference=reference,
            plan_code=plan_code,
            metadata={
                **metadata,
                "paid_at": paystack_data.get("paid_at") or paystack_data.get("created_at"),
                "amount_kobo": paystack_data.get("amount") or metadata.get("amount_kobo"),
                "gateway_response": paystack_data.get("gateway_response"),
                "promo_route_version": PROMO_ROUTE_VERSION,
            },
        )
        return result if isinstance(result, dict) else {"ok": True, "qualified": False, "raw": result}
    except Exception as exc:
        logger.exception("Promo qualification after billing application failed")
        return {"ok": False, "qualified": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _intercept_billing_checkout():
    billing_route = _billing()

    account_id, account, debug = billing_route._resolve_current_account()
    if not account_id:
        return billing_route._json_error(
            "Please sign in again before choosing a plan.",
            401,
            error="unauthorized",
            fix="Login again from the website so the ntg_session cookie can be refreshed.",
            debug=debug,
        )

    data = billing_route._safe_json()
    plan_code = billing_route._lower(data.get("plan_code") or data.get("plan"))
    if not plan_code:
        return billing_route._json_error("plan_code is required.", 400, error="plan_code_required")

    plan = billing_route.get_plan(plan_code)
    if not plan:
        return billing_route._json_error("Selected plan was not found.", 404, error="plan_not_found", plan_code=plan_code)

    current = billing_route._get_subscription(account_id)
    if current and billing_route._subscription_is_active(current):
        current_plan_code = billing_route._lower(current.get("plan_code"))
        if current_plan_code == plan_code:
            return billing_route._json_error(
                f"You already have an active {billing_route._plan_name(plan_code)} subscription.",
                409,
                error="already_on_selected_plan",
                current_plan_code=current_plan_code,
            )

    original_amount_kobo = int(plan.get("price") or 0) * 100
    if original_amount_kobo <= 0:
        return billing_route._json_error(
            "This plan cannot be checked out because the price is invalid.",
            400,
            error="invalid_plan_price",
            plan=plan,
        )

    promo_preview = calculate_promo_checkout_preview(
        account_id=account_id,
        plan_code=plan_code,
        original_amount_kobo=original_amount_kobo,
    )

    amount_kobo = int(promo_preview.get("final_amount_kobo") or original_amount_kobo)
    if amount_kobo <= 0:
        return billing_route._json_error(
            "Promo discount produced an invalid checkout amount.",
            400,
            error="invalid_discounted_plan_price",
            promo=promo_preview,
            plan=plan,
        )

    promo_redemption = promo_preview.get("redemption") if isinstance(promo_preview.get("redemption"), dict) else {}
    promo_code = _upper((promo_redemption or {}).get("promo_code") or promo_preview.get("promo_code"))

    email = billing_route._clean(data.get("email") or (account or {}).get("email")) or f"user_{account_id[:8]}@naijataxguides.com"
    reference = billing_route.create_reference("NTG")

    metadata = {
        "account_id": account_id,
        "plan_code": plan_code,
        "plan_name": plan.get("name"),
        "plan_family": billing_route._plan_family_from_code(plan_code),
        "type": "subscription",
        "source": data.get("source") or "web_plans_page",
        "channel_type": billing_route._lower(data.get("channel_type")) or "web",
        "provider_user_id": billing_route._clean(data.get("provider_user_id")) or None,
        "amount_ngn": int(amount_kobo / 100),
        "amount_kobo": amount_kobo,
        "original_amount_ngn": int(original_amount_kobo / 100),
        "original_amount_kobo": original_amount_kobo,
        "discount_amount_ngn": int(int(promo_preview.get("discount_amount_kobo") or 0) / 100),
        "discount_amount_kobo": int(promo_preview.get("discount_amount_kobo") or 0),
        "final_amount_ngn": int(amount_kobo / 100),
        "final_amount_kobo": amount_kobo,
        "promo_applied": bool(promo_preview.get("applies")),
        "promo_code": promo_code or None,
        "promo_redemption_id": (promo_redemption or {}).get("id") if promo_redemption else None,
        "promo_benefit_type": (promo_redemption or {}).get("benefit_type") if promo_redemption else None,
        "promo_discount_percent": (promo_redemption or {}).get("discount_percent") if promo_redemption else None,
        "promo_route_version": PROMO_ROUTE_VERSION,
        "currency": plan.get("currency") or "NGN",
    }

    callback_url = f"{billing_route._public_backend_base_url()}/api/billing/callback?reference={reference}&plan={plan_code}"

    try:
        result = billing_route.initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
    except Exception as exc:
        logger.exception("Paystack promo checkout initialization failed")
        return billing_route._json_error(
            "Paystack checkout could not be started.",
            502,
            error="paystack_initialize_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Confirm PAYSTACK_SECRET_KEY is set on Koyeb and the selected plan price is valid.",
            debug=debug,
        )

    tx_note = billing_route._remember_transaction(reference, account_id, plan_code, amount_kobo, "pending", metadata, event_type="subscription")
    auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
    access_code = ((result or {}).get("data") or {}).get("access_code") or (result or {}).get("access_code")

    promo_checkout_note = record_promo_checkout_started(
        account_id=account_id,
        payment_reference=reference,
        plan_code=plan_code,
        original_amount_kobo=original_amount_kobo,
        discount_amount_kobo=int(promo_preview.get("discount_amount_kobo") or 0),
        final_amount_kobo=amount_kobo,
        metadata=metadata,
    )

    if not auth_url:
        return billing_route._json_error(
            "Paystack did not return an authorization URL.",
            502,
            error="paystack_authorization_url_missing",
            paystack_response=result,
            transaction_note=tx_note,
            promo_checkout_note=promo_checkout_note,
        )

    return billing_route.jsonify(
        {
            "ok": True,
            "billing_route_version": "2026-05-30-v35C-promo-admin-visibility-owner",
            "promo_route_version": PROMO_ROUTE_VERSION,
            "action": "checkout_started",
            "authorization_url": auth_url,
            "access_code": access_code,
            "reference": reference,
            "plan": plan,
            "pricing": {
                "original_amount_kobo": original_amount_kobo,
                "discount_amount_kobo": int(promo_preview.get("discount_amount_kobo") or 0),
                "final_amount_kobo": amount_kobo,
                "currency": plan.get("currency") or "NGN",
            },
            "promo": {
                "applied": bool(promo_preview.get("applies")),
                "code": promo_code or None,
                "preview": promo_preview,
                "checkout_note": promo_checkout_note,
            },
            "transaction_note": tx_note,
        }
    ), 200


def _intercept_billing_verify():
    billing_route = _billing()
    reference = billing_route._clean(request.args.get("reference") or request.args.get("trxref"))
    debug_requested = billing_route._lower(request.args.get("debug")) in {"1", "true", "yes"}

    if not reference:
        return billing_route._json_error("Payment reference is required.", 400, error="reference_required")

    try:
        verification = billing_route.verify_transaction(reference)
    except Exception as exc:
        logger.exception("Paystack verification failed")
        return billing_route._json_error(
            "Paystack payment verification failed.",
            502,
            error="paystack_verify_failed",
            reference=reference,
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
        )

    data = (verification or {}).get("data") or {}
    status = billing_route._lower(data.get("status"))
    paid = status == "success"

    applied = False
    application: Dict[str, Any] = {}
    promo_application: Dict[str, Any] = {}

    if paid:
        application = billing_route._apply_successful_payment(reference, data)
        applied = bool(application.get("applied"))
        promo_application = _qualify_promo_after_billing_application(reference, data, application)

    payload: Dict[str, Any] = {
        "ok": True,
        "billing_route_version": "2026-05-30-v35C-promo-admin-visibility-owner",
        "promo_route_version": PROMO_ROUTE_VERSION,
        "reference": reference,
        "status": status or "unknown",
        "paid": paid,
        "applied": applied,
        "application": application if application else None,
        "promo_application": promo_application if promo_application else None,
        "account_id": application.get("account_id") if application else None,
        "plan_code": application.get("plan_code") if application else None,
        "payment_type": application.get("payment_type") if application else None,
        "activation_state": "applied" if applied else ("not_paid" if not paid else "not_applied"),
        "message": "Payment verified and applied." if applied else "Payment verified, but it was not applied.",
    }

    if debug_requested:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        payload["debug"] = {
            "paystack_status": status,
            "metadata_keys": sorted(list(metadata.keys())),
            "application": application,
            "promo_application": promo_application,
            "raw_verification_data_keys": sorted(list(data.keys())) if isinstance(data, dict) else [],
        }

    return billing_route.jsonify(payload), 200


def _intercept_billing_callback():
    billing_route = _billing()
    reference = billing_route._clean(request.args.get("reference") or request.args.get("trxref"))
    plan_hint = billing_route._clean(request.args.get("plan"))

    if not reference:
        return redirect(f"{billing_route._front_base_url()}/billing?payment=missing_reference", code=302)

    try:
        verification = billing_route.verify_transaction(reference)
        data = (verification or {}).get("data") or {}

        if billing_route._lower(data.get("status")) == "success":
            application = billing_route._apply_successful_payment(reference, data)
            promo_application = _qualify_promo_after_billing_application(reference, data, application)

            if application.get("applied"):
                plan_code = billing_route._clean(application.get("plan_code") or plan_hint)
                promo_status = "promo_qualified" if promo_application.get("qualified") or promo_application.get("already_qualified") else "promo_not_qualified"
                return redirect(
                    f"{billing_route._front_base_url()}/billing/success?reference={reference}&plan={plan_code}&promo={promo_status}",
                    code=302,
                )

            return redirect(f"{billing_route._front_base_url()}/billing?payment=not_applied&reference={reference}", code=302)

    except Exception as exc:
        logger.exception("Billing callback verification failed")
        return redirect(f"{billing_route._front_base_url()}/billing?payment=verify_failed&error={type(exc).__name__}", code=302)

    return redirect(f"{billing_route._front_base_url()}/billing?payment=pending&reference={reference}", code=302)


def _intercept_billing_webhook():
    billing_route = _billing()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return billing_route._json_error("No webhook payload received.", 400, error="empty_webhook_payload")

    raw = request.get_data() or b""
    signature = request.headers.get("X-Paystack-Signature") or request.headers.get("x-paystack-signature") or ""

    if signature:
        try:
            if not billing_route.verify_webhook_signature(raw, signature):
                return billing_route._json_error("Invalid Paystack webhook signature.", 401, error="invalid_webhook_signature")
        except Exception as exc:
            return billing_route._json_error(
                "Webhook signature check failed.",
                401,
                error="signature_check_failed",
                root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            )

    event = payload.get("event")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = billing_route._clean(data.get("reference"))
    status = billing_route._lower(data.get("status"))

    billing_route._store_paystack_event(
        event_id=billing_route._clean(data.get("id")) or reference,
        event_type=billing_route._clean(event),
        reference=reference,
        payload=payload,
    )

    application: Dict[str, Any] = {}
    promo_application: Dict[str, Any] = {}

    if event == "charge.success" and reference and status == "success":
        application = billing_route._apply_successful_payment(reference, data)
        promo_application = _qualify_promo_after_billing_application(reference, data, application)

    return billing_route.jsonify(
        {
            "ok": True,
            "billing_route_version": "2026-05-30-v35C-promo-admin-visibility-owner",
            "promo_route_version": PROMO_ROUTE_VERSION,
            "message": "Webhook received",
            "event": event,
            "reference": reference,
            "status": status,
            "application": application if application else None,
            "promo_application": promo_application if promo_application else None,
        }
    ), 200


@bp.before_app_request
def _promo_billing_interceptor():
    path = request.path.rstrip("/")
    method = request.method.upper()

    if method == "POST" and path in {
        "/api/change-plan",
        "/api/checkout",
        "/api/initialize",
        "/api/billing/change-plan",
        "/api/billing/checkout",
        "/api/billing/initialize",
    }:
        return _intercept_billing_checkout()

    if method == "GET" and path in {
        "/api/verify",
        "/api/billing/verify",
        "/api/paystack/verify",
    }:
        return _intercept_billing_verify()

    if method == "GET" and path in {
        "/api/callback",
        "/api/billing/callback",
        "/api/paystack/callback",
    }:
        return _intercept_billing_callback()

    if method == "POST" and path in {
        "/api/webhook",
        "/api/billing/webhook",
    }:
        return _intercept_billing_webhook()

    return None


@bp.get("/promo/health")
def promo_health():
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "service_version": PROMO_SERVICE_VERSION,
        "rule": "Promo code is captured at signup/onboarding, not at payment form.",
        "batch35c": {
            "admin_management": True,
            "owner_assignment": True,
            "active_user_promo_preview": True,
        },
        "expected_urls": [
            "/api/promo/health",
            "/api/promo/my-active",
            "/api/promo/admin/health",
            "/api/promo/admin/codes",
            "/api/promo/admin/redemptions",
            "/api/promo/admin/rewards",
        ],
    }), 200


@bp.get("/promo/my-active")
def promo_my_active():
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({
            "ok": False,
            "error": "unauthorized",
            "route_version": PROMO_ROUTE_VERSION,
            "auth_debug": auth_debug,
        }), 401

    plan_code = _lower(request.args.get("plan_code"))
    amount_kobo = _to_int(request.args.get("amount_kobo"), 0)

    try:
        resp = (
            _sb()
            .table("promo_redemptions")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        redemption = _first(resp)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "promo_redemption_lookup_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            "route_version": PROMO_ROUTE_VERSION,
        }), 500

    preview = None
    if redemption and amount_kobo > 0:
        preview = calculate_promo_checkout_preview(
            account_id=account_id,
            plan_code=plan_code or "unknown",
            original_amount_kobo=amount_kobo,
        )

    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "account_id": account_id,
        "has_promo": bool(redemption),
        "redemption": redemption,
        "preview": preview,
    }), 200


@bp.get("/promo/validate/<code>")
def promo_validate(code: str):
    result = validate_promo_code(code)
    links = build_promo_links(code)
    return jsonify({**result, "route_version": PROMO_ROUTE_VERSION, "links": links}), 200


@bp.get("/promo/hub/<code>")
def promo_hub(code: str):
    validation = validate_promo_code(code)
    links = build_promo_links(code)
    tracking = track_promo_event(
        promo_code=code,
        event_type="promo_hub_view",
        selected_platform=request.args.get("source") or request.args.get("platform"),
        landing_url=request.url,
        request_obj=request,
        metadata={"query": dict(request.args), "route": "/api/promo/hub/<code>"},
    )
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "promo_code": links["code"],
        "valid": bool(validation.get("valid")),
        "validation": validation,
        "links": links,
        "tracking": {"ok": bool(tracking.get("ok")), "non_blocking": True},
    }), 200


@bp.route("/promo/track", methods=["GET", "POST"])
def promo_track():
    if request.method == "POST":
        body: Dict[str, Any] = request.get_json(silent=True) or {}
        code = body.get("code") or body.get("promo_code")
        platform = body.get("platform") or body.get("selected_platform")
        event_type = body.get("event_type") or "promo_platform_select"
    else:
        body = {}
        code = request.args.get("code") or request.args.get("promo_code")
        platform = request.args.get("platform") or request.args.get("selected_platform")
        event_type = request.args.get("event_type") or "promo_platform_select"

    validation = validate_promo_code(code)
    links = build_promo_links(code)
    selected = _clean(platform).lower() or "website"
    destinations = {
        "web": links["website"],
        "website": links["website"],
        "signup": links["website"],
        "whatsapp": links["whatsapp"],
        "wa": links["whatsapp"],
        "telegram": links["telegram"],
        "tg": links["telegram"],
    }
    destination = destinations.get(selected, links["website"])
    tracking = track_promo_event(
        promo_code=code,
        event_type=event_type,
        selected_platform=selected,
        landing_url=destination,
        request_obj=request,
        metadata={"query": dict(request.args), "body": body, "route": "/api/promo/track"},
    )
    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "promo_code": links["code"],
        "valid": bool(validation.get("valid")),
        "selected_platform": selected,
        "destination": destination,
        "tracking": {"ok": bool(tracking.get("ok")), "non_blocking": True},
    }), 200


@bp.get("/promo/track-and-go/<code>/<platform>")
def promo_track_and_go(code: str, platform: str):
    links = build_promo_links(code)
    selected = _clean(platform).lower() or "website"
    destinations = {
        "web": links["website"],
        "website": links["website"],
        "signup": links["website"],
        "whatsapp": links["whatsapp"],
        "wa": links["whatsapp"],
        "telegram": links["telegram"],
        "tg": links["telegram"],
    }
    destination = destinations.get(selected, links["website"])
    track_promo_event(
        promo_code=code,
        event_type="promo_platform_click",
        selected_platform=selected,
        landing_url=destination,
        request_obj=request,
        metadata={"query": dict(request.args), "route": "/api/promo/track-and-go/<code>/<platform>"},
    )
    return redirect(destination, code=302)


@bp.get("/promo/admin/health")
def promo_admin_health():
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    return jsonify({
        "ok": True,
        "route_version": PROMO_ROUTE_VERSION,
        "message": "Promo admin endpoints are active.",
        "admin_key_configured": bool(_get_expected_admin_key()),
        "endpoints": [
            "GET /api/promo/admin/codes",
            "POST /api/promo/admin/codes",
            "POST /api/promo/admin/codes/<code>/assign-owner",
            "GET /api/promo/admin/redemptions",
            "GET /api/promo/admin/rewards",
            "POST /api/promo/admin/rewards/<reward_id>/mark-status",
        ],
    }), 200


@bp.get("/promo/admin/codes")
def promo_admin_codes():
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    limit = _to_int(request.args.get("limit"), 100)
    try:
        resp = _sb().table("promo_codes").select("*").order("created_at", desc=True).limit(max(1, min(limit, 500))).execute()
        rows = _response_data(resp)
        return jsonify({"ok": True, "route_version": PROMO_ROUTE_VERSION, "count": len(rows), "rows": rows}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "promo_codes_list_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}), 500


@bp.post("/promo/admin/codes")
def promo_admin_create_or_update_code():
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    code = _normalize_code(body.get("code") or body.get("promo_code"))
    if not code:
        return jsonify({"ok": False, "error": "code_required", "message": "Promo code is required."}), 400

    now_iso = _now_iso()
    payload = {
        "code": code,
        "name": _clean(body.get("name")) or code,
        "description": _clean(body.get("description")) or None,
        "status": _lower(body.get("status")) or "active",
        "promo_type": _lower(body.get("promo_type")) or "influencer",
        "benefit_type": _lower(body.get("benefit_type")) or "percent_discount",
        "discount_percent": _to_decimal_string(body.get("discount_percent"), "0"),
        "discount_amount_ngn": _to_decimal_string(body.get("discount_amount_ngn"), "0"),
        "bonus_credits": _to_int(body.get("bonus_credits"), 0),
        "reward_type": _lower(body.get("reward_type")) or "cash",
        "reward_amount_ngn": _to_decimal_string(body.get("reward_amount_ngn"), "0"),
        "reward_percent": _to_decimal_string(body.get("reward_percent"), "0"),
        "owner_account_id": _clean(body.get("owner_account_id")) or None,
        "owner_name": _clean(body.get("owner_name")) or None,
        "owner_email": _clean(body.get("owner_email")).lower() or None,
        "starts_at": _clean(body.get("starts_at")) or None,
        "expires_at": _clean(body.get("expires_at")) or None,
        "max_uses": body.get("max_uses") if body.get("max_uses") not in ("", None) else None,
        "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        "updated_at": now_iso,
    }

    try:
        existing = _get_code_row(code)
        if existing:
            row = _update_code_by_id(existing.get("id"), payload)
            action = "updated"
        else:
            payload["used_count"] = _to_int(body.get("used_count"), 0)
            payload["paid_conversion_count"] = _to_int(body.get("paid_conversion_count"), 0)
            payload["created_at"] = now_iso
            row = _insert_code(payload)
            action = "created"

        return jsonify({
            "ok": True,
            "route_version": PROMO_ROUTE_VERSION,
            "action": action,
            "code": code,
            "row": row,
            "links": build_promo_links(code),
        }), 200
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": "promo_code_upsert_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            "payload": payload,
        }), 500


@bp.post("/promo/admin/codes/<code>/assign-owner")
def promo_admin_assign_owner(code: str):
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    code = _normalize_code(code)
    body = request.get_json(silent=True) or {}
    row = _get_code_row(code)
    if not row:
        return jsonify({"ok": False, "error": "promo_code_not_found", "code": code}), 404

    payload = {
        "owner_account_id": _clean(body.get("owner_account_id")) or None,
        "owner_name": _clean(body.get("owner_name")) or None,
        "owner_email": _clean(body.get("owner_email")).lower() or None,
        "updated_at": _now_iso(),
    }

    try:
        updated = _update_code_by_id(row.get("id"), payload)
        return jsonify({
            "ok": True,
            "route_version": PROMO_ROUTE_VERSION,
            "message": "Promo code owner assigned.",
            "code": code,
            "row": updated,
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "promo_owner_assignment_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}), 500


@bp.get("/promo/admin/redemptions")
def promo_admin_redemptions():
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    limit = _to_int(request.args.get("limit"), 100)
    code = _normalize_code(request.args.get("code"))
    status = _lower(request.args.get("status"))

    try:
        query = _sb().table("promo_redemptions").select("*").order("created_at", desc=True)
        if code:
            query = query.eq("promo_code", code)
        if status:
            query = query.eq("status", status)
        resp = query.limit(max(1, min(limit, 500))).execute()
        rows = _response_data(resp)
        return jsonify({"ok": True, "route_version": PROMO_ROUTE_VERSION, "count": len(rows), "rows": rows}), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "promo_redemptions_list_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}), 500


@bp.get("/promo/admin/rewards")
def promo_admin_rewards():
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    limit = _to_int(request.args.get("limit"), 100)
    code = _normalize_code(request.args.get("code"))
    status = _lower(request.args.get("status"))

    try:
        query = _sb().table("promo_rewards").select("*").order("created_at", desc=True)
        if code:
            query = query.eq("promo_code", code)
        if status:
            query = query.eq("status", status)
        resp = query.limit(max(1, min(limit, 500))).execute()
        rows = _response_data(resp)

        total_pending = 0
        for row in rows:
            if _lower(row.get("status")) == "pending":
                try:
                    total_pending += float(row.get("reward_amount_ngn") or 0)
                except Exception:
                    pass

        return jsonify({
            "ok": True,
            "route_version": PROMO_ROUTE_VERSION,
            "count": len(rows),
            "summary": {"pending_reward_amount_ngn": total_pending},
            "rows": rows,
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "promo_rewards_list_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}), 500


@bp.post("/promo/admin/rewards/<reward_id>/mark-status")
def promo_admin_reward_mark_status(reward_id: str):
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    status = _lower(body.get("status"))
    allowed = {"pending", "processing", "approved", "paid", "failed", "reversed", "cancelled", "canceled"}
    if status not in allowed:
        return jsonify({"ok": False, "error": "invalid_reward_status", "allowed": sorted(allowed)}), 400

    now_iso = _now_iso()
    payload: Dict[str, Any] = {
        "status": status,
        "updated_at": now_iso,
    }

    if status == "approved":
        payload["approved_at"] = now_iso
    elif status == "paid":
        payload["paid_at"] = now_iso
    elif status in {"failed", "reversed", "cancelled", "canceled"}:
        payload["reversed_at"] = now_iso
        payload["reversal_reason"] = _clean(body.get("reason")) or _clean(body.get("failure_reason")) or status

    if isinstance(body.get("metadata"), dict):
        payload["metadata"] = body.get("metadata")

    try:
        resp = _sb().table("promo_rewards").update(payload).eq("id", reward_id).execute()
        row = _first(resp)
        return jsonify({
            "ok": True,
            "route_version": PROMO_ROUTE_VERSION,
            "reward_id": reward_id,
            "status": status,
            "row": row,
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": "promo_reward_status_update_failed", "root_cause": f"{type(exc).__name__}: {_clip(exc)}"}), 500
