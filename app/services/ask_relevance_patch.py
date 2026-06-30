from __future__ import annotations

from typing import Any, Dict


ASK_RELEVANCE_PATCH_VERSION = "2026-06-14-v5-prioritize-approved-cache-rows"


def apply_ask_relevance_patch() -> None:
    """
    Prevent weak cache/library matches from answering the wrong question.

    Policy enforced here:
    - Use free database/library answers only when the question has an exact
      normalized_question or canonical_key match.
    - Do not use broad/fuzzy token matches for tax advice answers.
    - If there is no exact match, ask_service must continue to the paid AI
      fallback, where normal credit checks and one-credit successful AI debit
      already apply.
    """
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    try:
        from app.services.ask_answer_format_patch import apply_ask_answer_format_patch

        apply_ask_answer_format_patch()
    except Exception:
        pass

    try:
        from app.services.ask_review_patch import apply_ask_review_patch

        apply_ask_review_patch()
    except Exception:
        pass

    try:
        from app.services.ask_high_risk_reuse_patch import apply_ask_high_risk_reuse_patch

        apply_ask_high_risk_reuse_patch()
    except Exception:
        pass

    try:
        from app.services.ask_response_policy_patch import apply_ask_response_policy_patch

        apply_ask_response_policy_patch()
    except Exception:
        pass

    try:
        from app.services.billing_payment_patch import apply_billing_payment_patch

        apply_billing_payment_patch()
    except Exception:
        pass

    def _row_rank(row: Dict[str, Any]) -> tuple:
        status = svc._lower(row.get("review_status") or row.get("status") or "")
        enabled = str(row.get("enabled") if row.get("enabled") is not None else "").strip().lower() in {"true", "1", "yes", "on"}
        source = svc._lower(row.get("source") or row.get("source_type") or "")
        try:
            trust = float(row.get("trust_score") if row.get("trust_score") is not None else 0)
        except Exception:
            trust = 0.0
        reusable = str(row.get("reusable_without_credit") if row.get("reusable_without_credit") is not None else "").strip().lower() in {"true", "1", "yes", "on"}
        approved_status = status in {"approved", "active", "published", "ok", "enabled", "ai_reviewed_safe"}
        return (1 if enabled else 0, 1 if approved_status else 0, 1 if reusable else 0, trust, 1 if source.startswith("ai") else 0)

    def _sorted_rows(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        try:
            return sorted([r for r in rows if isinstance(r, dict)], key=_row_rank, reverse=True)
        except Exception:
            return [r for r in rows if isinstance(r, dict)]

    def _find_database_answer_strict(question: str, lang: str = "en") -> Dict[str, Any]:
        normalized = svc._normalize_question(question)
        canonical = svc._canonical_key(question)
        errors: list[str] = []

        # 1. Exact qa_cache matches only. Pull enough rows and rank approved
        # reusable rows first so older pending candidates do not hide a newer
        # approved high-risk safe-guidance row.
        for filters in (
            {"normalized_question": normalized, "lang": lang, "jurisdiction": "nigeria"},
            {"canonical_key": canonical, "lang": lang, "jurisdiction": "nigeria"},
            {"normalized_question": normalized},
            {"canonical_key": canonical},
        ):
            rows, err = svc._query_rows("qa_cache", "*", limit=50, **filters)
            if err:
                errors.append(err)
                continue

            for row in _sorted_rows(rows):
                answer = svc._answer_from_row(row)
                if answer and svc._row_review_ok(row):
                    return {
                        "ok": True,
                        "found": True,
                        "answer": svc._ensure_professional_answer_shape(answer, question),
                        "source": "database",
                        "mode": "direct_cache_exact",
                        "table": "qa_cache",
                        "row": row,
                        "normalized_question": normalized,
                        "canonical_key": canonical,
                        "strict_relevance": True,
                    }

        # 2. Exact qa_library matches only. Do not call find_library_answer()
        # here because that helper can return fuzzy candidates.
        for filters in (
            {"normalized_question": normalized, "lang": lang},
            {"canonical_key": canonical, "lang": lang},
            {"normalized_question": normalized},
            {"canonical_key": canonical},
        ):
            rows, err = svc._query_rows("qa_library", "*", limit=50, **filters)
            if err:
                errors.append(err)
                continue

            for row in _sorted_rows(rows):
                answer = svc._answer_from_row(row)
                if answer and svc._row_review_ok(row):
                    return {
                        "ok": True,
                        "found": True,
                        "answer": svc._ensure_professional_answer_shape(answer, question),
                        "source": "library",
                        "mode": "library_exact",
                        "table": "qa_library",
                        "row": row,
                        "normalized_question": normalized,
                        "canonical_key": canonical,
                        "strict_relevance": True,
                    }

        return {
            "ok": True,
            "found": False,
            "source": "database",
            "mode": "no_high_confidence_match",
            "errors": errors[:8],
            "normalized_question": normalized,
            "canonical_key": canonical,
            "strict_relevance": True,
            "policy": "exact_cache_or_library_only_then_ai_fallback",
        }

    svc._find_database_answer = _find_database_answer_strict
