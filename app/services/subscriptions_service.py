# app/services/subscriptions_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, List

from app.core.supabase_client import supabase

# ------------------------------------------------------------
# Global-standard subscription rules
# ------------------------------------------------------------
# past_due grace window (typical SaaS: 3–7 days). We'll use 3.
GRACE_DAYS = int((supabase and "3") or "3")

# Fallback plan durations (days) if you do not store duration in DB.
FALLBACK_PLAN_DAYS = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
    "trial": 7,
    "manual": 30,
}


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            v = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sb():
    # supports both "supabase client instance" and "factory"
    return supabase() if callable(supabase) else supabase


def _compute_grace(end_at: Optional[datetime]) -> Optional[datetime]:
    if not end_at:
        return None
    return end_at + timedelta(days=int(GRACE_DAYS))


def _safe_select_one(table: str, select: str, **eq_filters) -> Optional[Dict[str, Any]]:
    """
    Best-effort select 1 row. If table doesn't exist or any error -> None.
    """
    try:
        sb = _sb()
        q = sb.table(table).select(select)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        res = q.limit(1).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    except Exception:
        return None


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Best-effort upsert. If table doesn't exist or any error -> None.
    """
    try:
        sb = _sb()
        if on_conflict:
            res = sb.table(table).upsert(payload, on_conflict=on_conflict).execute()
        else:
            res = sb.table(table).upsert(payload).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else payload
    except Exception:
        return None


# ------------------------------------------------------------
# Core: get latest subscription row (history-preserving)
# Supabase schema (as per your screenshots):
#   subscriptions: id, user_id, plan, status, start_at, end_at, paystack_ref, amount_kobo
# ------------------------------------------------------------
def _latest_subscription_row(user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Returns (row, error_reason, error_detail)
    """
    try:
        sb = _sb()
        res = (
            sb.table("subscriptions")
            .select("id, user_id, plan, status, start_at, end_at, paystack_ref, amount_kobo, created_at, updated_at")
            .eq("user_id", user_id)
            .order("end_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None, "no_subscription", None
        return rows[0], None, None
    except Exception as e:
        return None, "status_lookup_failed", repr(e)


def _derive_access(status: str, end_at: Optional[datetime]) -> Dict[str, Any]:
    """
    Global-standard access evaluation.

    Rules:
      - active, trial => active access while end_at in future
      - cancelled => active access until end_at (paid already)
      - past_due => active access ONLY within grace (end_at + GRACE_DAYS)
      - expired/other => not active
    """
    now = _now_utc()
    status_norm = (status or "").strip().lower()
    grace_until = _compute_grace(end_at)

    within_period = bool(end_at and end_at > now)
    within_grace = bool(grace_until and grace_until > now)

    active = False
    reason = None

    if status_norm in ("active", "trial"):
        active = within_period
        if not active:
            reason = "expired_period"

    elif status_norm == "cancelled":
        active = within_period
        if not active:
            reason = "cancelled_and_expired"

    elif status_norm == "past_due":
        active = within_grace
        if not active:
            reason = "past_due_out_of_grace"

    elif status_norm == "expired":
        active = False
        reason = "expired"

    else:
        active = False
        reason = status_norm or "unknown_status"

    return {
        "active": active,
        "state": status_norm or "none",
        "reason": None if active else reason,
        "grace_until": grace_until,
    }


# ------------------------------------------------------------
# Public: Subscription Status (this is what /web/ask gate uses)
# NOTE: In your system, g.account_id is the UUID of accounts.account_id.
# We map that UUID directly to subscriptions.user_id (your Supabase schema).
# ------------------------------------------------------------
def get_subscription_status(
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a stable frontend-friendly shape:
      {
        account_id,
        active,
        expires_at,        # ISO str (maps to end_at)
        grace_until,       # computed
        plan_code,         # maps to plan
        reason,
        state,
        debug
      }

    We ignore provider/provider_user_id here because your web auth already
    produces account_id and your subscriptions table uses user_id uuid.
    """
    resolved = (account_id or "").strip() or None

    out: Dict[str, Any] = {
        "account_id": resolved,
        "active": False,
        "expires_at": None,
        "grace_until": None,
        "plan_code": None,
        "reason": "none",
        "state": "none",
        "debug": {"stage": "subscription_checked"},
    }

    if not resolved:
        out["reason"] = "no_account"
        return out

    row, err, detail = _latest_subscription_row(resolved)
    if not row:
        out["reason"] = err or "no_subscription"
        out["debug"] = {"stage": "subscription_checked", "error": detail}
        return out

    plan = row.get("plan")
    status = row.get("status")
    end_at = _parse_iso(row.get("end_at"))

    derived = _derive_access(status, end_at)

    out["plan_code"] = plan
    out["expires_at"] = _iso(end_at)
    out["grace_until"] = _iso(derived.get("grace_until"))
    out["active"] = bool(derived.get("active"))
    out["state"] = derived.get("state") or "none"
    out["reason"] = derived.get("reason")

    out["debug"] = {
        "stage": "subscription_checked",
        "row_id": row.get("id"),
        "raw_plan": plan,
        "raw_status": status,
        "raw_end_at": row.get("end_at"),
    }
    return out


# ------------------------------------------------------------
# Activation / Admin / Testing
# Inserts NEW ROW (history-preserving). Global-standard.
# ------------------------------------------------------------
def _get_plan_days(plan_code: str) -> int:
    """
    Optional: if you have a plans table with duration_days, we can use it.
    Otherwise fallback.
    """
    p = (plan_code or "").strip().lower() or "manual"

    # Best-effort: try plans table
    try:
        sb = _sb()
        res = (
            sb.table("plans")
            .select("plan_code, duration_days, active")
            .eq("plan_code", p)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            r = rows[0] or {}
            if r.get("active") is False:
                return FALLBACK_PLAN_DAYS.get(p, 30)
            dd = r.get("duration_days")
            if isinstance(dd, int) and dd > 0:
                return dd
            if isinstance(dd, str) and dd.strip().isdigit():
                return int(dd.strip())
    except Exception:
        pass

    return FALLBACK_PLAN_DAYS.get(p, 30)


def activate_subscription_now(
    account_id: str,
    plan_code: str,
    status: str = "active",
    duration_days: Optional[int] = None,
    expires_at: Optional[str] = None,
    paystack_ref: Optional[str] = None,
    amount_kobo: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Creates a new subscription row in public.subscriptions (history-preserving).

    Maps:
      account_id -> subscriptions.user_id
      plan_code  -> subscriptions.plan
      expires_at -> subscriptions.end_at
    """
    user_id = (account_id or "").strip()
    if not user_id:
        return {"ok": False, "error": "no_account_id"}

    pcode = (plan_code or "").strip().lower() or "manual"
    st = (status or "").strip().lower() or "active"
    now = _now_utc()

    if expires_at:
        end_dt = _parse_iso(expires_at)
        if not end_dt:
            return {"ok": False, "error": "invalid_expires_at"}
    else:
        days = duration_days if isinstance(duration_days, int) and duration_days > 0 else _get_plan_days(pcode)
        end_dt = now + timedelta(days=int(days))

    payload = {
        "user_id": user_id,
        "plan": pcode,
        "status": st,
        "start_at": _iso(now),
        "end_at": _iso(end_dt),
        "paystack_ref": paystack_ref,
        "amount_kobo": amount_kobo,
    }

    try:
        sb = _sb()
        res = sb.table("subscriptions").insert(payload).execute()
        rows = getattr(res, "data", None) or []
        inserted = rows[0] if rows else payload
        return {"ok": True, "inserted": inserted, "subscription": get_subscription_status(account_id=user_id)}
    except Exception as e:
        return {"ok": False, "error": "insert_failed", "detail": repr(e)}


def manual_activate_subscription(
    account_id: str,
    plan_code: str = "manual",
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Backwards compat helper: create manual subscription row.
    """
    return activate_subscription_now(
        account_id=account_id,
        plan_code=plan_code,
        status="active",
        duration_days=None,
        expires_at=expires_at,
    )


def start_trial_if_eligible(account_id: str, trial_plan_code: str = "trial") -> Dict[str, Any]:
    """
    Starts a trial only if user has no subscription rows yet.
    """
    user_id = (account_id or "").strip()
    if not user_id:
        return {"ok": False, "error": "no_account_id"}

    # If any subscription exists, not eligible
    existing, err, detail = _latest_subscription_row(user_id)
    if existing:
        return {"ok": False, "error": "trial_not_eligible", "reason": "already_has_subscription"}

    res = activate_subscription_now(
        account_id=user_id,
        plan_code=(trial_plan_code or "trial").strip().lower(),
        status="trial",
        duration_days=_get_plan_days("trial"),
    )
    if res.get("ok"):
        return {"ok": True, "subscription": res.get("subscription"), "inserted": res.get("inserted")}
    return {"ok": False, "error": "trial_failed", "detail": res.get("detail")}


# ------------------------------------------------------------
# Cron: Expire overdue subscriptions
# Your codebase imports this from cron routes.
# Must exist to avoid boot crash.
# ------------------------------------------------------------
def expire_overdue_subscriptions(limit: int = 200) -> Dict[str, Any]:
    """
    Best-effort:
      - Finds subscriptions where end_at < now AND status in active/trial/past_due/cancelled
      - Marks them expired by updating that row's status to 'expired'
    NOTE:
      Since you use history rows, we update the row itself (not one-row-per-user).
    """
    sb = _sb()
    now = _now_utc()

    try:
        res = (
            sb.table("subscriptions")
            .select("id, user_id, status, end_at")
            .order("end_at", desc=False)
            .limit(limit)
            .execute()
        )
        rows: List[Dict[str, Any]] = getattr(res, "data", None) or []
    except Exception as e:
        return {"ok": False, "error": "subscriptions_select_failed", "detail": repr(e)}

    expired = 0
    checked = 0

    for row in rows:
        checked += 1
        try:
            sid = row.get("id")
            status = (row.get("status") or "").strip().lower()
            end_dt = _parse_iso(row.get("end_at"))

            if not sid or not end_dt:
                continue

            # Only process candidates
            if status not in ("active", "trial", "past_due", "cancelled"):
                continue

            if end_dt >= now:
                continue

            sb.table("subscriptions").update({"status": "expired"}).eq("id", sid).execute()
            expired += 1
        except Exception:
            continue

    return {"ok": True, "expired": expired, "checked": checked, "now": _iso(now)}


# ------------------------------------------------------------
# Webhook Payment Success Handler
# app.routes.webhooks imports this by name.
# Must exist to avoid boot crash.
# ------------------------------------------------------------
def handle_payment_success(
    *,
    reference: Optional[str] = None,
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
    plan_code: Optional[str] = None,
    amount: Optional[int] = None,
    currency: Optional[str] = None,
    paid_at: Optional[str] = None,
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Idempotent-ish payment handler.

    We:
      1) Resolve account_id (required)
      2) Best-effort idempotency via optional 'payments' table (if it exists)
      3) Insert NEW subscription row in 'subscriptions'
      4) Record payment in 'payments' (best-effort) if table exists

    IMPORTANT:
      - Your subscriptions schema uses user_id (uuid), plan, status, start_at, end_at.
    """
    resolved = (account_id or "").strip() or None
    if not resolved:
        # keep shape stable for debugging
        return {"ok": False, "error": "no_account", "reference": reference, "debug": {"provider": provider, "provider_user_id": provider_user_id}}

    pcode = (plan_code or "").strip().lower() or "monthly"

    # Best-effort idempotency
    if reference:
        existing = _safe_select_one("payments", "reference, status, account_id, plan_code, created_at", reference=reference)
        if existing and (existing.get("status") in ("success", "succeeded", "paid")):
            status = get_subscription_status(account_id=resolved)
            return {
                "ok": True,
                "idempotent": True,
                "reference": reference,
                "account_id": resolved,
                "plan_code": status.get("plan_code"),
                "expires_at": status.get("expires_at"),
                "subscription_status": status,
            }

    # Activate subscription by inserting a NEW row (history-preserving)
    act = activate_subscription_now(
        account_id=resolved,
        plan_code=pcode,
        status="active",
        duration_days=None,
        expires_at=None,
        paystack_ref=reference,
        amount_kobo=amount,
    )

    # Record payment (best-effort)
    if reference:
        now = _now_utc()
        pay_payload = {
            "reference": reference,
            "account_id": resolved,
            "plan_code": pcode,
            "amount": amount,
            "currency": currency or "NGN",
            "status": "success",
            "paid_at": paid_at,
            "created_at": _iso(now),
            "raw": raw,
        }
        _safe_upsert("payments", pay_payload, on_conflict="reference")

    if not act.get("ok"):
        return {
            "ok": False,
            "error": "subscription_activation_failed",
            "reference": reference,
            "account_id": resolved,
            "detail": act.get("detail"),
        }

    sub_status = act.get("subscription") or get_subscription_status(account_id=resolved)

    return {
        "ok": True,
        "reference": reference,
        "account_id": resolved,
        "plan_code": sub_status.get("plan_code"),
        "expires_at": sub_status.get("expires_at"),
        "subscription": sub_status,
    }
