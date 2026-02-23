# app/routes/paystack.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.config import ENV, WEB_AUTH_DEBUG
from app.core.supabase_client import supabase
from app.services.plans_service import get_plan
from app.services.paystack_service import (
    create_reference,
    initialize_transaction,
    verify_transaction,
    verify_webhook_signature,
)
from app.services.subscriptions_service import activate_subscription_now

bp = Blueprint("paystack", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _debug_enabled() -> bool:
    # In prod you can keep WEB_AUTH_DEBUG=False
    return bool(WEB_AUTH_DEBUG) or (ENV.lower() != "prod")


def _err(error: str, *, status: int = 400, **extra):
    payload = {"ok": False, "error": error}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _parse_init_request(body: Dict[str, Any]) -> Tuple[str, str, int, str, Dict[str, Any]]:
    """
    Accept BOTH formats:

    A) Old format:
      {
        "account_id": "<uuid>",
        "plan_code": "monthly|quarterly|yearly",
        "email": "user@email.com"
      }

    B) New format (PowerShell / frontend):
      {
        "email": "...",
        "amount_kobo": 20000,
        "currency": "NGN",
        "metadata": {
          "account_id": "...",
          "plan_code": "monthly",
          "channel": "web",
          "purpose": "subscription"
        }
      }

    Returns: (account_id, plan_code, amount_kobo, currency, metadata)
    """
    email = (body.get("email") or "").strip()

    # Prefer metadata path first (new format)
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    account_id = (meta.get("account_id") or body.get("account_id") or "").strip()
    plan_code = (meta.get("plan_code") or body.get("plan_code") or "").strip().lower()

    # amount handling:
    # - new: amount_kobo
    # - old: derive from plan price (naira) * 100
    amount_kobo: Optional[int] = None
    if body.get("amount_kobo") is not None:
        try:
            amount_kobo = int(body.get("amount_kobo"))
        except Exception:
            amount_kobo = None
    elif body.get("amount") is not None:
        # some callers mistakenly send amount already in kobo — keep as-is
        try:
            amount_kobo = int(body.get("amount"))
        except Exception:
            amount_kobo = None

    currency = (body.get("currency") or "NGN").strip() or "NGN"

    metadata = dict(meta) if isinstance(meta, dict) else {}
    # Ensure canonical fields
    if account_id:
        metadata["account_id"] = account_id
    if plan_code:
        metadata["plan_code"] = plan_code
    metadata.setdefault("purpose", "subscription")

    # If amount_kobo not provided, derive from plan (old flow)
    if amount_kobo is None:
        if not plan_code:
            raise ValueError("plan_code_required")
        plan = get_plan(plan_code)
        if not plan or not plan.get("active", True):
            raise ValueError("invalid_plan")
        amount_naira = int(plan.get("price") or 0)
        if amount_naira <= 0:
            raise ValueError("invalid_plan_price")
        amount_kobo = amount_naira * 100

    if not email:
        raise ValueError("email_required")
    if not account_id:
        raise ValueError("account_id_required")
    if not plan_code:
        raise ValueError("plan_code_required")
    if int(amount_kobo) <= 0:
        raise ValueError("invalid_amount_kobo")

    return account_id, plan_code, int(amount_kobo), currency, metadata


@bp.post("/paystack/init")
def paystack_init():
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    try:
        account_id, plan_code, amount_kobo, currency, metadata = _parse_init_request(body)
        reference = create_reference("NTG")

        init_data = initialize_transaction(
            email=(body.get("email") or "").strip(),
            amount_kobo=amount_kobo,
            reference=reference,
            currency=currency,
            metadata=metadata,
        )

        d = init_data.get("data") or {}

        # Store initiated transaction (best-effort, do not block)
        try:
            _sb().table("paystack_transactions").insert(
                {
                    "reference": reference,
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "amount_kobo": amount_kobo,
                    "currency": d.get("currency") or currency,
                    "status": "initiated",
                    "authorization_url": d.get("authorization_url"),
                    "access_code": d.get("access_code"),
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
            }
        ), 200

    except Exception as e:
        if _debug_enabled():
            return _err(
                "paystack_init_failed",
                status=400,
                detail=str(e),
                detail_type=e.__class__.__name__,
            )
        return _err("paystack_init_failed", status=400)


@bp.get("/paystack/verify/<reference>")
def paystack_verify(reference: str):
    reference = (reference or "").strip()
    if not reference:
        return _err("missing_reference", status=400)

    try:
        data = verify_transaction(reference)
        tx = (data.get("data") or {})
        status = (tx.get("status") or "").lower()
        metadata = tx.get("metadata") or {}

        account_id = (metadata.get("account_id") or "").strip()
        plan_code = (metadata.get("plan_code") or "").strip().lower()

        # update transaction row (best-effort)
        try:
            _sb().table("paystack_transactions").update(
                {
                    "paystack_status": status,
                    "transaction_id": str(tx.get("id") or ""),
                    "paid_at": tx.get("paid_at"),
                    "raw": data,
                    "status": "success" if status == "success" else "failed",
                }
            ).eq("reference", reference).execute()
        except Exception:
            pass

        if status != "success":
            return _err("payment_not_successful", status=400, paystack_status=status)

        if not account_id or not plan_code:
            return _err("missing_metadata", status=400)

        # Activate subscription (should be written idempotently in your subscriptions_service)
        sub = activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")

        return jsonify({"ok": True, "reference": reference, "subscription": sub}), 200

    except Exception as e:
        if _debug_enabled():
            return _err(
                "paystack_verify_failed",
                status=400,
                detail=str(e),
                detail_type=e.__class__.__name__,
            )
        return _err("paystack_verify_failed", status=400)


@bp.post("/paystack/webhook")
def paystack_webhook():
    """
    Paystack sends raw JSON + header:
      x-paystack-signature: <hmac sha512 hex>

    We validate signature on raw body, then process.
    """
    sig = request.headers.get("x-paystack-signature", "")
    raw = request.get_data(cache=False) or b""

    if not verify_webhook_signature(raw_body=raw, signature_header=sig):
        return _err("invalid_signature", status=401)

    # Parse JSON after signature verification
    payload: Dict[str, Any] = request.get_json(silent=True) or {}
    event = (payload.get("event") or "").strip().lower()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    # Always ack quickly
    try:
        if event == "charge.success":
            reference = (data.get("reference") or "").strip()
            status = (data.get("status") or "").strip().lower()
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            account_id = (metadata.get("account_id") or "").strip()
            plan_code = (metadata.get("plan_code") or "").strip().lower()

            # update tx best-effort
            try:
                _sb().table("paystack_transactions").update(
                    {
                        "paystack_status": status,
                        "transaction_id": str(data.get("id") or ""),
                        "paid_at": data.get("paid_at"),
                        "raw": payload,
                        "status": "success" if status == "success" else "failed",
                    }
                ).eq("reference", reference).execute()
            except Exception:
                pass

            # activate if success + metadata present
            if status == "success" and account_id and plan_code:
                try:
                    activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")
                except Exception:
                    # Don't fail webhook ack
                    pass

        return jsonify({"ok": True}), 200

    except Exception as e:
        # Still ACK 200 to avoid Paystack retry storms, but expose debug optionally
        if _debug_enabled():
            return jsonify({"ok": True, "note": "webhook_handled_with_internal_error", "detail": str(e)}), 200
        return jsonify({"ok": True}), 200
