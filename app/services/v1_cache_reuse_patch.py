from __future__ import annotations

"""V1 cache-reuse hardening for the live Ask path.

Uses the existing qa_cache RPC infrastructure after the legacy exact/library
lookup misses. Semantic matching is deliberately deferred until qa_embeddings
is populated and validated, so a cache lookup never creates an embedding/API
cost merely to discover that the semantic table is empty.
"""

from typing import Any, Dict

V1_CACHE_REUSE_PATCH_VERSION = "2026-09-07-v1-keyword-reuse"
_APPLIED = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _answer_from_row(row: Dict[str, Any]) -> str:
    for key in ("resolved_answer", "answer", "answer_en", "response", "content", "body", "text"):
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _approved_reusable(row: Dict[str, Any]) -> bool:
    status = _clean(row.get("review_status") or row.get("status") or "approved").lower()
    if status not in {"approved", "active", "published", "ok", "enabled"}:
        return False
    enabled = row.get("enabled")
    if enabled is not None and _clean(enabled).lower() in {"false", "0", "no", "off"}:
        return False
    reusable = row.get("reusable_without_credit")
    if reusable is not None and _clean(reusable).lower() in {"false", "0", "no", "off"}:
        return False
    try:
        trust = float(row.get("trust_score") if row.get("trust_score") is not None else 1.0)
    except Exception:
        trust = 0.0
    return trust >= 0.75


def apply_v1_cache_reuse_patch() -> None:
    global _APPLIED
    if _APPLIED:
        return

    from app.services import ask_service
    from app.services.qa_hybrid_search_service import keyword_cache_match

    original_find = ask_service._find_database_answer
    original_save = ask_service._save_ai_answer_to_cache

    def cache_first_find(question: str, lang: str = "en") -> Dict[str, Any]:
        result = original_find(question, lang=lang)
        if result.get("found"):
            return result

        keyword = keyword_cache_match(question=question, lang=lang, limit=5)
        if not keyword.get("ok"):
            return result

        for row in keyword.get("matches") or []:
            if not isinstance(row, dict) or not _approved_reusable(row):
                continue
            answer = _answer_from_row(row)
            if not answer:
                continue
            try:
                keyword_score = float(row.get("keyword_score") or 0.0)
            except Exception:
                keyword_score = 0.0
            # Conservative V1 threshold: keyword reuse must be strong. Lower
            # confidence remains a miss and can proceed to entitled paid AI.
            if keyword_score < 0.85:
                continue
            return {
                "ok": True,
                "found": True,
                "answer": ask_service._ensure_professional_answer_shape(answer, question),
                "source": "database",
                "mode": "keyword_cache",
                "table": "qa_cache",
                "row": row,
                "match_score": keyword_score,
                "normalized_question": result.get("normalized_question"),
                "canonical_key": result.get("canonical_key"),
            }
        return result

    def safe_ai_cache_write(*, question: str, answer: str, lang: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Never auto-promote a fresh AI answer to approved reusable tax guidance."""
        # Preserve duplicate detection from the existing service, but prevent
        # its current automatic review_status='approved' write. Fresh AI output
        # can be delivered to the entitled user; promotion to reusable tax
        # knowledge requires the existing review/approval workflow.
        normalized = ask_service._normalize_question(question)
        existing, _ = ask_service._query_rows("qa_cache", "*", limit=1, normalized_question=normalized)
        if existing:
            return {"ok": True, "table": "qa_cache", "mode": "already_exists", "id": existing[0].get("id")}
        return {
            "ok": True,
            "mode": "review_required_not_cached",
            "review_status": "pending",
            "reusable_without_credit": False,
            "reason": "fresh_ai_tax_answer_requires_review",
        }

    ask_service._find_database_answer = cache_first_find
    ask_service._save_ai_answer_to_cache = safe_ai_cache_write
    ask_service.ASK_SERVICE_VERSION = "2026-09-07-v1-10-safe-cache-reuse"
    _APPLIED = True
