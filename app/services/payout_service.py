from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from supabase import Client

from app.core.supabase_client import get_supabase_client


ALLOWED_BULK_ACTIONS = {"mark-processing", "mark-paid", "mark-failed"}


@dataclass
class PayoutUpdateResult:
    payout: Dict[str, Any]
    updated_reward_ids: List[str]
    audit_logged: bool


class PayoutServiceError(Exception):
    pass


class PayoutValidationError(PayoutServiceError):
    pass


class PayoutNotFoundError(PayoutServiceError):
    pass


class PayoutService:
    def __init__(self, supabase: Client):
        self.supabase = supabase

    def get_queue(self, statuses: Sequence[str], limit: int = 200) -> List[Dict[str, Any]]:
        normalized_statuses = [self._normalize_status(item) for item in statuses if self._normalize_status(item) != "unknown"]
        if not normalized_statuses:
            normalized_statuses = ["pending", "processing", "failed"]

        response = (
            self.supabase.table("referral_payouts")
            .select("*")
            .in_("status", normalized_statuses)
            .order("requested_at", desc=True)
            .limit(max(1, min(int(limit or 200), 500)))
            .execute()
        )
        return response.data or []

    def get_payout(self, payout_id: str) -> Dict[str, Any]:
        payout_id = str(payout_id or "").strip()
        if not payout_id:
            raise PayoutValidationError("Payout ID is required.")

        response = (
            self.supabase.table("referral_payouts")
            .select("*")
            .eq("id", payout_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise PayoutNotFoundError(f"Payout {payout_id} was not found.")
        return rows[0]

    def get_audit_history(self, payout_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        payout_id = str(payout_id or "").strip()
        if not payout_id:
            raise PayoutValidationError("Payout ID is required.")

        response = (
            self.supabase.table("referral_payout_audit_logs")
            .select("*")
            .eq("payout_id", payout_id)
            .order("created_at", desc=True)
            .limit(max(1, min(int(limit or 100), 300)))
            .execute()
        )
        return response.data or []

    def mark_processing(
        self,
        payout_id: str,
        provider_reference: Optional[str] = None,
        provider_transfer_code: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PayoutUpdateResult:
        payout = self.get_payout(payout_id)
        old_status = self._normalize_status(payout.get("status"))
        if old_status not in {"pending", "failed", "unknown"}:
            raise PayoutValidationError("Only pending or failed payouts can be marked processing.")

        now_iso = self._now_iso()
        update_payload = {
            "status": "processing",
            "processed_at": now_iso,
            "failed_at": None,
            "failure_reason": None,
            "provider_reference": self._coalesce(provider_reference, payout.get("provider_reference")),
            "provider_transfer_code": self._coalesce(provider_transfer_code, payout.get("provider_transfer_code")),
            "updated_at": now_iso,
        }

        updated = self._update_payout_row(str(payout["id"]), update_payload)
        self._log_audit(
            payout_id=str(updated["id"]),
            account_id=str(updated.get("account_id") or ""),
            action="mark_processing",
            old_status=old_status,
            new_status="processing",
            provider_reference=updated.get("provider_reference"),
            provider_transfer_code=updated.get("provider_transfer_code"),
            failure_reason=None,
            metadata=metadata,
        )
        return PayoutUpdateResult(payout=updated, updated_reward_ids=[], audit_logged=True)

    def mark_paid(
        self,
        payout_id: str,
        provider_reference: Optional[str] = None,
        provider_transfer_code: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PayoutUpdateResult:
        payout = self.get_payout(payout_id)
        old_status = self._normalize_status(payout.get("status"))
        if old_status not in {"processing", "unknown"}:
            raise PayoutValidationError("Only processing payouts can be marked paid.")

        account_id = str(payout.get("account_id") or "").strip()
        if not account_id:
            raise PayoutValidationError("Payout record is missing account_id.")

        amount = self._to_decimal(payout.get("amount"))
        rewards = self._get_payable_rewards(account_id)
        total_payable = sum(self._to_decimal(row.get("reward_amount")) for row in rewards)

        if total_payable + Decimal("0.009") < amount:
            raise PayoutValidationError(
                f"Approved rewards total {total_payable:.2f}, which is less than payout amount {amount:.2f}."
            )

        paid_at = self._now_iso()
        updated_reward_ids = [str(row.get("id")) for row in rewards if str(row.get("id") or "").strip()]

        update_payload = {
            "status": "paid",
            "paid_at": paid_at,
            "failed_at": None,
            "failure_reason": None,
            "provider_reference": self._coalesce(provider_reference, payout.get("provider_reference")),
            "provider_transfer_code": self._coalesce(provider_transfer_code, payout.get("provider_transfer_code")),
            "updated_at": paid_at,
        }

        updated = self._update_payout_row(str(payout["id"]), update_payload)
        self._mark_rewards_paid(account_id=account_id, paid_at=paid_at)

        self._log_audit(
            payout_id=str(updated["id"]),
            account_id=account_id,
            action="mark_paid",
            old_status=old_status,
            new_status="paid",
            provider_reference=updated.get("provider_reference"),
            provider_transfer_code=updated.get("provider_transfer_code"),
            failure_reason=None,
            metadata={
                **(metadata or {}),
                "updated_reward_ids": updated_reward_ids,
                "reward_count": len(updated_reward_ids),
            },
        )

        return PayoutUpdateResult(
            payout=updated,
            updated_reward_ids=updated_reward_ids,
            audit_logged=True,
        )

    def mark_failed(
        self,
        payout_id: str,
        failure_reason: str,
        provider_reference: Optional[str] = None,
        provider_transfer_code: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PayoutUpdateResult:
        payout = self.get_payout(payout_id)
        old_status = self._normalize_status(payout.get("status"))
        if old_status == "paid":
            raise PayoutValidationError("Paid payouts cannot be marked failed.")

        cleaned_reason = (failure_reason or "").strip()
        if not cleaned_reason:
            raise PayoutValidationError("Failure reason is required when marking a payout as failed.")

        update_payload = {
            "status": "failed",
            "failed_at": self._now_iso(),
            "failure_reason": cleaned_reason,
            "provider_reference": self._coalesce(provider_reference, payout.get("provider_reference")),
            "provider_transfer_code": self._coalesce(provider_transfer_code, payout.get("provider_transfer_code")),
            "updated_at": self._now_iso(),
        }

        updated = self._update_payout_row(str(payout["id"]), update_payload)
        self._log_audit(
            payout_id=str(updated["id"]),
            account_id=str(updated.get("account_id") or ""),
            action="mark_failed",
            old_status=old_status,
            new_status="failed",
            provider_reference=updated.get("provider_reference"),
            provider_transfer_code=updated.get("provider_transfer_code"),
            failure_reason=cleaned_reason,
            metadata=metadata,
        )
        return PayoutUpdateResult(payout=updated, updated_reward_ids=[], audit_logged=True)

    def bulk_update(
        self,
        action: str,
        payout_ids: Sequence[str],
        provider_reference: Optional[str] = None,
        provider_transfer_code: Optional[str] = None,
        failure_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if action not in ALLOWED_BULK_ACTIONS:
            raise PayoutValidationError("Unsupported bulk payout action.")

        unique_ids = [item for item in dict.fromkeys(str(x).strip() for x in payout_ids if str(x).strip())]
        if not unique_ids:
            raise PayoutValidationError("At least one payout ID is required for a bulk action.")

        payouts = self._get_payout_rows(unique_ids)
        payout_map = {str(row.get("id")): row for row in payouts if str(row.get("id") or "").strip()}
        missing = [pid for pid in unique_ids if pid not in payout_map]
        if missing:
            raise PayoutValidationError(f"Some payouts were not found: {', '.join(missing)}")

        validation_errors = self._validate_bulk_action(action, payouts, failure_reason)
        if validation_errors:
            raise PayoutValidationError("; ".join(validation_errors))

        successes: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []

        for payout_id in unique_ids:
            try:
                if action == "mark-processing":
                    result = self.mark_processing(
                        payout_id=payout_id,
                        provider_reference=provider_reference,
                        provider_transfer_code=provider_transfer_code,
                        metadata={**(metadata or {}), "bulk": True},
                    )
                elif action == "mark-paid":
                    result = self.mark_paid(
                        payout_id=payout_id,
                        provider_reference=provider_reference,
                        provider_transfer_code=provider_transfer_code,
                        metadata={**(metadata or {}), "bulk": True},
                    )
                else:
                    result = self.mark_failed(
                        payout_id=payout_id,
                        failure_reason=failure_reason or "",
                        provider_reference=provider_reference,
                        provider_transfer_code=provider_transfer_code,
                        metadata={**(metadata or {}), "bulk": True},
                    )
                successes.append(
                    {
                        "payout_id": payout_id,
                        "status": result.payout.get("status"),
                        "reward_count": len(result.updated_reward_ids),
                    }
                )
            except Exception as exc:
                failures.append({"payout_id": payout_id, "error": str(exc)})

        return {
            "action": action,
            "requested_count": len(unique_ids),
            "success_count": len(successes),
            "failure_count": len(failures),
            "successes": successes,
            "failures": failures,
        }

    def _validate_bulk_action(
        self,
        action: str,
        payouts: Sequence[Dict[str, Any]],
        failure_reason: Optional[str],
    ) -> List[str]:
        statuses = {self._normalize_status(row.get("status")) for row in payouts}
        errors: List[str] = []

        if action == "mark-processing":
            if "paid" in statuses:
                errors.append("Bulk mark processing cannot include paid payouts.")
            if not statuses.issubset({"pending", "failed", "unknown"}):
                errors.append("Bulk mark processing only supports pending or failed payouts.")
        elif action == "mark-paid":
            if not statuses.issubset({"processing", "unknown"}):
                errors.append("Bulk mark paid only supports processing payouts.")
        elif action == "mark-failed":
            if "paid" in statuses:
                errors.append("Bulk mark failed cannot include paid payouts.")
            if not (failure_reason or "").strip():
                errors.append("Failure reason is required for bulk mark failed.")

        return errors

    def _get_payout_rows(self, payout_ids: Sequence[str]) -> List[Dict[str, Any]]:
        response = (
            self.supabase.table("referral_payouts")
            .select("*")
            .in_("id", list(payout_ids))
            .execute()
        )
        return response.data or []

    def _get_payable_rewards(self, account_id: str) -> List[Dict[str, Any]]:
        response = (
            self.supabase.table("referral_rewards")
            .select("*")
            .eq("account_id", account_id)
            .eq("status", approved_reward_status())
            .order("approved_at", desc=False)
            .execute()
        )
        return response.data or []

    def _mark_rewards_paid(self, account_id: str, paid_at: str) -> None:
        self.supabase.table("referral_rewards").update(
            {"status": "paid", "paid_at": paid_at, "updated_at": self._now_iso()}
        ).eq("account_id", account_id).eq("status", approved_reward_status()).execute()

    def _update_payout_row(self, payout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = (
            self.supabase.table("referral_payouts")
            .update(payload)
            .eq("id", payout_id)
            .select("*")
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise PayoutNotFoundError(f"Payout {payout_id} was not found after update.")
        return rows[0]

    def _log_audit(
        self,
        payout_id: str,
        account_id: str,
        action: str,
        old_status: str,
        new_status: str,
        provider_reference: Optional[str],
        provider_transfer_code: Optional[str],
        failure_reason: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        payload = {
            "payout_id": payout_id,
            "account_id": account_id,
            "action": action,
            "old_status": old_status,
            "new_status": new_status,
            "provider_reference": provider_reference,
            "provider_transfer_code": provider_transfer_code,
            "failure_reason": failure_reason,
            "metadata": metadata or {},
            "created_at": self._now_iso(),
        }
        self.supabase.table("referral_payout_audit_logs").insert(payload).execute()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _normalize_status(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text else "unknown"

    def _coalesce(self, incoming: Optional[str], existing: Any) -> Optional[str]:
        incoming_text = (incoming or "").strip()
        if incoming_text:
            return incoming_text
        existing_text = str(existing or "").strip()
        return existing_text or None

    def _to_decimal(self, value: Any) -> Decimal:
        return _to_decimal(value)


# ---------------------------------------------------------------------------
# Shared helpers / compatibility layer
# ---------------------------------------------------------------------------


def _svc() -> PayoutService:
    return PayoutService(get_supabase_client(admin=True))


def _sb() -> Client:
    return get_supabase_client(admin=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _normalize_currency(value: Optional[str]) -> str:
    text = str(value or payout_currency()).strip().upper()
    return text or payout_currency()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def approved_reward_status() -> str:
    return str(os.getenv("REFERRAL_REWARD_MATURE_STATUS") or "approved").strip().lower() or "approved"


def payout_enabled() -> bool:
    raw = os.getenv("REFERRAL_PAYOUT_ENABLED")
    if raw is None:
        raw = os.getenv("PAYOUT_ENABLED", "1")
    return _truthy(raw)


def payout_provider() -> str:
    return str(os.getenv("REFERRAL_PAYOUT_PROVIDER") or os.getenv("PAYOUT_PROVIDER") or "manual").strip().lower() or "manual"


def payout_currency() -> str:
    return str(os.getenv("REFERRAL_REWARD_CURRENCY") or os.getenv("REFERRAL_PAYOUT_CURRENCY") or "NGN").strip().upper() or "NGN"


def min_payout_amount() -> Decimal:
    raw = os.getenv("REFERRAL_MIN_PAYOUT_AMOUNT") or os.getenv("REFERRAL_PAYOUT_MINIMUM") or "0"
    amount = _to_decimal(raw, Decimal("0"))
    return amount if amount >= Decimal("0") else Decimal("0")


def list_payout_queue(statuses: Sequence[str], limit: int = 200) -> List[Dict[str, Any]]:
    return _svc().get_queue(statuses=statuses, limit=limit)


def get_payout_row(payout_id: str) -> Dict[str, Any]:
    return _svc().get_payout(payout_id)


def list_payout_audit_logs(payout_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    return _svc().get_audit_history(payout_id=payout_id, limit=limit)


def admin_mark_payout_processing(
    payout_id: str,
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = _svc().mark_processing(
        payout_id=payout_id,
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        metadata=metadata,
    )
    return {
        "ok": True,
        "payout": result.payout,
        "updated_reward_ids": result.updated_reward_ids,
        "audit_logged": result.audit_logged,
    }


def admin_mark_payout_paid(
    payout_id: str,
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = _svc().mark_paid(
        payout_id=payout_id,
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        metadata=metadata,
    )
    return {
        "ok": True,
        "payout": result.payout,
        "updated_reward_ids": result.updated_reward_ids,
        "audit_logged": result.audit_logged,
    }


def admin_mark_payout_failed(
    payout_id: str,
    failure_reason: str,
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = _svc().mark_failed(
        payout_id=payout_id,
        failure_reason=failure_reason,
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        metadata=metadata,
    )
    return {
        "ok": True,
        "payout": result.payout,
        "updated_reward_ids": result.updated_reward_ids,
        "audit_logged": result.audit_logged,
    }


def admin_bulk_update_payouts(
    action: str,
    payout_ids: Sequence[str],
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    failure_reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result = _svc().bulk_update(
        action=action,
        payout_ids=payout_ids,
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        failure_reason=failure_reason,
        metadata=metadata,
    )
    return {"ok": True, **result}


def get_payout_account(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    sb = _sb()
    response = (
        sb.table("referral_payout_accounts")
        .select("*")
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def upsert_payout_account(
    account_id: str,
    bank_name: Optional[str] = None,
    account_name: Optional[str] = None,
    account_number: Optional[str] = None,
    currency: Optional[str] = None,
    provider: Optional[str] = None,
    recipient_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    bank_code: Optional[str] = None,
    account_number_masked: Optional[str] = None,
    is_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise PayoutValidationError("Account ID is required.")

    normalized_provider = str(provider or payout_provider()).strip().lower() or payout_provider()
    normalized_currency = _normalize_currency(currency)
    now_iso = _now_iso()

    clean_bank_name = (bank_name or "").strip() or None
    clean_bank_code = (bank_code or "").strip() or None
    clean_account_name = (account_name or "").strip() or None
    clean_account_number = (account_number or "").strip() or None
    clean_account_number_masked = (account_number_masked or "").strip() or None
    clean_recipient_code = (recipient_code or "").strip() or None

    if not clean_account_number_masked and clean_account_number:
        digits_only = "".join(ch for ch in clean_account_number if ch.isdigit())
        if len(digits_only) >= 4:
            clean_account_number_masked = f"****{digits_only[-4:]}"
        elif digits_only:
            clean_account_number_masked = digits_only

    payload = {
        "account_id": account_id,
        "provider": normalized_provider,
        "bank_code": clean_bank_code,
        "bank_name": clean_bank_name,
        "account_name": clean_account_name,
        "account_number": clean_account_number,
        "account_number_masked": clean_account_number_masked,
        "recipient_code": clean_recipient_code,
        "currency": normalized_currency,
        "is_verified": bool(is_verified),
        "metadata": metadata or {},
        "updated_at": now_iso,
    }

    existing = get_payout_account(account_id)
    sb = _sb()

    if existing and existing.get("id"):
        response = (
            sb.table("referral_payout_accounts")
            .update(payload)
            .eq("id", existing["id"])
            .select("*")
            .limit(1)
            .execute()
        )
    else:
        payload["created_at"] = now_iso
        response = (
            sb.table("referral_payout_accounts")
            .insert(payload)
            .select("*")
            .limit(1)
            .execute()
        )

    rows = response.data or []
    return rows[0] if rows else {**payload, "id": existing.get("id") if existing else None}


def approved_balance_for_account(account_id: str) -> Decimal:
    account_id = str(account_id or "").strip()
    if not account_id:
        return Decimal("0")

    sb = _sb()
    approved_rows = (
        sb.table("referral_rewards")
        .select("id,reward_amount,status")
        .eq("account_id", account_id)
        .eq("status", approved_reward_status())
        .execute()
    ).data or []
    approved_total = sum((_to_decimal(row.get("reward_amount")) for row in approved_rows), Decimal("0"))

    open_rows = (
        sb.table("referral_payouts")
        .select("id,amount,status")
        .eq("account_id", account_id)
        .in_("status", ["pending", "processing"])
        .execute()
    ).data or []
    open_total = sum((_to_decimal(row.get("amount")) for row in open_rows), Decimal("0"))

    available = approved_total - open_total
    return available if available > Decimal("0") else Decimal("0")


def payout_eligibility(account_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise PayoutValidationError("Account ID is required.")

    sb = _sb()
    payout_account = get_payout_account(account_id)

    approved_rows = (
        sb.table("referral_rewards")
        .select("id,reward_amount,status,approved_at")
        .eq("account_id", account_id)
        .eq("status", approved_reward_status())
        .execute()
    ).data or []

    open_rows = (
        sb.table("referral_payouts")
        .select("id,amount,status,requested_at")
        .eq("account_id", account_id)
        .in_("status", ["pending", "processing"])
        .order("requested_at", desc=True)
        .execute()
    ).data or []

    approved_amount = sum((_to_decimal(row.get("reward_amount")) for row in approved_rows), Decimal("0"))
    open_amount = sum((_to_decimal(row.get("amount")) for row in open_rows), Decimal("0"))
    available_amount = approved_amount - open_amount
    if available_amount < Decimal("0"):
        available_amount = Decimal("0")

    minimum = min_payout_amount()
    meets_minimum = available_amount >= minimum if minimum > Decimal("0") else available_amount > Decimal("0")

    return {
        "ok": True,
        "account_id": account_id,
        "payout_account": payout_account,
        "has_payout_account": bool(payout_account),
        "approved_reward_count": len(approved_rows),
        "approved_reward_amount": float(approved_amount),
        "open_payout_count": len(open_rows),
        "open_payout_amount": float(open_amount),
        "available_amount": float(available_amount),
        "minimum_amount": float(minimum),
        "eligible": bool(payout_account) and meets_minimum,
        "minimum_reached": meets_minimum,
        "requires_verified_account": True,
        "is_verified": bool((payout_account or {}).get("is_verified")),
    }


def get_pending_or_processing_payout(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None

    response = (
        _sb().table("referral_payouts")
        .select("*")
        .eq("account_id", account_id)
        .in_("status", ["pending", "processing"])
        .order("requested_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def create_payout_row(
    account_id: str,
    amount: Decimal | float | int | str,
    currency: Optional[str] = None,
    provider: Optional[str] = None,
    status: str = "pending",
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise PayoutValidationError("Account ID is required.")

    amount_decimal = _to_decimal(amount)
    if amount_decimal <= Decimal("0"):
        raise PayoutValidationError("Payout amount must be greater than zero.")

    now_iso = _now_iso()
    payload = {
        "account_id": account_id,
        "amount": float(amount_decimal),
        "currency": _normalize_currency(currency),
        "provider": str(provider or payout_provider()).strip().lower() or payout_provider(),
        "provider_reference": (provider_reference or "").strip() or None,
        "provider_transfer_code": (provider_transfer_code or "").strip() or None,
        "status": str(status or "pending").strip().lower() or "pending",
        "requested_at": now_iso,
        "processed_at": None,
        "paid_at": None,
        "failed_at": None,
        "failure_reason": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "metadata": metadata or {},
    }

    response = (
        _sb().table("referral_payouts")
        .insert(payload)
        .select("*")
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else payload


def request_payout(
    account_id: str,
    amount: Optional[float] = None,
    provider: Optional[str] = None,
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        raise PayoutValidationError("Account ID is required.")

    if not payout_enabled():
        raise PayoutValidationError("Referral payout is currently disabled.")

    payout_account = get_payout_account(account_id)
    if not payout_account:
        raise PayoutValidationError("A payout account must be configured before requesting payout.")

    if not bool(payout_account.get("is_verified")):
        raise PayoutValidationError("Your payout account must be verified before requesting payout.")

    existing = get_pending_or_processing_payout(account_id)
    if existing:
        raise PayoutValidationError("You already have a pending or processing payout request.")

    available_amount = approved_balance_for_account(account_id)
    if available_amount <= Decimal("0"):
        raise PayoutValidationError("No approved referral balance is available for payout.")

    minimum = min_payout_amount()
    if available_amount < minimum:
        raise PayoutValidationError(
            f"Available payout amount {available_amount:.2f} is below the minimum payout amount {minimum:.2f}."
        )

    if amount is None:
        requested_amount = available_amount
    else:
        requested_amount = _to_decimal(amount)

    if requested_amount <= Decimal("0"):
        raise PayoutValidationError("Payout amount must be greater than zero.")

    if requested_amount > available_amount:
        raise PayoutValidationError(
            f"Requested payout amount {requested_amount:.2f} exceeds available amount {available_amount:.2f}."
        )

    if minimum > Decimal("0") and requested_amount < minimum:
        raise PayoutValidationError(
            f"Requested payout amount {requested_amount:.2f} is below the minimum payout amount {minimum:.2f}."
        )

    payout = create_payout_row(
        account_id=account_id,
        amount=requested_amount,
        currency=payout_currency(),
        provider=provider or payout_provider(),
        status="pending",
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        metadata={
            **(metadata or {}),
            "source": (metadata or {}).get("source") or "user_request",
        },
    )

    return {
        "ok": True,
        "payout": payout,
        "payout_account": payout_account,
        "eligibility": payout_eligibility(account_id),
    }
