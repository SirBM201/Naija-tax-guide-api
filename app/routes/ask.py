# app/routes/ask.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request, session

from app.core.supabase_client import supabase
from app.services.ask_service import ASK_SERVICE_VERSION, ask_guarded
from app.services.web_auth_service import get_account_id_from_request

try:
    from app.services.auth_service import get_current_user
except Exception:  # pragma: no cover
    get_current_user = None  # type: ignore

bp = Blueprint("ask", __name__)

ASK_ROUTE_VERSION = "2026-05-23-v6-web-whatsapp-telegram-credit-safe"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _debug_enabled() -> bool:
    return _truthy(_env("ASK_DEBUG", "0")) or _truthy(_env("DEBUG_AI", "0")) or _truthy(_env("SHOW_ASK_DEBUG", "0"))


def _safe_json() -> Dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _clip(value: Any, limit: int = 800) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _normalize_lang(value: Any) -> str:
    return _safe_text(value).lower() or "en"


def _normalize_channel(value: Any) -> str:
    return _safe_text(value).lower() or "web"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _get_bearer_token() -> str:
    h = (request.headers.get("Authorization") or "").strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return ""


def _is_dev_bypass_request() -> bool:
    expected = (_env("BYPASS_TOKEN") or _env("DEV_BYPASS_TOKEN") or "").strip()
    if not expected:
        return False

    bearer = _get_bearer_token()
    x_token = (request.headers.get("X-Auth-Token") or "").strip()
    return bearer == expected or x_token == expected


def _extract_account_id(auth_result: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    if isinstance(auth_result, str):
        account_id = auth_result.strip()
        return (account_id or None, {})

    if isinstance(auth_result, tuple):
        first = auth_result[0] if len(auth_result) > 0 else None
        second = auth_result[1] if len(auth_result) > 1 and isinstance(auth_result[1], dict) else {}
        if isinstance(first, str):
            account_id = first.strip()
            return (account_id or None, second)
        return (None, {"error": "invalid_auth_tuple", "raw": repr(auth_result)})

    if isinstance(auth_result, dict):
        account_id = str(auth_result.get("account_id") or auth_result.get("id") or "").strip()
        return (account_id or None, dict(auth_result))

    return (None, {"error": "unsupported_auth_result", "raw_type": str(type(auth_result))})


def _resolve_account_id_from_request(payload: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    debug: Dict[str, Any] = {
        "resolver": "ask_route_v6_session_web_token_payload",
        "payload_account_checked": False,
        "flask_session_checked": False,
        "auth_service_checked": False,
        "web_token_checked": False,
    }

    # 1. Explicit account_id in payload, used by WhatsApp/Telegram internal calls.
    payload_account_id = _safe_text(payload.get("account_id"))
    if payload_account_id:
        debug["payload_account_checked"] = True
        debug["account_source"] = "payload"
        return payload_account_id, debug

    # 2. Flask session values.
    debug["flask_session_checked"] = True
    for key in ("account_id", "user_id", "id"):
        value = _safe_text(session.get(key))
        if value:
            debug["account_source"] = f"flask_session.{key}"
            return value, debug

    # 3. Optional legacy auth service.
    if get_current_user is not None:
        try:
            debug["auth_service_checked"] = True
            user = get_current_user()  # type: ignore[misc]
            if isinstance(user, dict) and user:
                debug["auth_service_user_keys"] = sorted(list(user.keys()))
                account_id = _safe_text(user.get("account_id") or user.get("id"))
                if account_id:
                    debug["account_source"] = "auth_service"
                    return account_id, debug
        except Exception as exc:
            debug["auth_service_error"] = f"{exc.__class__.__name__}: {_clip(exc)}"

    # 4. Web token/cookie resolver.
    try:
        debug["web_token_checked"] = True
        auth_raw = get_account_id_from_request(request)
        account_id, auth_debug = _extract_account_id(auth_raw)
        debug["web_token_debug"] = auth_debug
        if account_id:
            debug["account_source"] = "web_token"
            return account_id, debug
    except Exception as exc:
        debug["web_token_error"] = f"{exc.__class__.__name__}: {_clip(exc)}"

    return None, debug


def _build_unauthorized(auth_debug: Dict[str, Any]):
    body: Dict[str, Any] = {
        "ok": False,
        "error": "unauthorized",
        "message": "Authentication required.",
    }
    if _debug_enabled():
        body["debug"] = {"auth": auth_debug}
    return jsonify(body), 401


def _history_source_from_result(result: Dict[str, Any], channel: str) -> str:
    source = _safe_text(result.get("source")).lower()
    if source in {"", "ai", "direct_cache", "cache", "rules_engine", "tax_process_composer", "ai_grounded", "database"}:
        return channel if source == "ai" else "database"
    return source


def _history_flags_from_result(result: Dict[str, Any]) -> Tuple[bool, int, bool, Optional[str]]:
    mode = _safe_text(result.get("mode")).lower()
    source = _safe_text(result.get("source")).lower()
    meta = dict(result.get("meta") or {})

    from_cache = mode in {"direct_cache", "library_match"} or source in {"database", "library", "cache"}
    credits_consumed = _as_int(meta.get("credits_consumed"), 0)

    if credits_consumed <= 0:
        credits_before = meta.get("credits_left_before")
        credits_after = meta.get("credits_left") or meta.get("credit_balance")
        try:
            if credits_before is not None and credits_after is not None:
                credits_consumed = max(0, int(credits_before) - int(credits_after))
        except Exception:
            credits_consumed = 0

    usage_charged = bool(
        meta.get("usage_charged") is True
        or credits_consumed > 0
        or mode in {"ai_grounded", "ai_grounded_manual_precharged"}
    )

    plan_code = _safe_text(meta.get("plan_code")) or None
    return from_cache, credits_consumed, usage_charged, plan_code


def _insert_history_direct(
    *,
    account_id: str,
    question: str,
    answer: str,
    lang: str,
    channel: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    from_cache, credits_consumed, usage_charged, plan_code = _history_flags_from_result(result)
    now_iso = _now_iso()

    # Keep the route self-contained and schema-tolerant. Full payload first,
    # then smaller fallbacks if the current qa_history table has fewer columns.
    full_payload: Dict[str, Any] = {
        "account_id": account_id or None,
        "question": question,
        "answer": answer,
        "lang": lang or "en",
        "source": _history_source_from_result(result, channel),
        "from_cache": from_cache,
        "plan_code": plan_code,
        "credits_consumed": credits_consumed,
        "usage_charged": usage_charged,
        "channel": channel or "web",
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    errors = []
    for idx, payload_item in enumerate(
        [
            full_payload,
            {k: v for k, v in full_payload.items() if k != "updated_at"},
            {
                "account_id": account_id or None,
                "question": question,
                "answer": answer,
                "lang": lang or "en",
                "source": _history_source_from_result(result, channel),
                "created_at": now_iso,
            },
            {
                "question": question,
                "answer": answer,
                "created_at": now_iso,
            },
        ]
    ):
        try:
            res = _sb().table("qa_history").insert(payload_item).execute()
            data = getattr(res, "data", None)
            return {
                "ok": True,
                "mode": f"direct_insert_{idx}",
                "row": data[0] if isinstance(data, list) and data else None,
            }
        except Exception as exc:
            errors.append(f"fallback_{idx}: {exc.__class__.__name__}: {_clip(exc)}")

    return {
        "ok": False,
        "error": "qa_history_direct_insert_failed",
        "errors": errors[:4],
    }


def _status_from_result(result: Dict[str, Any]) -> int:
    if result.get("ok"):
        return 200

    error = result.get("error")
    if error in {"question_required", "missing_question", "invalid_request", "account_required", "account_invalid", "account_id_required"}:
        return 400
    if error in {"unauthorized", "missing_token", "invalid_token", "session_expired"}:
        return 401
    if error in {"paid_plan_required", "insufficient_credits", "credit_access_denied"}:
        return 402
    return 500


def _handle_ask():
    payload = _safe_json()
    question = _safe_text(
        payload.get("question")
        or payload.get("query")
        or payload.get("text")
        or payload.get("message")
    )
    lang = _normalize_lang(payload.get("lang") or "en")
    channel = _normalize_channel(payload.get("channel") or "web")

    if not question:
        return jsonify(
            {
                "ok": False,
                "error": "missing_question",
                "message": "Question is required.",
            }
        ), 400

    account_id, auth_debug = _resolve_account_id_from_request(payload)

    if not account_id and _is_dev_bypass_request():
        account_id = _safe_text(payload.get("account_id") or _env("DEV_BYPASS_ACCOUNT_ID"))
        auth_debug["dev_bypass"] = bool(account_id)

    if not account_id:
        return _build_unauthorized(auth_debug)

    try:
        result = ask_guarded(
            account_id=account_id,
            question=question,
            lang=lang,
            channel=channel,
            provider=payload.get("provider"),
            provider_user_id=payload.get("provider_user_id"),
            action_code=payload.get("action_code") or "ai_tax_answer",
            **{k: v for k, v in payload.items() if k.startswith("__") or k in {"max_words", "max_output_tokens"}},
        )

        if not isinstance(result, dict):
            body: Dict[str, Any] = {
                "ok": False,
                "error": "invalid_ask_result",
                "message": "Ask service returned an invalid response.",
            }
            if _debug_enabled():
                body["debug"] = {
                    "result_type": str(type(result)),
                    "account_id": account_id,
                }
            return jsonify(body), 500

        answer_text = _safe_text(result.get("answer"))
        if result.get("ok") and question and answer_text:
            history_result = _insert_history_direct(
                account_id=account_id,
                question=question,
                answer=answer_text,
                lang=lang,
                channel=channel,
                result=result,
            )
            if _debug_enabled():
                result["history_result"] = history_result
                result["auth_debug"] = auth_debug

        return jsonify(result), _status_from_result(result)

    except Exception as exc:
        body: Dict[str, Any] = {
            "ok": False,
            "error": "ask_failed",
            "message": "We could not complete your request right now.",
        }

        if _debug_enabled():
            body["debug"] = {
                "exception_type": exc.__class__.__name__,
                "exception": str(exc),
                "account_id": account_id,
                "auth": auth_debug,
            }

        return jsonify(body), 500


@bp.route("/ask", methods=["POST", "OPTIONS"], strict_slashes=False)
def ask_no_slash():
    if request.method == "OPTIONS":
        return ("", 200)
    return _handle_ask()


@bp.route("/ask/", methods=["POST", "OPTIONS"], strict_slashes=False)
def ask_with_slash():
    if request.method == "OPTIONS":
        return ("", 200)
    return _handle_ask()


@bp.route("/ask/health", methods=["GET"], strict_slashes=False)
def ask_health():
    return jsonify(
        {
            "ok": True,
            "service": "ask",
            "route_version": ASK_ROUTE_VERSION,
            "ask_service_version": ASK_SERVICE_VERSION,
            "endpoints": [
                "POST /ask",
                "POST /ask/",
                "GET /ask/health",
                "GET /ask/credit-rules",
            ],
            "credit_policy": "database/library free; AI paid-plan credit only; WhatsApp Q5 manual debit protected",
        }
    ), 200


@bp.route("/ask/credit-rules", methods=["GET"], strict_slashes=False)
def ask_credit_rules():
    return jsonify(
        {
            "ok": True,
            "route_version": ASK_ROUTE_VERSION,
            "rules": {
                "database_or_library_answer": {
                    "credits": 0,
                    "free_plan": True,
                    "paid_plan": True,
                },
                "ai_tax_answer": {
                    "credits": 1,
                    "free_plan": False,
                    "paid_plan": True,
                    "requires_active_paid_subscription": True,
                },
                "whatsapp_q5_ai_explanation": {
                    "credits": 1,
                    "free_plan": False,
                    "paid_plan": True,
                    "deduction_owner": "app.routes.whatsapp v17 pre-debit",
                    "ask_service_double_debit_protection": True,
                },
                "basic_calculators": {
                    "credits": 0,
                    "free_plan": True,
                    "paid_plan": True,
                },
                "non_ai_quiz": {
                    "credits": 0,
                    "free_plan_daily_limit": 12,
                    "paid_plan": "unlimited",
                },
            },
        }
    ), 200
