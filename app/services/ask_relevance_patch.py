from __future__ import annotations

from typing import Any, Dict


ASK_RELEVANCE_PATCH_VERSION = "2026-09-07-v7-safe-keyword-cache-reuse"


def apply_ask_relevance_patch() -> None:
    """Enforce safe reusable knowledge and activate the V1 guided channel assistant."""
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    for module_name, function_name in (
        ("app.services.ask_answer_format_patch", "apply_ask_answer_format_patch"),
        ("app.services.ask_review_patch", "apply_ask_review_patch"),
        ("app.services.ask_high_risk_reuse_patch", "apply_ask_high_risk_reuse_patch"),
        ("app.services.ask_response_policy_patch", "apply_ask_response_policy_patch"),
        ("app.services.billing_payment_patch", "apply_billing_payment_patch"),
    ):
        try:
            module = __import__(module_name, fromlist=[function_name])
            getattr(module, function_name)()
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
        for table, mode, filters_list in (
            ("qa_cache", "direct_cache_exact", (
                {"normalized_question": normalized, "lang": lang, "jurisdiction": "nigeria"},
                {"canonical_key": canonical, "lang": lang, "jurisdiction": "nigeria"},
                {"normalized_question": normalized}, {"canonical_key": canonical},
            )),
            ("qa_library", "library_exact", (
                {"normalized_question": normalized, "lang": lang},
                {"canonical_key": canonical, "lang": lang},
                {"normalized_question": normalized}, {"canonical_key": canonical},
            )),
        ):
            for filters in filters_list:
                rows, err = svc._query_rows(table, "*", limit=50, **filters)
                if err:
                    errors.append(err)
                    continue
                for row in _sorted_rows(rows):
                    answer = svc._answer_from_row(row)
                    if answer and svc._row_review_ok(row):
                        return {
                            "ok": True, "found": True,
                            "answer": svc._ensure_professional_answer_shape(answer, question),
                            "source": "library" if table == "qa_library" else "database",
                            "mode": mode, "table": table, "row": row,
                            "normalized_question": normalized, "canonical_key": canonical,
                            "strict_relevance": True,
                        }
        return {
            "ok": True, "found": False, "source": "database",
            "mode": "no_high_confidence_match", "errors": errors[:8],
            "normalized_question": normalized, "canonical_key": canonical,
            "strict_relevance": True,
            "policy": "exact_then_reviewed_keyword_then_ai_fallback",
        }

    svc._find_database_answer = _find_database_answer_strict

    # Extend the strict exact path with the existing database keyword RPC and
    # prevent fresh AI answers from being auto-promoted as approved tax facts.
    # Semantic/vector matching remains dormant until qa_embeddings is populated
    # and validated; this avoids paying for query embeddings against an empty
    # vector table.
    try:
        from app.services.v1_cache_reuse_patch import apply_v1_cache_reuse_patch
        apply_v1_cache_reuse_patch()
    except Exception:
        pass

    # Apply last so Telegram/WhatsApp/inbound keep all established commands but
    # their natural-language fallback is now the cost-guarded V1 assistant.
    try:
        from app.services.guided_channel_patch import apply_guided_channel_patch
        apply_guided_channel_patch()
    except Exception:
        pass
