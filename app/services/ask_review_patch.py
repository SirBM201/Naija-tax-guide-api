from __future__ import annotations

from typing import Any, Dict


def apply_ask_review_patch() -> None:
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    base_ok = svc._row_review_ok

    def row_ok(row: Dict[str, Any]) -> bool:
        src = svc._lower(row.get("source") or row.get("source_type") or "")
        if src.startswith("ai"):
            status = svc._lower(row.get("review_status") or row.get("status") or "")
            if status not in {"approved", "active", "published", "ok", "enabled"}:
                return False
        return base_ok(row)

    def save_for_review(*, question: str, answer: str, lang: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        normalized = svc._normalize_question(question)
        canonical = svc._canonical_key(question)
        clean_answer = svc._ensure_professional_answer_shape(answer, question)
        existing, err = svc._query_rows("qa_cache", "*", limit=10, normalized_question=normalized)
        errors = [err] if err else []

        for row in existing:
            if not isinstance(row, dict):
                continue
            row_key = svc._clean(row.get("canonical_key"))
            if row_key and row_key != canonical:
                continue
            return {
                "ok": True,
                "table": "qa_cache",
                "mode": "already_exists",
                "id": row.get("id"),
                "review_status": row.get("review_status") or row.get("status") or "unknown",
                "enabled": row.get("enabled"),
                "reusable_without_credit": bool(row_ok(row)),
            }

        base = {
            "normalized_question": normalized,
            "canonical_key": canonical,
            "answer": clean_answer,
            "tags": ["ai", "review-candidate"],
            "source": "ai",
            "enabled": False,
            "priority": 5,
            "lang": lang or "en",
            "intent_type": metadata.get("intent_type") or "general",
            "topic": metadata.get("topic") or "general",
            "trust_score": 0.35,
            "review_status": "candidate",
            "jurisdiction": "nigeria",
            "last_used_at": svc._now_iso(),
        }
        payloads = (
            {**base, "question": question[:800]},
            base,
            {
                "normalized_question": normalized,
                "canonical_key": canonical,
                "answer": clean_answer,
                "source": "ai",
                "enabled": False,
                "review_status": "candidate",
                "trust_score": 0.35,
                "lang": lang or "en",
            },
        )
        for payload in payloads:
            insert_err = svc._safe_insert("qa_cache", payload)
            if not insert_err:
                return {
                    "ok": True,
                    "table": "qa_cache",
                    "schema_mode": "review_candidate",
                    "review_status": "candidate",
                    "enabled": False,
                    "reusable_without_credit": False,
                    "normalized_question": normalized,
                    "canonical_key": canonical,
                }
            errors.append(insert_err)
        return {
            "ok": False,
            "error": "review_candidate_insert_failed",
            "errors": errors[:6],
            "normalized_question": normalized,
            "canonical_key": canonical,
        }

    svc._row_review_ok = row_ok
    svc._save_ai_answer_to_cache = save_for_review
