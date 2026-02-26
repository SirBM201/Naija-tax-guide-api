# app/services/accounts_service.py
from __future__ import annotations

from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timezone
import uuid

from app.core.supabase_client import supabase


# =========================================================
# ACCOUNTS IDENTITY MODEL (IMPORTANT)
# =========================================================
# - accounts.id         : physical row PK (supabase row id)
# - accounts.account_id : CANONICAL "global account identifier" used everywhere
#
# Your system (web_tokens, web_sessions, user_subscriptions, etc.) should reference
# accounts.account_id, NOT accounts.id.
#
# This service therefore:
#  1) ALWAYS returns account_id = accounts.account_id
#  2) Auto-repairs legacy rows where account_id is NULL by setting account_id = id
#  3) Emits failure exposers (root cause + fix) on DB failures
# =========================================================


# ---------------------------------------------------------
# Time helpers
# ---------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _is_active_from_expiry(expiry: Optional[datetime]) -> bool:
    if not expiry:
        return False
    return expiry > _now_utc()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except Exception:
        return False


# ---------------------------------------------------------
# Failure exposers
# ---------------------------------------------------------
def _expose_db_error(
    *,
    where: str,
    exc: Exception,
    fix: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Standardized DB failure exposer so you always see:
      - root_cause (short)
      - where it happened
      - what to do next (fix)
    """
    payload: Dict[str, Any] = {
        "ok": False,
        "error": "db_error",
        "where": where,
        "root_cause": f"{type(exc).__name__}: {str(exc)[:260]}",
        "fix": fix,
    }
    if extra:
        payload["details"] = extra
    return payload


# ---------------------------------------------------------
# Provider normalization (must match DB constraint list)
# ---------------------------------------------------------
ALLOWED_PROVIDERS = {"wa", "tg", "msgr", "ig", "email", "web"}

PROVIDER_ALIASES = {
    "wa": "wa",
    "whatsapp": "wa",
    "waba": "wa",
    "tg": "tg",
    "telegram": "tg",
    "msgr": "msgr",
    "messenger": "msgr",
    "facebook_messenger": "msgr",
    "fb_messenger": "msgr",
    "facebook messenger": "msgr",
    "ig": "ig",
    "instagram": "ig",
    "instagram_dm": "ig",
    "email": "email",
    "mail": "email",
    "web": "web",
    "website": "web",
}


def _norm_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    return PROVIDER_ALIASES.get(p, p)


def _validate_provider_and_id(provider: str, provider_user_id: str) -> Optional[str]:
    provider = _norm_provider(provider)
    if provider not in ALLOWED_PROVIDERS:
        return "provider must be one of: wa, tg, msgr, ig, email, web"
    if not provider_user_id:
        return "provider_user_id required"

    if provider == "email":
        v = provider_user_id.strip().lower()
        if "@" not in v:
            return "provider_user_id must be a valid email for provider=email"

    return None


# ---------------------------------------------------------
# Identity helpers (canonical account_id)
# ---------------------------------------------------------
def _canonical_account_id_from_row(row: Dict[str, Any]) -> Optional[str]:
    """
    Canonical account key = accounts.account_id.
    If missing, fall back to id (legacy) but DO NOT return it without repair.
    """
    if not row:
        return None
    return (row.get("account_id") or row.get("id") or None)


def _repair_account_id_if_missing(row: Dict[str, Any]) -> None:
    """
    Best-effort repair for legacy rows:
      if accounts.account_id is NULL, set account_id = id
    This prevents FK failures across web_tokens/web_sessions/etc.
    """
    try:
        if not row:
            return
        rid = row.get("id")
        aid = row.get("account_id")
        if rid and not aid:
            supabase().table("accounts").update({"account_id": str(rid)}).eq("id", str(rid)).execute()
            row["account_id"] = str(rid)
    except Exception:
        # Never crash callers during a repair attempt.
        return


# ---------------------------------------------------------
# Account upsert / link / lookup
# ---------------------------------------------------------
def upsert_account(
    *,
    provider: str,
    provider_user_id: str,
    display_name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upsert a channel identity into accounts table.
    NOTE: Returns the full account row; callers should use ensure_account_id()
    if they need the canonical account_id.
    """
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {"ok": False, "error": err}

    payload = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "display_name": (display_name or None),
        "phone": (phone or None),
        "updated_at": _now_iso(),
    }

    try:
        res = (
            supabase()
            .table("accounts")
            .upsert(payload, on_conflict="provider,provider_user_id", returning="representation")
            .execute()
        )
    except Exception as e:
        return _expose_db_error(
            where="accounts.upsert_account",
            exc=e,
            fix="Check accounts table constraints/RLS. Ensure provider+provider_user_id unique constraint matches upsert on_conflict.",
            extra={"provider": provider, "provider_user_id": provider_user_id},
        )

    row = (getattr(res, "data", None) or [None])[0]
    if not row:
        return {
            "ok": False,
            "error": "account_upsert_failed",
            "root_cause": "upsert returned no row",
            "fix": "Ensure Supabase is returning representation and RLS allows select on accounts.",
            "details": {"provider": provider, "provider_user_id": provider_user_id},
        }

    # Repair legacy missing account_id (best-effort)
    _repair_account_id_if_missing(row)
    return {"ok": True, "account": row}


def lookup_account(
    *,
    provider: str,
    provider_user_id: str,
) -> Dict[str, Any]:
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {"ok": False, "error": err}

    try:
        res = (
            supabase()
            .table("accounts")
            .select("id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at")
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return _expose_db_error(
            where="accounts.lookup_account",
            exc=e,
            fix="Check accounts table schema and RLS permissions. Ensure select is allowed.",
            extra={"provider": provider, "provider_user_id": provider_user_id},
        )

    row = (getattr(res, "data", None) or [None])[0]
    if not row:
        return {"ok": True, "found": False}

    _repair_account_id_if_missing(row)
    return {
        "ok": True,
        "found": True,
        "account": row,
        "auth_user_id": row.get("auth_user_id"),
        "account_id": row.get("account_id"),
        "id": row.get("id"),
    }


def upsert_account_link(
    *,
    provider: str,
    provider_user_id: str,
    auth_user_id: str,
    display_name: Optional[str] = None,
    phone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Link a channel identity to an existing auth_user_id.

    NOTE: This function does not decide identity model; it simply links auth_user_id.
    """
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()
    auth_user_id = (auth_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {"ok": False, "error": err}
    if not auth_user_id:
        return {"ok": False, "error": "auth_user_id required"}
    if not _is_uuid(auth_user_id):
        return {"ok": False, "error": "auth_user_id must be a valid uuid"}

    existing = lookup_account(provider=provider, provider_user_id=provider_user_id)
    if existing.get("ok") and existing.get("found"):
        old = (existing.get("auth_user_id") or "").strip()
        if old and old != auth_user_id:
            return {
                "ok": False,
                "error": "This channel is already linked to another account.",
                "reason": "channel_already_linked",
            }

    payload = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "auth_user_id": auth_user_id,
        "display_name": (display_name or None),
        "phone": (phone or None),
        "updated_at": _now_iso(),
    }

    try:
        res = (
            supabase()
            .table("accounts")
            .upsert(payload, on_conflict="provider,provider_user_id", returning="representation")
            .execute()
        )
    except Exception as e:
        return _expose_db_error(
            where="accounts.upsert_account_link",
            exc=e,
            fix="Check accounts table constraints/RLS. Ensure provider+provider_user_id unique constraint matches upsert on_conflict.",
            extra={"provider": provider, "provider_user_id": provider_user_id, "auth_user_id": auth_user_id},
        )

    row = (getattr(res, "data", None) or [None])[0]
    if not row:
        return {
            "ok": False,
            "error": "account_link_failed",
            "root_cause": "upsert returned no row",
            "fix": "Ensure Supabase is returning representation and RLS allows select on accounts.",
        }

    _repair_account_id_if_missing(row)
    return {"ok": True, "account": row}


# ---------------------------------------------------------
# MAIN ENTRYPOINT USED BY ROUTES/SERVICES
# ---------------------------------------------------------
def ensure_account_id(
    *,
    provider: str,
    provider_user_id: str,
    phone_e164: Optional[str] = None,
    phone: Optional[str] = None,
    display_name: Optional[str] = None,
    contact: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ensures an account exists and returns CANONICAL accounts.account_id as account_id.

    Accepts multiple aliases to prevent future crashes:
      - phone_e164 or phone or contact (any of them)

    IMPORTANT:
      - This function DOES NOT return accounts.id anymore (legacy behavior removed).
      - If a legacy row has account_id=NULL, it repairs it (account_id=id) to keep FK-safe.
    """
    provider = _norm_provider(provider)
    provider_user_id = (provider_user_id or "").strip()

    err = _validate_provider_and_id(provider, provider_user_id)
    if err:
        return {"ok": False, "error": err}

    phone_value = (phone_e164 or phone or contact or None)

    res = upsert_account(
        provider=provider,
        provider_user_id=provider_user_id,
        display_name=display_name,
        phone=phone_value,
    )
    if not res.get("ok"):
        # bubble up full exposer
        return res

    row = res.get("account") or {}
    _repair_account_id_if_missing(row)

    account_id = _canonical_account_id_from_row(row)
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_missing",
            "root_cause": "accounts row has neither account_id nor id",
            "fix": "Verify accounts table has columns id (uuid) and account_id (uuid).",
            "details": {"row": row},
        }

    # Enforce that returned account_id equals accounts.account_id post-repair
    account_id = str(row.get("account_id") or account_id)

    return {"ok": True, "account_id": account_id, "account": row}


# ---------------------------------------------------------
# Plan status (kept as-is, but uses auth_user_id)
# ---------------------------------------------------------
def _plan_from_subscriptions_table(auth_user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        res = (
            supabase()
            .table("subscriptions")
            .select("status,plan_code,expires_at,started_at,created_at,updated_at")
            .eq("auth_user_id", auth_user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return None, f"subscriptions table error: {str(e)}"

    row = (getattr(res, "data", None) or [None])[0]
    if not row:
        return None, None

    exp = _parse_dt(row.get("expires_at"))
    is_active = _is_active_from_expiry(exp)

    return (
        {
            "known": True,
            "is_active": is_active,
            "plan": row.get("plan_code"),
            "status": row.get("status"),
            "plan_expiry": exp.isoformat() if exp else None,
            "notes": "From subscriptions table",
        },
        None,
    )


def _try_fetch_plan_from_table_guess(table: str, auth_user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        res = (
            supabase()
            .table(table)
            .select("*")
            .eq("auth_user_id", auth_user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return None, str(e)

    row = (getattr(res, "data", None) or [None])[0]
    if not row:
        return None, None

    # best-effort extraction
    status = row.get("status") or row.get("state")
    plan = row.get("plan_code") or row.get("plan") or row.get("tier")
    exp = _parse_dt(row.get("expires_at") or row.get("expiry") or row.get("plan_expiry"))
    is_active = _is_active_from_expiry(exp)

    return (
        {
            "known": True,
            "is_active": is_active,
            "plan": plan,
            "status": status,
            "plan_expiry": exp.isoformat() if exp else None,
            "notes": f"From {table} (guessed columns)",
        },
        None,
    )


def get_plan_status_for_auth_user(auth_user_id: str) -> Dict[str, Any]:
    auth_user_id = (auth_user_id or "").strip()
    if not auth_user_id:
        return {"ok": True, "known": False, "is_active": False, "plan": None, "status": None, "plan_expiry": None}
    if not _is_uuid(auth_user_id):
        return {"ok": True, "known": False, "is_active": False, "plan": None, "status": None, "plan_expiry": None}

    plan_obj, err = _plan_from_subscriptions_table(auth_user_id)
    if err is None and plan_obj:
        return {"ok": True, **plan_obj}

    debug_errors: List[Dict[str, str]] = []
    if err:
        debug_errors.append({"table": "subscriptions", "error": err})

    candidates = ["user_subscriptions", "user_plans", "plans"]
    for t in candidates:
        obj, e = _try_fetch_plan_from_table_guess(t, auth_user_id)
        if obj:
            return {"ok": True, **obj}
        if e:
            debug_errors.append({"table": t, "error": e})

    return {
        "ok": True,
        "known": False,
        "is_active": False,
        "plan": None,
        "status": None,
        "plan_expiry": None,
        "notes": "No subscription record found.",
        "debug_errors": debug_errors[:2],
    }
