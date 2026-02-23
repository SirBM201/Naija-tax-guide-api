# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List

from ..core.supabase_client import supabase


_PLAN_DAYS: Dict[str, int] = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _norm_plan(plan_code: Optional[str]) -> str:
    return (plan_code or "").strip().lower()


def _duration_days(plan_code: str) -> int:
    return _PLAN_DAYS.get(plan_code, 30)


def _rootcause(where: str, e: Exception, *, hint: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"where": where, "type": type(e).__name__, "message": str(e)}
    if hint:
        out["hint"] = hint
    if extra:
        out["extra"] = extra
    return out


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **data}


def _fail(
    error: str,
    *,
    where: str,
    e: Optional[Exception] = None,
    hint: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": False, "error": error, "where": where}
    if e is not None:
        payload["root_cause"] = _rootcause(where, e, hint=hint, extra=extra)
    else:
        payload["root_cause"] = {"where": where, "message": hint or "unknown"}
        if extra:
            payload["root_cause"]["extra"] = extra
    return payload


def _db():
    return supabase()


# -----------------------------------------------------------------------------
# Accounts prerequisite (FK safety)
# -----------------------------------------------------------------------------
def _account_exists(account_id: str) -> Tuple[bool, bool, Optional[Dict[str, Any]]]:
    account_id = (account_id or "").strip()
    try:
        db = _db()
        res = db.table("accounts").select("account_id").eq("account_id", account_id).limit(1).execute()
        rows = getattr(res, "data", None) or []
        return True, bool(rows), None
    except Exception as e:
        return False, False, _rootcause(
            "accounts.select",
            e,
            hint="Failed to read accounts table. Check Supabase credentials and RLS policies.",
            extra={"account_id": account_id},
        )


def _ensure_account_exists(account_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    ok, exists, err = _account_exists(account_id)
    if not ok:
        return False, err
    if not exists:
        return False, {
            "where": "ensure_account_exists",
            "type": "ForeignKeyViolation",
            "message": f"account_id '{account_id}' does not exist in accounts, so user_subscriptions cannot reference it.",
            "hint": "Create/login the account first (OTP flow) so it is inserted into accounts, then retry activation.",
            "extra": {"account_id": account_id, "required_table": "accounts", "fk": "user_subscriptions.account_id -> accounts.account_id"},
        }
    return True, None


# -----------------------------------------------------------------------------
# user_subscriptions helpers
# -----------------------------------------------------------------------------
def _get_user_subscription(account_id: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    account_id = (account_id or "").strip()
    try:
        db = _db()
        res = (
            db.table("user_subscriptions")
            .select("account_id, plan_code, status, expires_at, grace_until, trial_until, created_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
        return True, row, None
    except Exception as e:
        return False, None, _rootcause(
            "user_subscriptions.select",
            e,
            hint="Read failed. Check RLS policy for user_subscriptions and service role key usage.",
            extra={"account_id": account_id},
        )


def _upsert_user_subscription(payload: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        db = _db()
        db.table("user_subscriptions").upsert(payload, on_conflict="account_id").execute()

        ok, row, err = _get_user_subscription(payload.get("account_id") or "")
        if not ok:
            return False, None, err
        return True, row, None
    except Exception as e:
        return False, None, _rootcause(
            "user_subscriptions.upsert",
            e,
            hint="Upsert failed. Common causes: FK missing accounts row, RLS denies, or wrong Supabase key.",
            extra={"on_conflict": "account_id", "payload_keys": sorted(list(payload.keys()))},
        )


# -----------------------------------------------------------------------------
# paystack_transactions helper (optional side-effect)
# -----------------------------------------------------------------------------
def _upsert_paystack_transaction(
    *,
    reference: str,
    status: str,
    account_id: Optional[str] = None,
    plan_code: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Best-effort upsert by reference. Requires unique index on paystack_transactions(reference).
    """
    reference = (reference or "").strip()
    if not reference:
        return
    try:
        db = _db()
        db.table("paystack_transactions").upsert(
            {
                "reference": reference,
                "status": (status or "unknown").strip().lower(),
                "account_id": (account_id or "").strip() or None,
                "plan_code": (plan_code or "").strip().lower() or None,
                "raw": raw,
            },
            on_conflict="reference",
        ).execute()
    except Exception:
        return


# -----------------------------------------------------------------------------
# Public service functions
# -----------------------------------------------------------------------------
def activate_subscription_now(
    *,
    account_id: str,
    plan_code: str,
    days: Optional[int] = None,
    status: str = "active",
    provider: Optional[str] = None,
    reference: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Idempotent activation:
      - Upserts user_subscriptions by account_id
      - Optionally upserts paystack_transactions by reference
      - Safe if called multiple times (webhook retry + verify endpoint)
    """
    where = "activate_subscription_now"

    account_id = (account_id or "").strip()
    plan_code = _norm_plan(plan_code)

    if not account_id:
        return _fail("missing_account_id", where=where, hint="account_id is required")
    if not plan_code:
        return _fail("missing_plan_code", where=where, hint="plan_code is required")

    ok_acc, acc_err = _ensure_account_exists(account_id)
    if not ok_acc:
        return {"ok": False, "error": "account_not_found", "where": where, "root_cause": acc_err}

    now = _now_utc()
    dur = int(days) if days is not None else _duration_days(plan_code)
    expires_at = now + timedelta(days=dur)

    payload = {
        "account_id": account_id,
        "plan_code": plan_code,
        "status": (status or "active").strip().lower(),
        "expires_at": _iso(expires_at),
        "grace_until": None,
        "trial_until": None,
        "updated_at": _iso(now),
    }

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {
            "ok": False,
            "error": "db_upsert_failed",
            "where": where,
            "root_cause": err,
            "table": "user_subscriptions",
            "attempted_payload_keys": sorted(list(payload.keys())),
        }

    # Optional: record transaction reference best-effort (do NOT block activation)
    if provider and provider.strip().lower() == "paystack" and reference:
        _upsert_paystack_transaction(
            reference=reference,
            status="success",
            account_id=account_id,
            plan_code=plan_code,
            raw=raw,
        )

    return _ok({"account_id": account_id, "subscription": row, "table": "user_subscriptions"})


def cancel_subscription(*, account_id: str, status: str = "canceled") -> Dict[str, Any]:
    where = "cancel_subscription"
    account_id = (account_id or "").strip()
    if not account_id:
        return _fail("missing_account_id", where=where, hint="account_id is required")

    ok_acc, acc_err = _ensure_account_exists(account_id)
    if not ok_acc:
        return {"ok": False, "error": "account_not_found", "where": where, "root_cause": acc_err}

    now = _now_utc()
    payload = {"account_id": account_id, "status": (status or "canceled").strip().lower(), "updated_at": _iso(now)}

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {"ok": False, "error": "db_upsert_failed", "where": where, "root_cause": err}

    return _ok({"account_id": account_id, "subscription": row})


def set_trial(*, account_id: str, plan_code: str = "trial", trial_days: int = 7) -> Dict[str, Any]:
    where = "set_trial"
    account_id = (account_id or "").strip()
    if not account_id:
        return _fail("missing_account_id", where=where, hint="account_id is required")

    ok_acc, acc_err = _ensure_account_exists(account_id)
    if not ok_acc:
        return {"ok": False, "error": "account_not_found", "where": where, "root_cause": acc_err}

    now = _now_utc()
    trial_until = now + timedelta(days=int(trial_days))

    payload = {
        "account_id": account_id,
        "plan_code": _norm_plan(plan_code) or "trial",
        "status": "active",
        "trial_until": _iso(trial_until),
        "updated_at": _iso(now),
    }

    ok, row, err = _upsert_user_subscription(payload)
    if not ok:
        return {"ok": False, "error": "db_upsert_failed", "where": where, "root_cause": err}

    return _ok({"account_id": account_id, "subscription": row})


def debug_read_subscription(account_id: str) -> Dict[str, Any]:
    where = "debug_read_subscription"
    account_id = (account_id or "").strip()
    if not account_id:
        return _fail("missing_account_id", where=where, hint="account_id is required")

    ok, row, err = _get_user_subscription(account_id)
    if not ok:
        return {"ok": False, "error": "db_read_failed", "where": where, "root_cause": err}

    return _ok({"account_id": account_id, "subscription": row, "table": "user_subscriptions"})


# -----------------------------------------------------------------------------
# Overdue / expiry (Fixes your cron ImportError)
# -----------------------------------------------------------------------------
def expire_overdue_subscriptions(*, limit: int = 500) -> Dict[str, Any]:
    """
    Marks subscriptions as expired when:
      - status in ('active','past_due')
      - expires_at < now()
      - and (grace_until is null OR grace_until < now())

    NOTE: Uses a read-then-update loop for compatibility with the current code style.
    If you prefer, we can convert this to a single SQL RPC function later.
    """
    where = "expire_overdue_subscriptions"
    now = _now_utc().isoformat()

    try:
        db = _db()
        res = (
            db.table("user_subscriptions")
            .select("account_id, status, expires_at, grace_until")
            .in_("status", ["active", "past_due"])
            .lt("expires_at", now)
            .limit(int(limit))
            .execute()
        )
        rows: List[Dict[str, Any]] = getattr(res, "data", None) or []
    except Exception as e:
        return _fail("db_read_failed", where=where, e=e, hint="Failed to scan overdue subscriptions.")

    expired = 0
    failed: List[Dict[str, Any]] = []

    for r in rows:
        account_id = (r.get("account_id") or "").strip()
        if not account_id:
            continue

        # If grace_until exists and is still in future, skip
        grace_until = r.get("grace_until")
        if grace_until:
            try:
                # Compare as string ISO works if Supabase returns ISO; otherwise leave conservative
                if str(grace_until) > now:
                    continue
            except Exception:
                pass

        try:
            db.table("user_subscriptions").update(
                {"status": "expired", "updated_at": now}
            ).eq("account_id", account_id).execute()
            expired += 1
        except Exception as e:
            failed.append({"account_id": account_id, "error": str(e)})

    return _ok({"expired": expired, "scanned": len(rows), "failed": failed})
