from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from supabase import Client


ALLOWED_SINGLE_ACTIONS = {"mark-processing", "mark-paid", "mark-failed"}
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
        query = (
            self.supabase.table("referral_payouts")
            .select("*")
            .in_("status", list(statuses))
            .order("requested_at", desc=True)
            .limit(limit)
        )
        response = query.execute()
        return response.data or []

    def get_payout(self, payout_id: str) -> Dict[str, Any]:
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
        response = (
            self.supabase.table("referral_payout_audit_logs")
            .select("*")
            .eq("payout_id", payout_id)
            .order("created_at", desc=True)
            .limit(limit)
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

        update_payload = {
            "status": "processing",
            "processed_at": self._now_iso(),
            "failed_at": None,
            "failure_reason": None,
            "provider_reference": self._coalesce(provider_reference, payout.get("provider_reference")),
            "provider_transfer_code": self._coalesce(provider_transfer_code, payout.get("provider_transfer_code")),
            "updated_at": self._now_iso(),
        }

        updated = self._update_payout_row(payout_id, update_payload)
        self._log_audit(
            payout_id=payout_id,
            account_id=updated["account_id"],
            action="mark_processing",
            old_status=old_status,
            new_status="processing",
            provider_reference=updated.get("provider_reference"),
            provider_transfer_code=updated.get("provider_transfer_code"),
            failure_reason=None,
            metadata=metadata,
        )
        return PayoutUpdateResult(
            payout=updated,
            updated_reward_ids=[],
            audit_logged=True,
        )

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

        account_id = payout["account_id"]
        amount = self._to_number(payout.get("amount"))
        rewards = self._get_payable_rewards(account_id)

        total_payable = round(sum(self._to_number(row.get("reward_amount")) for row in rewards), 2)
        if total_payable < amount - 0.01:
            raise PayoutValidationError(
                f"Approved rewards total {total_payable:.2f}, which is less than payout amount {amount:.2f}."
            )

        paid_at = self._now_iso()
        updated_reward_ids = [row["id"] for row in rewards if self._to_number(row.get("reward_amount")) > 0]

        update_payload = {
            "status": "paid",
            "paid_at": paid_at,
            "failed_at": None,
            "failure_reason": None,
            "provider_reference": self._coalesce(provider_reference, payout.get("provider_reference")),
            "provider_transfer_code": self._coalesce(provider_transfer_code, payout.get("provider_transfer_code")),
            "updated_at": self._now_iso(),
        }

        updated = self._update_payout_row(payout_id, update_payload)
        self._mark_rewards_paid(account_id=account_id, paid_at=paid_at)

        self._log_audit(
            payout_id=payout_id,
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
        if old_status in {"paid"}:
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

        updated = self._update_payout_row(payout_id, update_payload)
        self._log_audit(
            payout_id=payout_id,
            account_id=updated["account_id"],
            action="mark_failed",
            old_status=old_status,
            new_status="failed",
            provider_reference=updated.get("provider_reference"),
            provider_transfer_code=updated.get("provider_transfer_code"),
            failure_reason=cleaned_reason,
            metadata=metadata,
        )

        return PayoutUpdateResult(
            payout=updated,
            updated_reward_ids=[],
            audit_logged=True,
        )

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

        unique_ids = [item for item in dict.fromkeys([str(x).strip() for x in payout_ids if str(x).strip()])]
        if not unique_ids:
            raise PayoutValidationError("At least one payout ID is required for a bulk action.")

        payouts = self._get_payout_rows(unique_ids)
        payout_map = {row["id"]: row for row in payouts}
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
            .eq("status", "approved")
            .order("approved_at", desc=False)
            .execute()
        )
        return response.data or []

    def _mark_rewards_paid(self, account_id: str, paid_at: str) -> None:
        self.supabase.table("referral_rewards").update(
            {"status": "paid", "paid_at": paid_at, "updated_at": self._now_iso()}
        ).eq("account_id", account_id).eq("status", "approved").execute()

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

    def _to_number(self, value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0


def _svc() -> PayoutService:
    from app.supabase_client import get_supabase_client
    return PayoutService(get_supabase_client())


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
    from app.supabase_client import get_supabase_client

    supabase = get_supabase_client()

    try:
        response = (
            supabase.table("referral_payout_accounts")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if rows:
            return rows[0]
    except Exception:
        pass

    try:
        response = (
            supabase.table("accounts")
            .select("*")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if rows:
            return rows[0]
    except Exception:
        pass

    return None


def payout_eligibility(account_id: str) -> Dict[str, Any]:
    from app.supabase_client import get_supabase_client

    supabase = get_supabase_client()

    payout_account = get_payout_account(account_id)

    approved_rewards = (
        supabase.table("referral_rewards")
        .select("id,reward_amount,status,approved_at")
        .eq("account_id", account_id)
        .eq("status", "approved")
        .execute()
    )
    approved_rows = approved_rewards.data or []
    approved_amount = round(
        sum(float(row.get("reward_amount") or 0) for row in approved_rows), 2
    )

    pending_processing = (
        supabase.table("referral_payouts")
        .select("id,amount,status,requested_at")
        .eq("account_id", account_id)
        .in_("status", ["pending", "processing"])
        .order("requested_at", desc=True)
        .execute()
    )
    pending_rows = pending_processing.data or []
    pending_amount = round(
        sum(float(row.get("amount") or 0) for row in pending_rows), 2
    )

    available_amount = round(max(approved_amount - pending_amount, 0), 2)

    return {
        "ok": True,
        "account_id": account_id,
        "payout_account": payout_account,
        "has_payout_account": bool(payout_account),
        "approved_reward_count": len(approved_rows),
        "approved_reward_amount": approved_amount,
        "open_payout_count": len(pending_rows),
        "open_payout_amount": pending_amount,
        "available_amount": available_amount,
        "eligible": bool(payout_account) and available_amount > 0,
        "minimum_reached": available_amount > 0,
    }


def request_payout(
    account_id: str,
    amount: float,
    provider: Optional[str] = None,
    provider_reference: Optional[str] = None,
    provider_transfer_code: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from app.supabase_client import get_supabase_client

    supabase = get_supabase_client()

    eligibility = payout_eligibility(account_id)
    requested_amount = 0.0
    try:
        requested_amount = float(amount or 0)
    except (TypeError, ValueError):
        requested_amount = 0.0

    if requested_amount <= 0:
        raise PayoutValidationError("Payout amount must be greater than zero.")

    available_amount = float(eligibility.get("available_amount") or 0)
    if requested_amount > available_amount:
        raise PayoutValidationError(
            f"Requested payout amount {requested_amount:.2f} exceeds available amount {available_amount:.2f}."
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "account_id": account_id,
        "amount": requested_amount,
        "currency": "NGN",
        "provider": (provider or "manual").strip() or "manual",
        "provider_reference": (provider_reference or "").strip() or None,
        "provider_transfer_code": (provider_transfer_code or "").strip() or None,
        "status": "pending",
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
        supabase.table("referral_payouts")
        .insert(payload)
        .execute()
    )
    rows = response.data or []
    payout = rows[0] if rows else payload

    return {
        "ok": True,
        "payout": payout,
        "eligibility": eligibility,
    }

