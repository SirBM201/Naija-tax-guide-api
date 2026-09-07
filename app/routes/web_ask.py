from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, g, jsonify, request

from app.core.auth import require_auth_plus
from app.services.guided_tax_assistant_service import guide_or_answer
from app.services.qa_history_service import log_history_item_best_effort

bp = Blueprint("web_ask", __name__)
WEB_ASK_VERSION = "2026-09-07-v1-04b-guided-assistant"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lang(value: Any) -> str:
    return _safe_text(value).lower() or "en"


def _normalize_channel(value: Any) -> str:
    return _safe_text(value).lower() or "web"


def _history_source_from_result(result: Dict[str, Any], channel: str) -> str:
    source = _safe_text(result.get("source")).lower()
    if source in {"", "ai", "direct_cache", "cache", "rules_engine", "tax_process_composer", "ai_grounded"}:
        return channel
    return source


def _history_flags_from_result(result: Dict[str, Any]) -> Tuple[bool, int, bool, Optional[str]]:
    mode = _safe_text(result.get("mode")).lower()
    source = _safe_text(result.get("source")).lower()
    meta = dict(result.get("meta") or {})
    from_cache = mode in {"direct_cache", "library_match", "curated_starter"} or source in {"database", "library", "cache"}
    credits_consumed = int(meta.get("credits_consumed") or 0)
    usage_charged = bool(meta.get("usage_charged") is True or credits_consumed > 0)
    plan_code = _safe_text(meta.get("plan_code")) or None
    return from_cache, credits_consumed, usage_charged, plan_code


@bp.post("/web/ask")
@require_auth_plus
def web_ask():
    body: Dict[str, Any] = request.get_json(silent=True) or {}
    account_id = _safe_text(getattr(g, "account_id", None))
    message = _safe_text(body.get("question") or body.get("query") or body.get("text") or body.get("message") or "")
    lang = _normalize_lang(body.get("lang") or "en")
    channel = _normalize_channel(body.get("channel") or "web")

    res = guide_or_answer(account_id=account_id, message=message, lang=lang, channel=channel)
    status = 200
    if not res.get("ok") and res.get("error") in {"invalid_request", "account_required", "account_id_required", "question_required", "empty_question", "missing_question"}:
        status = 400

    answer_text = _safe_text(res.get("answer"))
    # Deterministic product guidance is intentionally not stored as tax Q&A history.
    if res.get("ok") and message and answer_text and res.get("mode") != "deterministic_guidance":
        from_cache, credits_consumed, usage_charged, plan_code = _history_flags_from_result(res)
        log_history_item_best_effort(
            account_id=account_id,
            question=message,
            answer=answer_text,
            lang=lang,
            source=_history_source_from_result(res, channel),
            from_cache=from_cache,
            canonical_key=None,
            normalized_question=message.lower(),
            plan_code=plan_code,
            credits_consumed=credits_consumed,
            usage_charged=usage_charged,
            channel=channel,
        )

    if isinstance(res, dict):
        res.setdefault("route_version", WEB_ASK_VERSION)
    return jsonify(res), status
