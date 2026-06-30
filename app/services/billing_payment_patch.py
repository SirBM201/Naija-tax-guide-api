from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from flask import has_request_context, request


BILLING_PAYMENT_PATCH_VERSION = "2026-06-30-payment-context-recovery-v2"


def apply_billing_payment_patch() -> None:
    """Harden payment activation and subscription entitlement recovery.

    The billing routes call module-level helper functions at request time. Patching
    those helpers here lets callback, verify, webhook, and workspace entitlement
    paths recover the same current subscription state without replacing blueprints.
    """
    _patch_subscription_guard_latest_row()

    try:
        from app.routes import billing as b
    except Exception:
        return

    def _normalize_metadata(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _request_arg(name: str) -> str:
        if not has_request_context():
            return ""
        try:
            return b._clean(request.args.get(name))
        except Exception:
            return ""

    def _nested_dict(value: Any, key: str) -> Dict[str, Any]:
        if isinstance(value, dict) and isinstance(value.get(key), dict):
            return value.get(key) or {}
        return {}

    def _extract_payment_context(reference: str, paystack_data: Dict[str, Any]) -> Dict[str, Any]:
        transaction_row = b._find_transaction(reference) or {}

        tx_meta = _normalize_metadata(transaction_row.get("metadata"))
        ps_meta = _normalize_metadata(paystack_data.get("metadata"))

        event_payload = _normalize_metadata(transaction_row.get("payload"))
        event_data = _nested_dict(event_payload, "data")
        event_meta = _normalize_metadata(event_data.get("metadata"))

        metadata = b._merge_metadata(tx_meta, event_meta, ps_meta)

        plan_hint = b._lower(
            _request_arg("plan")
            or _request_arg("plan_code")
            or metadata.get("plan")
        )
        account_hint = b._clean(
            _request_arg("account")
            or _request_arg("account_id")
            or metadata.get("account")
        )

        amount = (
            paystack_data.get("amount")
            or event_data.get("amount")
            or transaction_row.get("amount_kobo")
            or transaction_row.get("amount")
            or metadata.get("amount_kobo")
            or metadata.get("amount")
            or 0
        )
        amount_kobo = b._as_int(amount, 0)
        if amount_kobo and amount_kobo < 1000 and metadata.get("amount_ngn"):
            amount_kobo = b._as_int(metadata.get("amount_ngn"), 0) * 100

        account_id = b._clean(
            metadata.get("account_id")
            or transaction_row.get("account_id")
            or event_data.get("account_id")
            or account_hint
        )
        plan_code = b._lower(
            metadata.get("plan_code")
            or transaction_row.get("plan_code")
            or event_data.get("plan_code")
            or plan_hint
        )

        metadata.update(
            {
                "reference": reference,
                "amount": amount_kobo,
                "amount_kobo": amount_kobo,
                "paid_at": paystack_data.get("paid_at") or event_data.get("paid_at") or paystack_data.get("created_at") or event_data.get("created_at") or b._now_iso(),
                "currency": paystack_data.get("currency") or event_data.get("currency") or metadata.get("currency") or "NGN",
                "gateway_response": paystack_data.get("gateway_response") or event_data.get("gateway_response"),
            }
        )
        if account_id:
            metadata["account_id"] = account_id
        if plan_code:
            metadata["plan_code"] = plan_code

        return {
            "transaction_row": transaction_row,
            "metadata": metadata,
            "account_id": account_id,
            "plan_code": plan_code,
            "amount_kobo": amount_kobo,
            "status": b._lower(paystack_data.get("status") or event_data.get("status")),
            "patch_version": BILLING_PAYMENT_PATCH_VERSION,
        }

    def _apply_successful_payment(reference: str, paystack_data: Dict[str, Any]) -> Dict[str, Any]:
        context = _extract_payment_context(reference, paystack_data)
        metadata = context["metadata"]
        account_id = context["account_id"]
        plan_code = context["plan_code"]
        amount_kobo = context["amount_kobo"]
        transaction_row = context["transaction_row"]

        if not account_id:
            return {
                "ok": False,
                "applied": False,
                "error": "missing_account_id",
                "reference": reference,
                "patch_version": BILLING_PAYMENT_PATCH_VERSION,
                "fix": "Paystack metadata, paystack_transactions, webhook payload, or callback query must include account_id.",
            }

        if b._is_credit_topup_metadata(metadata, transaction_row):
            topup_code = b._topup_code_from_payload(metadata)
            package = b._get_topup_package(topup_code)
            credits = b._as_int(metadata.get("credits"), 0)
            if package:
                credits = b._as_int(package.get("credits"), credits)
                metadata.update(
                    {
                        "type": "credit_topup",
                        "purpose": "usage_topup",
                        "topup_code": package["code"],
                        "package_code": package["code"],
                        "package_name": package["name"],
                        "credits": package["credits"],
                        "amount_ngn": package["amount_ngn"],
                        "amount_kobo": package["amount_kobo"],
                    }
                )

            if credits <= 0:
                return {"ok": False, "applied": False, "error": "missing_topup_credits", "reference": reference, "patch_version": BILLING_PAYMENT_PATCH_VERSION}

            credit_application = b._add_credits_to_balance(account_id, credits, reference, metadata)
            return {
                "ok": bool(credit_application.get("ok")),
                "applied": bool(credit_application.get("ok")),
                "payment_type": "credit_topup",
                "reference": reference,
                "account_id": account_id,
                "plan_code": metadata.get("topup_code") or metadata.get("package_code"),
                "credit_application": credit_application,
                "patch_version": BILLING_PAYMENT_PATCH_VERSION,
            }

        if not plan_code:
            return {
                "ok": False,
                "applied": False,
                "error": "missing_plan_code",
                "reference": reference,
                "patch_version": BILLING_PAYMENT_PATCH_VERSION,
                "fix": "Paystack metadata, paystack_transactions, webhook payload, or callback query must include plan_code.",
            }

        activation = b._activate_subscription(account_id, plan_code, reference, metadata={**metadata, "amount_kobo": amount_kobo})
        return {
            "ok": bool(activation.get("ok")),
            "applied": bool(activation.get("ok")),
            "payment_type": "subscription",
            "reference": reference,
            "account_id": account_id,
            "plan_code": plan_code,
            "subscription": activation,
            "patch_version": BILLING_PAYMENT_PATCH_VERSION,
        }

    b._normalize_metadata = _normalize_metadata
    b._extract_payment_context = _extract_payment_context
    b._apply_successful_payment = _apply_successful_payment


def _patch_subscription_guard_latest_row() -> None:
    try:
        from app.services import subscription_guard as sg
    except Exception:
        return

    def _safe_dt(value: Any) -> datetime:
        try:
            if not value:
                return datetime.min.replace(tzinfo=timezone.utc)
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    def _row_sort_key(row: Dict[str, Any]) -> tuple:
        normalized = sg._normalize_sub_row(row or {})
        active = 1 if sg._subscription_is_active_now(normalized) else 0
        status = str(normalized.get("status") or "").strip().lower()
        paid_plan = 1 if str(normalized.get("plan_code") or "").strip().lower() not in {"", "free", "free_forever"} else 0
        status_rank = 1 if status in {"active", "trial", "grace", "past_due"} else 0
        updated = _safe_dt(row.get("updated_at") or normalized.get("updated_at"))
        created = _safe_dt(row.get("created_at") or normalized.get("created_at"))
        period_end = _safe_dt(row.get("expires_at") or row.get("current_period_end") or normalized.get("expires_at") or normalized.get("current_period_end"))
        return (active, paid_plan, status_rank, updated, created, period_end)

    def _get_subscription_row_latest(account_id: str):
        account_id = (account_id or "").strip()
        if not account_id:
            return None, {
                "ok": False,
                "error": "account_id_required",
                "root_cause": "missing_account_id",
                "fix": "Pass canonical account_id to the subscription guard.",
            }

        try:
            query = sg._sb().table("user_subscriptions").select("*").eq("account_id", account_id)
            try:
                query = query.order("updated_at", desc=True)
            except Exception:
                pass
            res = query.limit(25).execute()
            rows = getattr(res, "data", None) or []
            if not rows:
                return None, None
            best_row = sorted([r for r in rows if isinstance(r, dict)], key=_row_sort_key, reverse=True)[0]
            return sg._normalize_sub_row(best_row or {}), None
        except Exception as e:
            return None, {
                "ok": False,
                "error": "subscription_lookup_failed",
                "root_cause": f"{type(e).__name__}: {sg._clip(e)}",
                "fix": "Check user_subscriptions table access and Supabase connectivity.",
                "details": {"account_id": account_id},
            }

    sg._get_subscription_row = _get_subscription_row_latest
