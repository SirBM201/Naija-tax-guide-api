# app/services/answer_metadata_patch.py
from __future__ import annotations

from typing import Any, Dict

from app.services.answer_metadata_service import (
    append_source_metadata_note,
    build_source_metadata,
)
from app.services.ai_service import finalize_tax_answer

ANSWER_METADATA_PATCH_VERSION = "2026-07-03-v4-success-only-ask-result-metadata"

_AI_PATCHED = False
_CACHE_PATCHED = False
_DB_WRAPPED_TARGET_ID: int | None = None
_ASK_WRAPPED_TARGET_IDS: set[int] = set()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _extract_question(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    payload = args[0] if args and isinstance(args[0], dict) else kwargs.get("payload")
    if isinstance(payload, dict):
        for key in ("question", "query", "text", "message", "user_message", "user_query"):
            value = _clean(payload.get(key))
            if value:
                return value
    for key in ("question", "query", "text", "message", "user_message", "user_query"):
        value = _clean(kwargs.get(key))
        if value:
            return value
    return ""


def enrich_database_result(
    result: Dict[str, Any],
    *,
    question: str,
    row: Dict[str, Any] | None = None,
    table: str = "",
    source_kind: str = "",
    mode: str = "",
) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    metadata = build_source_metadata(
        row=row or result.get("row") or {},
        source_kind=source_kind or _clean(result.get("source") or "database"),
        table=table or _clean(result.get("table")),
        question=question,
        mode=mode or _clean(result.get("mode")),
    )

    out = dict(result)
    out["source_metadata"] = metadata

    answer = _clean(out.get("answer"))
    if answer:
        out["answer"] = append_source_metadata_note(answer, metadata)

    return out


def enrich_ai_result(result: Dict[str, Any], *, question: str) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    metadata = build_source_metadata(
        row={},
        source_kind="ai",
        table="",
        question=question,
        mode=_clean(result.get("mode") or "ai_grounded"),
        ai_model=_clean(result.get("model") or result.get("provider_model")),
    )

    out = dict(result)
    out["source_metadata"] = metadata

    answer = _clean(out.get("answer"))
    if answer:
        answer = finalize_tax_answer(answer, question)
        out["answer"] = append_source_metadata_note(answer, metadata)

    return out


def enrich_ask_result(result: Dict[str, Any], *, question: str) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result

    out = dict(result)
    meta = dict(out.get("meta") or {}) if isinstance(out.get("meta"), dict) else {}

    source_metadata = out.get("source_metadata") if isinstance(out.get("source_metadata"), dict) else None
    if not source_metadata:
        source_metadata = meta.get("source_metadata") if isinstance(meta.get("source_metadata"), dict) else None
    if not source_metadata:
        source_metadata = build_source_metadata(
            row={},
            source_kind=_clean(out.get("source") or meta.get("source_kind") or "answer"),
            question=question,
            mode=_clean(out.get("mode")),
            ai_model=_clean(meta.get("model")),
        )

    meta["source_metadata"] = source_metadata
    out["meta"] = meta

    answer = _clean(out.get("answer"))
    if answer:
        out["answer"] = append_source_metadata_note(answer, source_metadata)

    return out


def _patch_ai_callers(svc: Any) -> None:
    global _AI_PATCHED, _CACHE_PATCHED

    original_call_ai_answer = getattr(svc, "_call_ai_answer", None)
    if callable(original_call_ai_answer) and not getattr(original_call_ai_answer, "_ntg_answer_metadata_wrapped", False) and not _AI_PATCHED:
        def _call_ai_answer_with_metadata(question: str, lang: str = "en", channel: str = "web", **kwargs: Any) -> Dict[str, Any]:
            result = original_call_ai_answer(question, lang=lang, channel=channel, **kwargs)
            if isinstance(result, dict) and result.get("ok") and _clean(result.get("answer")):
                return enrich_ai_result(result, question=question)
            return result

        _call_ai_answer_with_metadata._ntg_answer_metadata_wrapped = True  # type: ignore[attr-defined]
        svc._call_ai_answer = _call_ai_answer_with_metadata  # type: ignore[attr-defined]
        _AI_PATCHED = True

    original_save_ai_answer_to_cache = getattr(svc, "_save_ai_answer_to_cache", None)
    if callable(original_save_ai_answer_to_cache) and not getattr(original_save_ai_answer_to_cache, "_ntg_answer_metadata_wrapped", False) and not _CACHE_PATCHED:
        def _save_ai_answer_to_cache_with_metadata(*, question: str, answer: str, lang: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
            enriched_meta = dict(metadata or {})
            source_metadata = build_source_metadata(
                row={},
                source_kind="ai",
                question=question,
                mode="ai_grounded",
                ai_model=_clean(enriched_meta.get("model")),
            )
            enriched_meta["source_metadata"] = source_metadata
            return original_save_ai_answer_to_cache(
                question=question,
                answer=append_source_metadata_note(finalize_tax_answer(answer, question), source_metadata),
                lang=lang,
                metadata=enriched_meta,
            )

        _save_ai_answer_to_cache_with_metadata._ntg_answer_metadata_wrapped = True  # type: ignore[attr-defined]
        svc._save_ai_answer_to_cache = _save_ai_answer_to_cache_with_metadata  # type: ignore[attr-defined]
        _CACHE_PATCHED = True


def _patch_database_resolver(svc: Any) -> None:
    global _DB_WRAPPED_TARGET_ID

    original_find_database_answer = getattr(svc, "_find_database_answer", None)
    if not callable(original_find_database_answer):
        return

    if getattr(original_find_database_answer, "_ntg_answer_metadata_wrapped", False):
        return

    target_id = id(original_find_database_answer)
    if _DB_WRAPPED_TARGET_ID == target_id:
        return

    def _find_database_answer_with_metadata(question: str, lang: str = "en") -> Dict[str, Any]:
        result = original_find_database_answer(question, lang=lang)
        if isinstance(result, dict) and result.get("found") and _clean(result.get("answer")):
            return enrich_database_result(
                result,
                question=question,
                row=result.get("row") if isinstance(result.get("row"), dict) else {},
                table=_clean(result.get("table")),
                source_kind=_clean(result.get("source") or "database"),
                mode=_clean(result.get("mode")),
            )
        return result

    _find_database_answer_with_metadata._ntg_answer_metadata_wrapped = True  # type: ignore[attr-defined]
    svc._find_database_answer = _find_database_answer_with_metadata  # type: ignore[attr-defined]
    _DB_WRAPPED_TARGET_ID = target_id


def _wrap_ask_guarded_function(fn: Any):
    if not callable(fn) or getattr(fn, "_ntg_answer_metadata_wrapped", False):
        return fn
    target_id = id(fn)
    if target_id in _ASK_WRAPPED_TARGET_IDS:
        return fn

    def _ask_guarded_with_metadata(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = fn(*args, **kwargs)
        question = _extract_question(args, kwargs)
        if isinstance(result, dict) and result.get("ok") is True and _clean(result.get("answer")):
            return enrich_ask_result(result, question=question)
        return result

    _ask_guarded_with_metadata._ntg_answer_metadata_wrapped = True  # type: ignore[attr-defined]
    _ASK_WRAPPED_TARGET_IDS.add(target_id)
    return _ask_guarded_with_metadata


def _patch_ask_guarded_refs(svc: Any) -> None:
    wrapped = _wrap_ask_guarded_function(getattr(svc, "ask_guarded", None))
    if callable(wrapped):
        svc.ask_guarded = wrapped  # type: ignore[attr-defined]

    for module_name in ("app.routes.ask", "app.routes.whatsapp", "app.routes.telegram", "app.routes.web_ask"):
        try:
            module = __import__(module_name, fromlist=["ask_guarded"])
        except Exception:
            continue
        current = getattr(module, "ask_guarded", None)
        wrapped_module_fn = _wrap_ask_guarded_function(current)
        if callable(wrapped_module_fn):
            setattr(module, "ask_guarded", wrapped_module_fn)


def apply_answer_metadata_patch() -> None:
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    _patch_ai_callers(svc)
    _patch_database_resolver(svc)
    _patch_ask_guarded_refs(svc)
