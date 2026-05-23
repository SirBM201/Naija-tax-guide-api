# app/services/credit_usage_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase

CREDIT_USAGE_SERVICE_VERSION = "2026-05-23-v4-paid-ai-no-double-debit-safe"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()



def _lower(value: Any) -> str:
    return _clean(value).lower()


def _clip(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None

    try:
        # Supabase often returns ISO strings with Z.
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _query_rows(
    table: str,
    select_cols: str = "*",
    *,
    limit: int = 20,
    **eq_filters: Any,
) -> Tuple[list[dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        res = q.limit(limit).execute()
        rows = getattr(res, "data", None) or []
        return [r for r in rows if isinstance(r, dict)], None
    except Exception as exc:
        return [], f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_insert(table: str, payload: Dict[str, Any]) -> Optional[str]:
    try:
        _sb().table(table).insert(payload).execute()
        return None
    except Exception as exc:
        return f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: str = "account_id") -> Optional[str]:
    try:
        _sb().table(table).upsert(payload, on_conflict=on_conflict).execute()
        return None
    except Exception as exc:
        return f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_update(table: str, payload: Dict[str, Any], *, account_id: str) -> Optional[str]:
    try:
        _sb().table(table).update(payload).eq("account_id", account_id).execute()
        return None
    except Exception as exc:
        return f"{table}: {type(exc).__name__}: {_clip(exc)}"


def plan_family_from_code(plan_code: Any) -> str:
    code = _lower(plan_code)
    if "business" in code:
        return "business"
    if "professional" in code or "pro" in code:
        return "professional"
    if "starter" in code:
        return "starter"
    return "free"


def is_paid_plan_code(plan_code: Any) -> bool:
    code = _lower(plan_code)
    if not code or code in {"free", "free_forever", "none", "no_plan"}:
        return False
    return plan_family_from_code(code) in {"starter", "professional", "business"}


def get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    account_id = _clean(account_id)
    if not account_id:
        return None

    # Current backend uses user_subscriptions.account_id.
    rows, _err = _query_rows("user_subscriptions", "*", limit=1, account_id=account_id)
    if rows:
        return rows[0]

    # Optional compatibility for older schemas. Errors are intentionally ignored.
    rows, _err = _query_rows("user_subscriptions", "*", limit=1, app_user_id=account_id)
    if rows:
        return rows[0]

    return None


def subscription_is_active(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False

    status = _lower(row.get("status") or row.get("subscription_status") or "")
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled", "paused", "failed"}:
        return False

    explicit = row.get("is_active")
    if explicit is not None and str(explicit).strip().lower() in {"false", "0", "no", "off"}:
        return False

    # If there is an expiry date, it must not be in the past.
    expiry_fields = (
        "expires_at",
        "current_period_end",
        "valid_until",
        "ends_at",
        "period_end",
    )
    for field in expiry_fields:
        dt = _parse_datetime(row.get(field))
        if dt is not None:
            return dt >= datetime.now(timezone.utc)

    # If no expiry date is present, accept known active statuses.
    return status in {"active", "trial", "trialing", "grace", "past_due"}


def get_effective_plan(account_id: str) -> Dict[str, Any]:
    sub = get_subscription(account_id)
    plan_code = _lower(
        (sub or {}).get("plan_code")
        or (sub or {}).get("plan")
        or (sub or {}).get("tier")
        or "free"
    )
    family = plan_family_from_code(plan_code)
    active = subscription_is_active(sub)

    return {
        "subscription": sub,
        "plan_code": plan_code,
        "plan_family": family,
        "active": active,
        "is_paid": bool(active and is_paid_plan_code(plan_code)),
    }


def _detect_credit_column(row: Dict[str, Any]) -> str:
    for col in ("balance", "credits", "credit_balance"):
        if col in row:
            return col
    return "balance"


def get_credit_balance(account_id: str) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "balance": 0, "error": "account_id_required"}

    rows, err = _query_rows("ai_credit_balances", "*", limit=1, account_id=account_id)
    if rows:
        row = rows[0]
        col = _detect_credit_column(row)
        balance = _as_int(row.get(col), 0)
        return {
            "ok": True,
            "account_id": account_id,
            "table": "ai_credit_balances",
            "row": row,
            "column": col,
            "balance": balance,
            "updated_at": row.get("updated_at"),
        }

    # Missing row is not a hard error. A paid user can have 0 credits.
    return {
        "ok": True,
        "account_id": account_id,
        "table": "ai_credit_balances",
        "row": None,
        "column": "balance",
        "balance": 0,
        "updated_at": None,
        "lookup_error": err,
    }


def set_credit_balance(account_id: str, new_balance: int) -> Dict[str, Any]:
    account_id = _clean(account_id)
    if not account_id:
        return {"ok": False, "error": "account_id_required"}

    balance = max(0, int(new_balance or 0))
    now_iso = _now_iso()
    current = get_credit_balance(account_id)
    col = _clean(current.get("column") or "balance")

    payload = {
        col: balance,
        "updated_at": now_iso,
    }

    if current.get("row"):
        err = _safe_update("ai_credit_balances", payload, account_id=account_id)
    else:
        err = _safe_upsert(
            "ai_credit_balances",
            {"account_id": account_id, "balance": balance, "updated_at": now_iso},
            on_conflict="account_id",
        )

    if not err:
        return {
            "ok": True,
            "account_id": account_id,
            "table": "ai_credit_balances",
            "column": col,
            "balance": balance,
            "updated_at": now_iso,
        }

    return {
        "ok": False,
        "account_id": account_id,
        "error": "credit_balance_write_failed",
        "root_cause": err,
    }


def log_credit_activity(
    *,
    account_id: str,
    action_code: str,
    description: str,
    channel: str = "web",
    credits_delta: int = 0,
    balance_after: Optional[int] = None,
    reference: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "account_id": _clean(account_id),
        "reference": _clean(reference) or None,
        "action_code": _clean(action_code) or "credit_activity",
        "description": _clean(description),
        "channel": _lower(channel) or "web",
        "credits_delta": int(credits_delta or 0),
        "balance_after": balance_after,
        "metadata": metadata or {},
        "created_at": _now_iso(),
    }

    errors: list[str] = []
    for table in ("credit_usage_logs", "credit_transactions", "ai_credit_transactions"):
        err = _safe_insert(table, payload)
        if not err:
            return {"ok": True, "table": table, "payload": payload}
        errors.append(err)

    # Logging failure must never block answer delivery after a valid deduction.
    return {
        "ok": False,
        "error": "credit_activity_log_failed",
        "errors": errors[:4],
        "payload": payload,
    }


@dataclass(frozen=True)
class CreditRule:
    action_code: str
    display_name: str
    credit_cost: int
    requires_paid_plan: bool
    free_if_cached_or_library: bool = True


DEFAULT_RULES: Dict[str, CreditRule] = {
    "ai_tax_answer": CreditRule(
        action_code="ai_tax_answer",
        display_name="AI tax answer",
        credit_cost=1,
        requires_paid_plan=True,
    ),
    "ai_quiz_explanation": CreditRule(
        action_code="ai_quiz_explanation",
        display_name="AI quiz explanation",
        credit_cost=1,
        requires_paid_plan=True,
    ),
    # This is intentionally 0 because WhatsApp Q5 v17 already pre-debits manually.
    "quiz_ai_explanation_q5_manual_credit": CreditRule(
        action_code="quiz_ai_explanation_q5_manual_credit",
        display_name="WhatsApp Q5 manual AI explanation",
        credit_cost=0,
        requires_paid_plan=True,
    ),
    "advanced_explanation": CreditRule(
        action_code="advanced_explanation",
        display_name="Advanced AI explanation",
        credit_cost=2,
        requires_paid_plan=True,
    ),
    "filing_checklist": CreditRule(
        action_code="filing_checklist",
        display_name="Filing checklist",
        credit_cost=2,
        requires_paid_plan=True,
    ),
    "document_draft": CreditRule(
        action_code="document_draft",
        display_name="Document draft",
        credit_cost=3,
        requires_paid_plan=True,
    ),
    "document_summary": CreditRule(
        action_code="document_summary",
        display_name="Document summary",
        credit_cost=3,
        requires_paid_plan=True,
    ),
    "document_review": CreditRule(
        action_code="document_review",
        display_name="Document review",
        credit_cost=5,
        requires_paid_plan=True,
    ),
}


def get_credit_rule(action_code: str) -> CreditRule:
    code = _lower(action_code) or "ai_tax_answer"
    return DEFAULT_RULES.get(code) or DEFAULT_RULES["ai_tax_answer"]


def is_manual_precharged_action(action_code: Any) -> bool:
    code = _lower(action_code)
    return code in {"quiz_ai_explanation_q5_manual_credit"} or "manual_credit" in code or "precharged" in code


def check_credit_access(
    *,
    account_id: str,
    action_code: str = "ai_tax_answer",
    source_kind: str = "ai",
    channel: str = "web",
    already_charged: bool = False,
) -> Dict[str, Any]:
    """
    Decision-only check. Does not deduct.

    Free source kinds never deduct. AI source requires an active paid plan. For
    normal AI, at least the rule cost must be available. For manual-precharged
    actions such as WhatsApp Q5, the caller has already debited the balance, so
    this function checks the paid plan only and returns charge=False.
    """
    account_id = _clean(account_id)
    action_code = _lower(action_code) or "ai_tax_answer"
    source_kind = _lower(source_kind) or "ai"
    channel = _lower(channel) or "web"
    rule = get_credit_rule(action_code)
    manual_precharged = bool(already_charged or is_manual_precharged_action(action_code))

    if source_kind in {"cache", "library", "database", "db", "basic_calculator", "calculator", "non_ai_quiz"}:
        return {
            "ok": True,
            "allowed": True,
            "charge": False,
            "credit_cost": 0,
            "reason": "free_source_kind",
            "source_kind": source_kind,
            "action_code": action_code,
            "channel": channel,
        }

    plan = get_effective_plan(account_id)
    balance_info = get_credit_balance(account_id)
    balance = int(balance_info.get("balance") or 0)

    if rule.requires_paid_plan and not plan["is_paid"]:
        return {
            "ok": False,
            "allowed": False,
            "charge": False,
            "error": "paid_plan_required",
            "message": "This action requires an active paid plan.",
            "reason": "paid_plan_required",
            "source_kind": source_kind,
            "action_code": action_code,
            "credit_cost": rule.credit_cost,
            "balance": balance,
            "plan_code": plan["plan_code"],
            "plan_family": plan["plan_family"],
            "active": plan["active"],
            "channel": channel,
        }

    if manual_precharged:
        return {
            "ok": True,
            "allowed": True,
            "charge": False,
            "manual_precharged": True,
            "reason": "already_charged_by_caller",
            "source_kind": source_kind,
            "action_code": action_code,
            "credit_cost": 0,
            "balance": balance,
            "plan_code": plan["plan_code"],
            "plan_family": plan["plan_family"],
            "active": plan["active"],
            "channel": channel,
        }

    if balance < rule.credit_cost:
        return {
            "ok": False,
            "allowed": False,
            "charge": False,
            "error": "insufficient_credits",
            "message": "Your Usage Credits are not enough for this action.",
            "reason": "insufficient_credits",
            "source_kind": source_kind,
            "action_code": action_code,
            "credit_cost": rule.credit_cost,
            "balance": balance,
            "plan_code": plan["plan_code"],
            "plan_family": plan["plan_family"],
            "active": plan["active"],
            "channel": channel,
        }

    return {
        "ok": True,
        "allowed": True,
        "charge": bool(rule.credit_cost > 0),
        "reason": "credit_charge_required" if rule.credit_cost > 0 else "no_cost_action",
        "source_kind": source_kind,
        "action_code": action_code,
        "credit_cost": rule.credit_cost,
        "balance": balance,
        "plan_code": plan["plan_code"],
        "plan_family": plan["plan_family"],
        "active": plan["active"],
        "channel": channel,
    }


def deduct_credits(
    *,
    account_id: str,
    action_code: str = "ai_tax_answer",
    source_kind: str = "ai",
    channel: str = "web",
    description: Optional[str] = None,
    reference: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    already_charged: bool = False,
) -> Dict[str, Any]:
    decision = check_credit_access(
        account_id=account_id,
        action_code=action_code,
        source_kind=source_kind,
        channel=channel,
        already_charged=already_charged,
    )

    if not decision.get("allowed"):
        return decision

    if not decision.get("charge"):
        return {
            **decision,
            "deducted": False,
            "credits_deducted": 0,
            "balance_after": decision.get("balance"),
        }

    cost = int(decision.get("credit_cost") or 0)
    before = int(decision.get("balance") or 0)
    after = max(0, before - cost)

    write_result = set_credit_balance(account_id, after)
    if not write_result.get("ok"):
        return {
            **decision,
            "ok": False,
            "allowed": False,
            "error": "credit_deduction_failed",
            "message": "Credit deduction failed before the action could be completed.",
            "write_result": write_result,
            "balance": before,
        }

    rule = get_credit_rule(action_code)
    log_result = log_credit_activity(
        account_id=account_id,
        action_code=action_code,
        description=description or f"{rule.display_name}: -{cost} Usage Credit(s)",
        channel=channel,
        credits_delta=-cost,
        balance_after=after,
        reference=reference,
        metadata={
            **(metadata or {}),
            "source_kind": source_kind,
            "credit_cost": cost,
            "balance_before": before,
            "balance_after": after,
        },
    )

    return {
        **decision,
        "ok": True,
        "allowed": True,
        "deducted": True,
        "credits_deducted": cost,
        "balance_before": before,
        "balance_after": after,
        "log_result": log_result,
    }


def refund_credits(
    *,
    account_id: str,
    credits: int,
    action_code: str = "credit_refund",
    channel: str = "web",
    description: str = "Credit refund",
    reference: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    amount = int(credits or 0)
    if amount <= 0:
        return {"ok": False, "error": "invalid_refund_amount"}

    before = int(get_credit_balance(account_id).get("balance") or 0)
    after = before + amount
    write_result = set_credit_balance(account_id, after)

    if not write_result.get("ok"):
        return {
            "ok": False,
            "error": "credit_refund_failed",
            "write_result": write_result,
        }

    log_result = log_credit_activity(
        account_id=account_id,
        action_code=action_code,
        description=description,
        channel=channel,
        credits_delta=amount,
        balance_after=after,
        reference=reference,
        metadata={
            **(metadata or {}),
            "balance_before": before,
            "balance_after": after,
        },
    )

    return {
        "ok": True,
        "account_id": account_id,
        "credits_refunded": amount,
        "balance_before": before,
        "balance_after": after,
        "log_result": log_result,
    }
