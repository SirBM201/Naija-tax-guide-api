# app/routes/paystack.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan
from app.services.paystack_service import create_reference, initialize_transaction, verify_transaction
from app.services.subscriptions_service import activate_subscription_now

bp = Blueprint("paystack", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _err(http_status: int, code: str, message: str, *, root_cause: Optional[Exception] = None, extra: Optional[Dict[str, Any]] = None):
    payload: Dict[str, Any] = {"ok": False, "error": code, "message": message}
    if extra:
        payload["extra"] = extra
    if root_cause:
        payload["root_cause"] = {"type": root_cause.__class__.__name__, "message": str(root_cause)}
    return jsonify(payload), http_status


def _extract_account_and_plan(body: Dict[str, Any]) -> Tuple[str, str]:
    account_id = (body.get("account_id") or "").strip()
    plan_code = (body.get("plan_code") or "").strip().lower()

    md = body.get("metadata") or {}
    if not account_id:
        account_id = (md.get("account_id") or "").strip()
    if not plan_code:
        plan_code = (md.get("plan_code") or "").strip().lower()

    return account_id, plan_code


@bp.get("/paystack/health")
def paystack_health():
    from app.core.config import PAYSTACK_SECRET_KEY, PAYSTACK_CURRENCY, PAYSTACK_CALLBACK_URL

    return jsonify(
        {
            "ok": True,
            "secret_key_set": bool(PAYSTACK_SECRET_KEY),
            "currency": PAYSTACK_CURRENCY,
            "callback_url_set": bool((PAYSTACK_CALLBACK_URL or "").strip()),
            "paystack_base": "https://api.paystack.co",
        }
    ), 200


@bp.post("/paystack/init")
def paystack_init():
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    account_id, plan_code = _extract_account_and_plan(body)

    if not email:
        return _err(400, "email_required", "email is required")
    if not account_id:
        return _err(400, "account_id_required", "account_id is required (direct or in metadata)")
    if not plan_code:
        return _err(400, "plan_code_required", "plan_code is required (direct or in metadata)")

    plan = get_plan(plan_code)
    if not plan or not plan.get("active", True):
        return _err(400, "invalid_plan", "plan_code is invalid or inactive", extra={"plan_code": plan_code})

    amount_naira = int(plan.get("price") or 0)
    if amount_naira <= 0:
        return _err(400, "invalid_plan_price", "plan price must be > 0", extra={"plan_code": plan_code})

    amount_kobo = amount_naira * 100
    currency = (body.get("currency") or "NGN").strip() or "NGN"
    reference = create_reference("NTG")

    # enforce required metadata keys
    metadata = dict((body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {})
    metadata.update(
        {
            "account_id": account_id,        # may be accounts.id OR accounts.account_id; service will resolve later
            "plan_code": plan_code,
            "purpose": metadata.get("purpose") or "subscription",
            "channel": metadata.get("channel") or "web",
            "product": metadata.get("product") or "ntg_subscription",
        }
    )

    try:
        init_data = initialize_transaction(
            email=email,
            amount_kobo=amount_kobo,
            reference=reference,
            metadata=metadata,
            currency=currency,
        )
        d = init_data.get("data") or {}

        # Store initiated tx best-effort (don’t assume extra columns exist)
        try:
            _sb().table("paystack_transactions").insert(
                {
                    "reference": reference,
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "amount": amount_naira,
                    "currency": d.get("currency") or currency,
                    "status": "initiated",
                    "raw": init_data,
                }
            ).execute()
        except Exception:
            pass

        return jsonify(
            {
                "ok": True,
                "authorization_url": d.get("authorization_url"),
                "access_code": d.get("access_code"),
                "reference": reference,
                "plan_code": plan_code,
                "amount_kobo": amount_kobo,
            }
        ), 200

    except Exception as e:
        return _err(400, "paystack_init_failed", "could not initialize transaction", root_cause=e)


@bp.get("/paystack/verify/<reference>")
def paystack_verify(reference: str):
    reference = (reference or "").strip()
    if not reference:
        return _err(400, "missing_reference", "reference is required")

    try:
        data = verify_transaction(reference)
        tx = (data.get("data") or {})
        status = (tx.get("status") or "").lower()
        metadata = tx.get("metadata") or {}

        account_id = (metadata.get("account_id") or "").strip()
        plan_code = (metadata.get("plan_code") or "").strip().lower()

        # Update tx best-effort (fallback if schema differs)
        try:
            _sb().table("paystack_transactions").update(
                {
                    "paystack_status": status,
                    "paid_at": tx.get("paid_at"),
                    "raw": data,
                    "status": "success" if status == "success" else "failed",
                }
            ).eq("reference", reference).execute()
        except Exception:
            try:
                _sb().table("paystack_transactions").update(
                    {"raw": data, "status": "success" if status == "success" else "failed"}
                ).eq("reference", reference).execute()
            except Exception:
                pass

        if status != "success":
            return _err(400, "payment_not_successful", "payment not successful", extra={"paystack_status": status, "reference": reference})

        if not account_id or not plan_code:
            return _err(400, "missing_metadata", "missing account_id/plan_code in metadata", extra={"metadata": metadata})

        sub = activate_subscription_now(
            account_id=account_id,
            plan_code=plan_code,
            status="active",
            provider="paystack",
            provider_ref=reference,
        )

        return jsonify({"ok": True, "reference": reference, "subscription": sub}), 200

    except Exception as e:
        return _err(400, "paystack_verify_failed", "could not verify/activate", root_cause=e)
