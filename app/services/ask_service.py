# app/services/ask_service.py
from __future__ import annotations

"""
ASK SERVICE (HARDENED)

Changes vs old version:
✅ account identity is STRICT:
    - account_id MUST be the canonical accounts.account_id
    - NO silent fallback to accounts.id anywhere

✅ Failure exposers:
    - If account resolution fails, response includes root_cause + fix + debug keys (when ASK_DEBUG enabled)

✅ Dev bypass:
    - Same behavior: routes/ask.py sets __bypass=True after validating BYPASS_TOKEN.
"""

import os
from typing import Any, Dict, Optional, Tuple, Union

from ..core.supabase_client import supabase

from .ai_service import ask_ai, ask_ai_chat, last_ai_error
from .subscriptions_service import get_subscription_status
from .qa_cache_service import (
    find_cached_answer,
    touch_cache_best_effort,
    upsert_ai_answer_to_cache_best_effort,
)
from .question_canonicalizer import basic_normalize, canonical_key
from .response_refiner import refine_answer
from .qa_usage_service import try_consume_cache_slot, get_cache_used_today


# -----------------------------
# Constants / knobs
# -----------------------------
MAX_QUESTION_CHARS = 2000

PAID_CACHE_DAILY_LIMIT = int((os.getenv("PAID_CACHE_DAILY_LIMIT", "1000") or "1000").strip())
FREE_CACHE_DAILY_LIMIT = int((os.getenv("FREE_CACHE_DAILY_LIMIT", "20") or "20").strip())

HARD_DAILY_MAX = int((os.getenv("HARD_DAILY_MAX", "1500") or "1500").strip())


# -----------------------------
# Debug
# -----------------------------
def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug_enabled() -> bool:
    return _truthy(os.getenv("ASK_DEBUG")) or _truthy(os.getenv("WEB_AUTH_DEBUG")) or _truthy(os.getenv("AUTH_DEBUG"))


def _dbg_pack(**kv: Any) -> Dict[str, Any]:
    if not _debug_enabled():
        return {}
    return dict(kv)


# -----------------------------
# Helpers
# -----------------------------
def _safe_str(v: Any) -> str:
    return (v or "").strip()


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n]


def _sb():
    return supabase() if callable(supabase) else supabase


def _dev_bypass_enabled(payload: Dict[str, Any]) -> bool:
    # Enabled only when routes/ask.py sets __bypass=True after validating token
    return bool(payload.get("__bypass") is True)


def _resolve_account_id_strict(payload: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    STRICT resolver:
      - if payload.account_id present => return it
      - else if (provider, provider_user_id) => lookup accounts.account_id ONLY
      - NEVER returns accounts.id.

    Returns (account_id, debug_info)
    """
    dbg: Dict[str, Any] = {}
    account_id = _safe_str(payload.get("account_id"))
    if account_id:
        dbg["source"] = "payload.account_id"
        return account_id, dbg

    provider = _safe_str(payload.get("provider")).lower()
    provider_user_id = _safe_str(payload.get("provider_user_id"))
    if not provider or not provider_user_id:
        dbg["source"] = "missing_provider_or_provider_user_id"
        return None, dbg

    try:
        res = (
            _sb()
            .table("accounts")
            .select("account_id")
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            dbg["source"] = "accounts_lookup_no_rows"
            return None, dbg

        row = rows[0] or {}
        aid = _safe_str(row.get("account_id"))
        if not aid:
            dbg["source"] = "accounts_account_id_empty"
            return None, dbg

        dbg["source"] = "accounts.account_id"
        return aid, dbg

    except Exception as e:
        dbg["source"] = "accounts_lookup_exception"
        dbg["error_type"] = type(e).__name__
        dbg["error"] = str(e)[:220]
        return None, dbg


# -----------------------------
# Subscription status (best effort)
# -----------------------------
def _get_subscription_status_best_effort(account_id: str, provider: str, provider_user_id: Optional[str]) -> Dict[str, Any]:
    try:
        return get_subscription_status(account_id, provider, provider_user_id)
    except Exception:
        return {
            "active": False,
            "state": "none",
            "reason": "subscription_check_failed",
            "plan_code": None,
            "expires_at": None,
            "grace_until": None,
        }


# -----------------------------
# Credits / Limits (RPC: consume_ai_credits)
# -----------------------------
def _consume_ai_credits(account_id: str, cost: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    cost = int(cost or 1)
    if cost < 1:
        cost = 1

    dbg: Dict[str, Any] = {"rpc": "consume_ai_credits", "cost": cost}

    try:
        res = _sb().rpc("consume_ai_credits", {"p_account_id": account_id, "p_cost": cost}).execute()
        data = getattr(res, "data", None)

        if isinstance(data, dict):
            ok = bool(data.get("ok"))
            reason = _safe_str(data.get("reason")) or ("ok" if ok else "no_credits")
            dbg.update({"rpc_ok": ok, "rpc_reason": reason})
            return ok, reason, dbg

        if isinstance(data, bool):
            dbg.update({"rpc_ok": bool(data)})
            return (data is True), ("ok" if data else "no_credits"), dbg

        dbg.update({"rpc_ok": False, "rpc_reason": "unexpected_response"})
        return False, "credits_rpc_unexpected_response", dbg

    except Exception as e:
        dbg.update({"rpc_ok": False, "rpc_reason": "rpc_failed"})
        if _debug_enabled():
            dbg.update({"error_type": type(e).__name__, "error": str(e)[:220]})
        return False, "credits_rpc_failed", dbg


# -----------------------------
# AI call
# -----------------------------
def _call_ai_model(question: str, lang: str = "en") -> str:
    ans = ask_ai(question, lang=lang)
    if not ans:
        raise RuntimeError(last_ai_error() or "ai_failed")
    return ans


def _cache_limit_message(used: int, limit: int) -> str:
    return (
        f"You’ve reached today’s fast-answer limit ({used}/{limit}).\n\n"
        "To continue now:\n"
        "• Try again tomorrow (limit resets daily), or\n"
        "• Use AI credits if available by asking a new question."
    )


# -----------------------------
# Public: unified ask (dict payload)
# -----------------------------
def ask_guarded(payload: Union[Dict[str, Any], str], *args, **kwargs) -> Dict[str, Any]:
    """
    Supports dict payloads used by routes.
    Backwards compatible if called with (question, account_id,...).
    """
    if isinstance(payload, str):
        question = payload
        account_id = kwargs.get("account_id") or (args[0] if args else None)
        if not account_id:
            return {"ok": False, "error": "account_required", "answer": "", "root_cause": "missing_account_id", "fix": "Provide canonical accounts.account_id."}
        return _ask_guarded_dict(
            {
                "question": question,
                "account_id": account_id,
                "provider": kwargs.get("provider") or "web",
                "provider_user_id": kwargs.get("provider_user_id"),
                "lang": kwargs.get("lang") or "en",
                "channel": kwargs.get("channel") or "ask",
            }
        )

    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_request", "answer": ""}

    return _ask_guarded_dict(payload)


def _ask_guarded_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    question = _truncate(_safe_str(payload.get("question")), MAX_QUESTION_CHARS)
    provider = (_safe_str(payload.get("provider")) or "web").lower()
    provider_user_id = _safe_str(payload.get("provider_user_id")) or None
    lang = _safe_str(payload.get("lang")) or "en"
    channel = _safe_str(payload.get("channel")) or ("web_ask" if provider == "web" else "ask")

    if not question:
        return {"ok": False, "error": "question_required", "answer": ""}

    account_id, ridbg = _resolve_account_id_strict(payload)
    if not account_id:
        out = {
            "ok": False,
            "error": "account_required",
            "answer": "",
            "root_cause": "account_id_missing_or_not_resolvable",
            "fix": (
                "Pass canonical accounts.account_id, OR pass provider+provider_user_id that maps to a row with a non-null accounts.account_id. "
                "DO NOT pass accounts.id."
            ),
        }
        out.update(_dbg_pack(account_resolution=ridbg))
        return out

    # Hard daily max gate (fast fail)
    try:
        used_today = int(get_cache_used_today(account_id) or 0)
        if used_today >= HARD_DAILY_MAX:
            return {
                "ok": False,
                "error": "hard_daily_limit_reached",
                "answer": "",
                "message": f"Daily limit reached ({used_today}/{HARD_DAILY_MAX}). Try again tomorrow.",
            }
    except Exception:
        pass

    # subscription status best effort
    sub = _get_subscription_status_best_effort(account_id, provider, provider_user_id)
    is_paid = bool(sub.get("active"))

    # Cache limit selection
    cache_limit = PAID_CACHE_DAILY_LIMIT if is_paid else FREE_CACHE_DAILY_LIMIT

    # Canonicalize question for cache key
    norm_q = basic_normalize(question)
    ckey = canonical_key(norm_q, lang=lang)

    # Try cache
    cached = find_cached_answer(ckey)
    if cached and cached.get("answer"):
        # touch + consume slot best effort
        try:
            touch_cache_best_effort(ckey)
        except Exception:
            pass
        try:
            try_consume_cache_slot(account_id, provider, channel)
        except Exception:
            pass

        ans = refine_answer(cached.get("answer") or "", lang=lang)
        return {
            "ok": True,
            "answer": ans,
            "source": "cache",
            "account_id": account_id,
            "subscription": sub,
            **_dbg_pack(cache_key=ckey),
        }

    # Cache slot check (best effort)
    try:
        used = int(get_cache_used_today(account_id) or 0)
        if used >= cache_limit:
            return {
                "ok": False,
                "error": "cache_limit_reached",
                "answer": "",
                "message": _cache_limit_message(used, cache_limit),
                "account_id": account_id,
                "subscription": sub,
                **_dbg_pack(cache_used=used, cache_limit=cache_limit),
            }
    except Exception:
        pass

    # AI credits gate (unless dev bypass)
    if not _dev_bypass_enabled(payload):
        ok_credits, reason, cdbg = _consume_ai_credits(account_id, cost=1)
        if not ok_credits:
            return {
                "ok": False,
                "error": "no_credits",
                "answer": "",
                "message": "You do not have enough AI credits to answer new questions right now.",
                "reason": reason,
                "account_id": account_id,
                "subscription": sub,
                **_dbg_pack(credits=cdbg),
            }

    # AI call
    try:
        ans = _call_ai_model(question, lang=lang)
    except Exception as e:
        return {
            "ok": False,
            "error": "ai_failed",
            "answer": "",
            "root_cause": f"{type(e).__name__}: {str(e)[:220]}",
            "fix": "Check OpenAI key/config, model settings, or upstream AI provider availability.",
        }

    ans = refine_answer(ans, lang=lang)

    # Cache write best effort
    try:
        upsert_ai_answer_to_cache_best_effort(ckey, question=question, answer=ans, lang=lang)
    except Exception:
        pass

    # Consume cache slot best effort
    try:
        try_consume_cache_slot(account_id, provider, channel)
    except Exception:
        pass

    return {
        "ok": True,
        "answer": ans,
        "source": "ai",
        "account_id": account_id,
        "subscription": sub,
        **_dbg_pack(cache_key=ckey),
    }
