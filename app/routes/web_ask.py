from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from app.core.auth import require_auth_plus
from app.services.ask_service import ask_guarded
from app.services.qa_history_service import log_history_item_best_effort

bp = Blueprint("web_ask", __name__)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lang(value: Any) -> str:
    text = _safe_text(value).lower()
    return text or "en"


def _normalize_channel(value: Any) -> str:
    text = _safe_text(value).lower()
    return text or "web"


def _history_source_from_result(result: Dict[str, Any], channel: str) -> str:
    source = _safe_text(result.get("source")).lower()
    if source in {"", "ai", "direct_cache", "cache", "rules_engine", "tax_process_composer", "ai_grounded"}:
        return channel
    return source


def _history_flags_from_result(result: Dict[str, Any]) -> Tuple[bool, int, bool, Optional[str]]:
    mode = _safe_text(result.get("mode")).lower()
    meta = dict(result.get("meta") or {})

    from_cache = mode == "direct_cache"
    credits_before = meta.get("credits_left_before")
    credits_after = meta.get("credits_left") or meta.get("credit_balance")

    credits_consumed = 0
    try:
        if credits_before is not None and credits_after is not None:
            credits_consumed = max(0, int(credits_before) - int(credits_after))
    except Exception:
        credits_consumed = 0

    usage_charged = bool(credits_consumed > 0 or mode == "ai_grounded")
    plan_code = _safe_text(meta.get("plan_code")) or None
    return from_cache, credits_consumed, usage_charged, plan_code


@bp.post("/web/ask")
@require_auth_plus
def web_ask():
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    account_id = getattr(g, "account_id", None)
    question = (
        body.get("question")
        or body.get("query")
        or body.get("text")
        or body.get("message")
        or ""
    )
    lang = _normalize_lang(body.get("lang") or "en")
    channel = _normalize_channel(body.get("channel") or "web")

    res = ask_guarded(
        account_id=_safe_text(account_id),
        question=_safe_text(question),
        lang=lang,
        channel=channel,
    )

    status = 200
    if not res.get("ok") and res.get("error") in {"invalid_request", "account_required", "question_required", "empty_question"}:
        status = 400

    answer_text = _safe_text(res.get("answer"))
    question_text = _safe_text(question)

    if res.get("ok") and question_text and answer_text:
        from_cache, credits_consumed, usage_charged, plan_code = _history_flags_from_result(res)
        log_history_item_best_effort(
            account_id=_safe_text(account_id),
            question=question_text,
            answer=answer_text,
            lang=lang,
            source=_history_source_from_result(res, channel),
            from_cache=from_cache,
            canonical_key=None,
            normalized_question=question_text.lower(),
            plan_code=plan_code,
            credits_consumed=credits_consumed,
            usage_charged=usage_charged,
            channel=channel,
        )

    return jsonify(res), status

