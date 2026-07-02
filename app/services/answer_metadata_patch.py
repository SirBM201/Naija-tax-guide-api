# app/services/answer_metadata_patch.py
from __future__ import annotations

from typing import Any, Dict

from app.services.answer_metadata_service import (
    append_source_metadata_note,
    build_source_metadata,
)
from app.services.ai_service import finalize_tax_answer

ANSWER_METADATA_PATCH_VERSION = "2026-07-03-v1-ask-response-source-metadata"

_APPLIED = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


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


def apply_answer_metadata_patch() -> None:
    global _APPLIED
    if _APPLIED:
        return

    try:
        from app.services import ask_service as svc
    except Exception:
        return

    original_call_ai_answer = getattr(svc, "_call_ai_answer", None)
    original_save_ai_answer_to_cache = getattr(svc, "_save_ai_answer_to_cache", None)

    if callable(original_call_ai_answer):
        def _call_ai_answer_with_metadata(question: str, lang: str = "en", channel: str = "web", **kwargs: Any) -> Dict[str, Any]:
            result = original_call_ai_answer(question, lang=lang, channel=channel, **kwargs)
            if isinstance(result, dict) and result.get("ok") and _clean(result.get("answer")):
                return enrich_ai_result(result, question=question)
            return result

        svc._call_ai_answer = _call_ai_answer_with_metadata  # type: ignore[attr-defined]

    if callable(original_save_ai_answer_to_cache):
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

        svc._save_ai_answer_to_cache = _save_ai_answer_to_cache_with_metadata  # type: ignore[attr-defined]

    _APPLIED = True
