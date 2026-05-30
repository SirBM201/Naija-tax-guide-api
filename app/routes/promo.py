# app/routes/promo.py
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, redirect, request

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

PROMO_ROUTE_VERSION = "2026-05-30-batch35B-promo-checkout-direct-upload"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _is_api_path(*paths: str) -> bool:
    current = request.path.rstrip("/")
    return current in {p.rstrip("/") for p in paths}


def _billing():
    # Import lazily so this optional promo blueprint does not break boot if billing imports later.
    from app.routes import billing as billing_route

    return billing_route


def _json_error_from_billing(message: str, status: int, *, error: str, **extra: Any):
    billing_route = _billing()
    return billing_route._json_error(message, status, error=error, **extra)


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
            "billing_route_version": "2026-05-30-v35B-promo-checkout-discount-reward",
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
        "billing_route_version": "2026-05-30-v35B-promo-checkout-discount-reward",
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
            "billing_route_version": "2026-05-30-v35B-promo-checkout-discount-reward",
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
    """
    Batch 35B direct-upload override.

    Because this app already registers app.routes.promo as an optional blueprint,
    this interceptor lets us upgrade billing checkout safely without replacing
    the large existing app/routes/billing.py file.
    """
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
        "batch35b": {
            "checkout_interceptor": True,
            "discount_source": "existing promo_redemptions row attached during signup",
            "payment_rule": "No promo code entry at payment.",
        },
        "expected_urls": [
            "/api/promo/health",
            "/api/promo/validate/TAXWITHBM",
            "/api/promo/hub/TAXWITHBM",
            "/api/promo/track?code=TAXWITHBM&platform=website",
            "/api/promo/track-and-go/TAXWITHBM/website",
            "/api/billing/initialize",
            "/api/billing/verify",
            "/api/billing/callback",
        ],
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
