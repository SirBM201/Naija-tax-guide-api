# app/services/subscriptions_service.py
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase

# Use this to prove Koyeb is running the latest file
SUBSCRIPTIONS_SERVICE_VERSION = "2026-02-24T06:00Z-v1"


# ---------------------------
# Small helpers
# ---------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if v == "":
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _plan_days(plan_code: str) -> int:
    return {"monthly": 30, "quarterly": 90, "yearly": 365}.get(plan_code, 30)


def _ok(req_id: str, **kw: Any) -> Dict[str, Any]:
    out = {"ok": True, "request_id": req_id}
    out.update(kw)
    return out


def _fail(req_id: str, error: str, *, where: str, hint: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "error": error,
        "where": where,
        "hint": hint,
        "request_id": req_id,
    }
    if extra:
        out["extra"] = extra
    return out


# ============================================================
# IMPORTANT: This function MUST exist (your route imports it)
# ============================================================
def activate_subscription_now(account_id: str, plan_code: str = "monthly", days: Optional[int] = None) -> Dict[str, Any]:
    """
    Admin activation - permanent design:
    Prefer RPC bms_activate_subscription to bypass PostgREST schema cache.
    Fallback: table upsert.
    """
    req_id = str(uuid.uuid4())

    account_id = (account_id or "").strip()
    plan_code = (plan_code or "monthly").strip().lower()

    if not account_id:
        return _fail(req_id, "missing_account_id", where="activate_subscription_now")

    if plan_code not in {"monthly", "quarterly", "yearly"}:
        return _fail(req_id, "invalid_plan_code", where="activate_subscription_now", extra={"plan_code": plan_code})

    if days is None:
        days = _plan_days(plan_code)
    else:
        try:
            days = int(days)
        except Exception:
            return _fail(req_id, "invalid_days", where="activate_subscription_now", extra={"days": days})

    use_rpc = _env_bool("SUBS_USE_RPC", True)

    # 1) RPC path (preferred)
    if use_rpc:
        try:
            r = supabase.rpc(
                "bms_activate_subscription",
                {"p_account_id": account_id, "p_plan_code": plan_code, "p_days": days},
            ).execute()
            return _ok(req_id, activated=True, method="rpc", result=getattr(r, "data", None))
        except Exception as e:
            # fall through to table upsert, but include rpc error for debugging
            rpc_err = str(e)
        # continue to fallback
    else:
        rpc_err = "SUBS_USE_RPC disabled"

    # 2) Fallback: table upsert
    try:
        end_ts = (_now_utc() + timedelta(days=days)).isoformat()
        payload = {
            "account_id": account_id,
            "plan_code": plan_code,
            "status": "active",
            "current_period_end": end_ts,
            "updated_at": _now_utc().isoformat(),
        }
        res = supabase.table("user_subscriptions").upsert(payload, on_conflict="account_id").execute()
        return _ok(req_id, activated=True, method="table_upsert", row=getattr(res, "data", None), rpc_error=rpc_err)
    except Exception as e:
        msg = str(e)
        hint = (
            "If you see PGRST204 or missing column errors, your PostgREST schema cache/table schema is mismatched. "
            "Permanent fix is RPC bms_activate_subscription + stable user_subscriptions columns."
        )
        return _fail(req_id, "activate_failed", where="activate_subscription_now", hint=hint, extra={"error": msg, "rpc_error": rpc_err})


def get_subscription_status(account_id: str) -> Dict[str, Any]:
    req_id = str(uuid.uuid4())
    account_id = (account_id or "").strip()
    if not account_id:
        return _fail(req_id, "missing_account_id", where="get_subscription_status")

    use_rpc = _env_bool("SUBS_USE_RPC", True)

    # Prefer RPC read
    if use_rpc:
        try:
            r = supabase.rpc("bms_read_subscription", {"p_account_id": account_id}).execute()
            row = getattr(r, "data", None)
            is_paid = bool(row and (row.get("status") or "").lower() == "active")
            return _ok(req_id, subscription=row, is_paid=is_paid, method="rpc")
        except Exception:
            pass

    # Fallback read
    try:
        res = (
            supabase.table("user_subscriptions")
            .select("account_id, plan_code, status, current_period_end, created_at, updated_at")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = (getattr(res, "data", None) or [])
        row = rows[0] if rows else None

        is_paid = False
        if row:
            status = (row.get("status") or "").lower()
            if status == "active":
                is_paid = True

            cpe = row.get("current_period_end")
            if cpe:
                try:
                    dt = datetime.fromisoformat(str(cpe).replace("Z", "+00:00"))
                    if dt > _now_utc():
                        is_paid = True
                except Exception:
                    pass

        return _ok(req_id, subscription=row, is_paid=is_paid, method="table")
    except Exception as e:
        return _fail(req_id, "status_read_failed", where="get_subscription_status", extra={"error": str(e)})


def handle_payment_success(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Used by Paystack webhook.
    Expects event: {account_id, plan_code, ...}
    """
    req_id = str(uuid.uuid4())
    try:
        account_id = (event.get("account_id") or "").strip()
        plan_code = (event.get("plan_code") or "").strip().lower()
        if not account_id or not plan_code:
            return _fail(req_id, "missing_metadata", where="handle_payment_success", extra={"seen": {"account_id": account_id, "plan_code": plan_code}})

        days = _plan_days(plan_code)
        activated = activate_subscription_now(account_id, plan_code, days=days)

        if not activated.get("ok"):
            return _fail(req_id, "activation_failed", where="handle_payment_success", extra={"activation": activated})

        return _ok(req_id, handled=True, activation=activated, provider=event.get("provider"), reference=event.get("reference"))
    except Exception as e:
        return _fail(req_id, "webhook_failed", where="handle_payment_success", extra={"error": str(e)})


def debug_expose_subscription_health(account_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Provides SQL recommendations + probes.
    """
    req_id = str(uuid.uuid4())
    probe_id = (account_id or "00000000-0000-0000-0000-000000000000").strip()

    info: Dict[str, Any] = {
        "ok": True,
        "request_id": req_id,
        "service_version": SUBSCRIPTIONS_SERVICE_VERSION,
        "client_ok": True,
        "diagnosis": [],
        "hints": {},
        "rpc_probe": {},
        "table_probe": {},
        "recommended_sql_files": {},
    }

    # RPC probe
    try:
        r = supabase.rpc("bms_read_subscription", {"p_account_id": probe_id}).execute()
        info["rpc_probe"] = {"ok": True, "data": getattr(r, "data", None)}
        info["diagnosis"].append("RPC bms_read_subscription is callable (good).")
    except Exception as e:
        info["rpc_probe"] = {"ok": False, "error": str(e)}
        info["diagnosis"].append("RPC bms_read_subscription is NOT callable (install RPC).")

    # Table probe
    try:
        res = supabase.table("user_subscriptions").select("*").limit(1).execute()
        data = getattr(res, "data", []) or []
        info["table_probe"] = {"ok": True, "sample_count": len(data), "sample_keys": list((data[0] or {}).keys()) if data else []}
        info["diagnosis"].append("Table user_subscriptions is readable via PostgREST (good).")
    except Exception as e:
        msg = str(e)
        info["table_probe"] = {"ok": False, "error": msg}
        info["diagnosis"].append("Table probe failed (permissions/schema cache issue).")

    info["diagnosis"].append("Permanent fix: use RPC bms_activate_subscription for activation; keep table schema stable.")

    info["recommended_sql_files"]["rpc.sql"] = """-- RPC READ (stable)
create or replace function public.bms_read_subscription(p_account_id uuid)
returns jsonb
language sql
stable
as $$
  select to_jsonb(us)
  from public.user_subscriptions us
  where us.account_id = p_account_id
  limit 1;
$$;

-- RPC ACTIVATE (permanent bypass of PostgREST schema cache)
create or replace function public.bms_activate_subscription(
  p_account_id uuid,
  p_plan_code text,
  p_days int
)
returns jsonb
language plpgsql
security definer
as $$
declare
  v_end timestamptz;
  v_row jsonb;
begin
  v_end := now() + make_interval(days => p_days);

  insert into public.user_subscriptions (account_id, plan_code, status, current_period_end, created_at, updated_at)
  values (p_account_id, p_plan_code, 'active', v_end, now(), now())
  on conflict (account_id) do update
    set plan_code = excluded.plan_code,
        status = excluded.status,
        current_period_end = excluded.current_period_end,
        updated_at = now();

  select to_jsonb(us) into v_row
  from public.user_subscriptions us
  where us.account_id = p_account_id
  limit 1;

  return jsonb_build_object(
    'account_id', p_account_id,
    'plan_code', p_plan_code,
    'current_period_end', v_end,
    'row', v_row
  );
end $$;

grant execute on function public.bms_read_subscription(uuid) to anon, authenticated, service_role;
grant execute on function public.bms_activate_subscription(uuid, text, int) to service_role;
"""

    info["recommended_sql_files"]["table_and_trigger.sql"] = """create table if not exists public.user_subscriptions (
  account_id uuid primary key,
  plan_code text not null default 'free',
  status text not null default 'inactive',
  current_period_end timestamptz null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_user_subscriptions_status on public.user_subscriptions(status);

create or replace function public.bms_touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists trg_user_subscriptions_touch on public.user_subscriptions;
create trigger trg_user_subscriptions_touch
before update on public.user_subscriptions
for each row execute function public.bms_touch_updated_at();
"""

    return info
