# app/routes/paystack.py
from __future__ import annotations

from typing import Any, Dict, Optional
from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.plans_service import get_plan
from app.services.paystack_service import create_reference, initialize_transaction, verify_transaction
from app.services.subscriptions_service import activate_subscription_now

bp = Blueprint("paystack", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _err(
    http_status: int,
    code: str,
    message: str,
    *,
    root_cause: Optional[Exception] = None,
    extra: Optional[Dict[str, Any]] = None,
):
    payload: Dict[str, Any] = {"ok": False, "error": code, "message": message}
    if extra:
        payload["extra"] = extra
    if root_cause:
        payload["root_cause"] = {
            "type": root_cause.__class__.__name__,
            "message": str(root_cause),
        }
    return jsonify(payload), http_status


def _extract_account_and_plan(body: Dict[str, Any]) -> tuple[str, str]:
    """
    Supports BOTH request shapes you used:

    A) flat:
      { account_id, plan_code, email }

    B) metadata-based:
      { email, metadata: { account_id, plan_code, ... } }
    """
    account_id = (body.get("account_id") or "").strip()
    plan_code = (body.get("plan_code") or "").strip().lower()

    md = body.get("metadata") or {}
    if not account_id:
        account_id = (md.get("account_id") or "").strip()
    if not plan_code:
        plan_code = (md.get("plan_code") or "").strip().lower()

    return account_id, plan_code


def _ensure_account_row(account_id: str, email: str):
    """
    Fixes your FK error:
      user_subscriptions.account_id -> accounts.account_id

    If account row is missing, create it best-effort.
    """
    if not account_id:
        return

    try:
        # If it already exists, do nothing
        existing = (
            _sb()
            .table("accounts")
            .select("account_id")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (existing.data or []) if hasattr(existing, "data") else []
        if rows:
            return

        # Insert minimal row (avoid columns that might not exist)
        _sb().table("accounts").insert(
            {
                "account_id": account_id,
                "provider": "web",
                "provider_user_id": (email or account_id),
                "display_name": (email or "Web User"),
            }
        ).execute()
    except Exception:
        # best-effort only; activation will still fail if FK requires strict fields
        return


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
    """
    Start a Paystack payment.

    Supports:
    - { account_id, plan_code, email }
    - { email, metadata: { account_id, plan_code, ... }, amount_kobo?, currency? }
    """
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

    # keep whatever metadata user sent, but enforce needed keys
    metadata = dict((body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {})
    metadata.update(
        {
            "account_id": account_id,
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

        # Store initiated tx best-effort
        try:
            _sb().table("paystack_transactions").insert(
                {
                    "reference": reference,
                    "account_id": account_id,
                    "plan_code": plan_code,
                    "amount": amount_naira,
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
                "plan_code": plan_code,
                "amount_kobo": amount_kobo,
            }
        ), 200

    except Exception as e:
        return _err(400, "paystack_init_failed", "could not initialize transaction", root_cause=e)


@bp.get("/paystack/verify/<reference>")
def paystack_verify(reference: str):
    """
    Verify a transaction and (if successful) activate the subscription.
    """
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

        # Update tx row best-effort
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
            return _err(
                400,
                "payment_not_successful",
                "payment not successful",
                extra={"paystack_status": status, "reference": reference},
            )

        if not account_id or not plan_code:
            return _err(400, "missing_metadata", "missing account_id/plan_code in metadata", extra={"metadata": metadata})

        # ✅ Fix FK issue: ensure accounts row exists before subscription upsert
        _ensure_account_row(account_id, email=(tx.get("customer") or {}).get("email") or "")

        sub = activate_subscription_now(account_id=account_id, plan_code=plan_code, status="active")

        return jsonify({"ok": True, "reference": reference, "subscription": sub}), 200

    except Exception as e:
        return _err(400, "paystack_verify_failed", "could not verify/activate", root_cause=e)
