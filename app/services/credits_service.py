from __future__ import annotations

"""
CREDITS SERVICE (CANONICAL + FAILURE EXPOSERS)

Provides BOTH APIs to avoid boot crashes:
- get_credit_balance(account_id) -> int
- check_credit_balance(account_id, cost=1) -> dict ok/root_cause/fix

Adds DAILY LIMIT ENFORCEMENT based on plans.daily_answers_limit.
Standard approach: track per-account per-day usage in a small table.

Canonical identity:
  account_id always means accounts.account_id (NOT accounts.id)

Schema assumed:
  public.ai_credit_balances:
    - account_id (uuid)  PRIMARY/UNIQUE
    - balance (int4)
    - updated_at (timestamptz)

  public.plans:
    - plan_code (text) pk
    - ai_credits_total (int4)
    - daily_answers_limit (int4)

  public.ai_daily_usage:
    - account_id (uuid)
    - day (date)
    - count (int4)
    - updated_at (timestamptz)
    - UNIQUE(account_id, day)
"""

from datetime import datetime, timezone, date
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _utc_day() -> date:
    return _now_utc().date()


def _iso_now() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _clip(s: str, n: int = 240) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


BAL_TABLE = "ai_credit_balances"
BAL_COL_ACCOUNT = "account_id"
BAL_COL_BALANCE = "balance"
BAL_COL_UPDATED = "updated_at"

PLANS_TABLE = "plans"

USAGE_TABLE = "ai_daily_usage"
USAGE_COL_ACCOUNT = "account_id"
USAGE_COL_DAY = "day"
USAGE_COL_COUNT = "count"
USAGE_COL_UPDATED = "updated_at"


# -----------------------------
# Credits
# -----------------------------
def get_credit_balance(account_id: str) -> int:
    """Return current AI credit balance (int). Never throws: returns 0 on any failure."""
    account_id = (account_id or "").strip()
    if not account_id:
        return 0

    try:
        res = (
            _sb()
            .table(BAL_TABLE)
            .select(BAL_COL_BALANCE)
            .eq(BAL_COL_ACCOUNT, account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return 0
        return _as_int(rows[0].get(BAL_COL_BALANCE), 0)
    except Exception:
        return 0


def get_credit_balance_details(account_id: str) -> Dict[str, Any]:
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "account_id was empty",
            "fix": "Pass canonical accounts.account_id into the request/session.",
        }

    try:
        res = (
            _sb()
            .table(BAL_TABLE)
            .select(f"{BAL_COL_BALANCE},{BAL_COL_UPDATED}")
            .eq(BAL_COL_ACCOUNT, account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return {
                "ok": True,
                "exists": False,
                "balance": 0,
                "updated_at": None,
                "account_id": account_id,
            }

        row = rows[0] or {}
        return {
            "ok": True,
            "exists": True,
            "balance": _as_int(row.get(BAL_COL_BALANCE), 0),
            "updated_at": row.get(BAL_COL_UPDATED),
            "account_id": account_id,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "credits_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": f"Verify {BAL_TABLE} exists, RLS allows read, and account_id is correct.",
            "details": {"table": BAL_TABLE, "account_id": account_id},
        }


def check_credit_balance(account_id: str, cost: int = 1) -> Dict[str, Any]:
    """Boot-safe, debuggable credit pre-check."""
    account_id = (account_id or "").strip()
    cost = _as_int(cost, 1)
    if cost < 1:
        cost = 1

    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "account_id was empty",
            "fix": "Pass canonical accounts.account_id into the request/session.",
        }

    details = get_credit_balance_details(account_id)
    if not details.get("ok"):
        return details

    balance = _as_int(details.get("balance"), 0)
    remaining = balance - cost
    if remaining < 0:
        return {
            "ok": False,
            "error": "insufficient_credits",
            "balance": balance,
            "cost": cost,
            "remaining": 0,
            "exists": bool(details.get("exists")),
            "root_cause": "AI credits are below required cost for this request.",
            "fix": "Top up credits or activate a plan that includes AI credits.",
            "details": {"account_id": account_id, "balance_row_exists": details.get("exists")},
        }

    return {
        "ok": True,
        "exists": bool(details.get("exists")),
        "balance": balance,
        "cost": cost,
        "remaining": remaining,
    }


def _set_credit_balance(account_id: str, new_balance: int) -> None:
    _sb().table(BAL_TABLE).upsert(
        {
            BAL_COL_ACCOUNT: account_id,
            BAL_COL_BALANCE: int(new_balance),
            BAL_COL_UPDATED: _iso_now(),
        },
        on_conflict=BAL_COL_ACCOUNT,
    ).execute()


def init_credits_for_plan(account_id: str, plan_code: str) -> Dict[str, Any]:
    """
    Called after a subscription is activated/changed.
    Overwrites balance to plan's ai_credits_total.
    """
    account_id = (account_id or "").strip()
    plan_code = (plan_code or "").strip().lower()

    if not account_id or not plan_code:
        return {
            "ok": False,
            "error": "missing_account_or_plan",
            "root_cause": "account_id or plan_code empty",
            "fix": "Pass valid account_id and plan_code.",
        }

    try:
        pres = (
            _sb()
            .table(PLANS_TABLE)
            .select("plan_code, ai_credits_total")
            .eq("plan_code", plan_code)
            .limit(1)
            .execute()
        )
        prows = getattr(pres, "data", None) or []
        if not prows:
            return {
                "ok": False,
                "error": "unknown_plan_code",
                "root_cause": f"plans.plan_code not found for '{plan_code}'",
                "fix": "Insert the plan into plans table or pass a valid plan_code.",
            }
        total = _as_int(prows[0].get("ai_credits_total"), 0)
    except Exception as e:
        return {
            "ok": False,
            "error": "plan_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check plans table, RLS, and the ai_credits_total column.",
        }

    try:
        _set_credit_balance(account_id, total)
    except Exception as e:
        return {
            "ok": False,
            "error": "credit_set_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check ai_credit_balances RLS allows upsert/update for service key.",
        }

    return {"ok": True, "account_id": account_id, "plan_code": plan_code, "balance": total}


def consume_credits(account_id: str, cost: int = 1) -> Dict[str, Any]:
    """
    Deduct credits only after successful AI answer generation.
    Uses a read -> overwrite pattern with service-role access.
    """
    account_id = (account_id or "").strip()
    cost = _as_int(cost, 1)
    if cost < 1:
        cost = 1

    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "account_id was empty",
        }

    bal = check_credit_balance(account_id, cost=cost)
    if not bal.get("ok"):
        return bal

    current_balance = _as_int(bal.get("balance"), 0)
    new_balance = current_balance - cost

    try:
        _set_credit_balance(account_id, new_balance)
        return {
            "ok": True,
            "account_id": account_id,
            "cost": cost,
            "balance_before": current_balance,
            "balance_after": new_balance,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "credit_consume_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check ai_credit_balances RLS allows update/upsert for service key.",
            "details": {"table": BAL_TABLE, "account_id": account_id, "cost": cost},
        }


# -----------------------------
# Daily usage
# -----------------------------
def get_daily_usage(account_id: str, day: Optional[date] = None) -> Dict[str, Any]:
    """
    Returns current usage count for UTC day.
    Never throws; returns ok=False with debug on lookup failure.
    """
    account_id = (account_id or "").strip()
    day = day or _utc_day()

    if not account_id:
        return {"ok": False, "error": "account_id_required", "root_cause": "account_id empty"}

    try:
        res = (
            _sb()
            .table(USAGE_TABLE)
            .select(f"{USAGE_COL_COUNT},{USAGE_COL_DAY}")
            .eq(USAGE_COL_ACCOUNT, account_id)
            .eq(USAGE_COL_DAY, str(day))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        count = _as_int((rows[0].get(USAGE_COL_COUNT) if rows else 0), 0)
        return {"ok": True, "account_id": account_id, "day": str(day), "count": count}
    except Exception as e:
        return {
            "ok": False,
            "error": "daily_usage_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": f"Verify {USAGE_TABLE} exists and RLS allows read for service key.",
            "details": {"table": USAGE_TABLE, "account_id": account_id, "day": str(day)},
        }


def increment_daily_usage(account_id: str, inc: int = 1, day: Optional[date] = None) -> Dict[str, Any]:
    """
    Upserts today's usage row and increments count.
    """
    account_id = (account_id or "").strip()
    day = day or _utc_day()
    inc = _as_int(inc, 1)
    if inc < 1:
        inc = 1

    if not account_id:
        return {"ok": False, "error": "account_id_required", "root_cause": "account_id empty"}

    current = get_daily_usage(account_id, day=day)
    if not current.get("ok"):
        return current

    new_count = _as_int(current.get("count"), 0) + inc

    try:
        _sb().table(USAGE_TABLE).upsert(
            {
                USAGE_COL_ACCOUNT: account_id,
                USAGE_COL_DAY: str(day),
                USAGE_COL_COUNT: int(new_count),
                USAGE_COL_UPDATED: _iso_now(),
            },
            on_conflict=f"{USAGE_COL_ACCOUNT},{USAGE_COL_DAY}",
        ).execute()
        return {"ok": True, "account_id": account_id, "day": str(day), "count": new_count}
    except Exception as e:
        return {
            "ok": False,
            "error": "daily_usage_update_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": f"Verify {USAGE_TABLE} exists and RLS allows upsert/update for service key.",
            "details": {"table": USAGE_TABLE, "account_id": account_id, "day": str(day), "new_count": new_count},
        }


def get_plan_limits(plan_code: str) -> Dict[str, Any]:
    plan_code = (plan_code or "").strip().lower()
    if not plan_code:
        return {
            "ok": False,
            "error": "plan_code_required",
            "root_cause": "plan_code empty",
            "fix": "Pass a valid plan_code.",
        }

    try:
        res = (
            _sb()
            .table(PLANS_TABLE)
            .select("plan_code, ai_credits_total, daily_answers_limit, price, duration_days, active")
            .eq("plan_code", plan_code)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return {
                "ok": False,
                "error": "plan_not_found",
                "root_cause": f"plans.plan_code not found: {plan_code}",
                "fix": "Ensure plan exists in plans table.",
            }

        p = rows[0] or {}
        return {
            "ok": True,
            "plan_code": plan_code,
            "ai_credits_total": _as_int(p.get("ai_credits_total"), 0),
            "daily_answers_limit": _as_int(p.get("daily_answers_limit"), 0),
            "active": bool(p.get("active", True)),
            "raw": p,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "plan_limits_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check plans table, RLS, and column names (daily_answers_limit, ai_credits_total).",
        }


def enforce_daily_limit(account_id: str, daily_limit: int) -> Dict[str, Any]:
    """
    Checks if user exceeded daily limit for today (UTC).
    daily_limit <= 0 means no limit.
    """
    daily_limit = _as_int(daily_limit, 0)
    if daily_limit <= 0:
        return {"ok": True, "limited": False, "limit": daily_limit}

    usage = get_daily_usage(account_id)
    if not usage.get("ok"):
        return {
            "ok": False,
            "error": "daily_limit_check_failed",
            "root_cause": usage.get("root_cause") or usage.get("error"),
            "fix": usage.get("fix") or "Fix ai_daily_usage table/RLS.",
            "details": usage.get("details") or {"account_id": account_id},
        }

    count = _as_int(usage.get("count"), 0)
    if count >= daily_limit:
        return {
            "ok": False,
            "error": "daily_limit_reached",
            "root_cause": "daily_answers_limit exceeded",
            "fix": "Upgrade plan or wait until next UTC day.",
            "details": {"account_id": account_id, "day": usage.get("day"), "count": count, "limit": daily_limit},
        }

    return {"ok": True, "limited": False, "details": {"count": count, "limit": daily_limit, "day": usage.get("day")}}


# Backward-compat aliases
def credits_balance(account_id: str) -> int:
    return get_credit_balance(account_id)
