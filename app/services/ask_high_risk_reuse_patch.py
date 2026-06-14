from __future__ import annotations

from typing import Any, Dict, Optional


ASK_HIGH_RISK_REUSE_PATCH_VERSION = "2026-06-14-v3-update-existing-by-normalized-question"

STRONG_NOTE = (
    "Important: This is high-risk general guidance only, not a final tax or legal opinion. Exact deadlines, "
    "penalties, enforcement steps, objections, appeals, waivers, and liabilities can depend on the facts, "
    "documents, tax year, applicable law, and the relevant tax authority. Confirm with FIRS, the State IRS, "
    "or a qualified tax professional before taking action."
)

HIGH_MARKERS = (
    "appeal",
    "tribunal",
    "court",
    "freeze",
    "bank account",
    "enforcement",
    "investigation",
    "personal liability",
    "personally liable",
    "director",
    "directors",
    "disputed tax assessment",
    "refuse to pay",
    "waiver",
    "waive",
    "payment terms",
    "outstanding tax",
)


def _low(value: Any) -> str:
    return str(value or "").strip().lower()


def _looks_high_risk(question: str, answer: str) -> bool:
    combined = f"{question}\n{answer}".lower()
    return any(marker in combined for marker in HIGH_MARKERS)


def _has_strong_note(answer: str) -> bool:
    lower = _low(answer)
    return "high-risk general guidance" in lower or "qualified tax professional" in lower


def _with_strong_note(answer: str) -> str:
    clean = str(answer or "").strip()
    if not clean:
        return STRONG_NOTE
    if _has_strong_note(clean):
        return clean
    return f"{clean}\n\n{STRONG_NOTE}"


def _review_is_clear_reject(review: Any) -> bool:
    if not isinstance(review, dict) or not review:
        return False
    if review.get("misleading") is True:
        return True
    if review.get("relevant") is False:
        return True
    if _low(review.get("risk")) == "reject":
        return True
    return False


def _execute_update(query) -> tuple[Optional[str], int]:
    try:
        res = query.execute()
        data = getattr(res, "data", None)
        if isinstance(data, list):
            return None, len(data)
        return None, -1
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:500]}", 0


def _update_by_id(svc: Any, *, row_id: Any, payload: Dict[str, Any]) -> tuple[Optional[str], int]:
    try:
        q = svc._sb().table("qa_cache").update(payload).eq("id", row_id)
        return _execute_update(q)
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:500]}", 0


def _update_by_normalized(svc: Any, *, normalized: str, payload: Dict[str, Any]) -> tuple[Optional[str], int]:
    try:
        q = svc._sb().table("qa_cache").update(payload).eq("normalized_question", normalized)
        return _execute_update(q)
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:500]}", 0


def _insert_reusable_cache_row(svc: Any, *, full_payload: Dict[str, Any], core_payload: Dict[str, Any]) -> Optional[str]:
    for payload in (full_payload, core_payload):
        err = svc._safe_insert("qa_cache", payload)
        if not err:
            return None
    return "qa_cache insert failed for reusable high-risk guidance"


def apply_ask_high_risk_reuse_patch() -> None:
    try:
        from app.services import ask_service as svc
    except Exception:
        return

    original = getattr(svc, "_save_ai_answer_to_cache", None)
    if original is None or getattr(original, "_ntg_high_risk_reuse_patch_applied", False):
        return

    def patched(*, question: str, answer: str, lang: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        result = original(question=question, answer=answer, lang=lang, metadata=metadata)
        if not isinstance(result, dict):
            return result

        review = result.get("second_ai_review") if isinstance(result.get("second_ai_review"), dict) else {}
        risk = _low(result.get("risk") or review.get("risk"))
        high_risk = risk == "high" or _looks_high_risk(question, answer)
        if not high_risk:
            return result
        if _review_is_clear_reject(review):
            return result

        normalized = svc._normalize_question(question)
        canonical = svc._canonical_key(question)
        now_iso = svc._now_iso()
        safe_answer = _with_strong_note(answer)
        intent_type = metadata.get("intent_type") or "general"
        topic = metadata.get("topic") or "general"

        full_payload = {
            "normalized_question": normalized,
            "canonical_key": canonical,
            "answer": safe_answer,
            "tags": ["ai", "auto-reviewed", "risk-high", "strong-disclaimer"],
            "source": "ai",
            "enabled": True,
            "priority": 12,
            "lang": lang or "en",
            "intent_type": intent_type,
            "topic": topic,
            "trust_score": 0.8,
            "review_status": "ai_reviewed_safe",
            "jurisdiction": "nigeria",
            "last_used_at": now_iso,
            "risk_level": "high",
            "disclaimer_type": "strong",
            "review_method": "high_risk_strong_disclaimer_auto_reuse",
            "second_ai_review": review or {},
            "reusable_without_credit": True,
            "reviewed_at": now_iso,
            "reviewed_by": "policy_after_second_ai",
            "review_notes": "High-risk answer approved for reuse only as general guidance with strong disclaimer.",
            "question": question[:800],
        }
        core_payload = {
            "normalized_question": normalized,
            "canonical_key": canonical,
            "answer": safe_answer,
            "tags": ["ai", "auto-reviewed", "risk-high"],
            "source": "ai",
            "enabled": True,
            "priority": 12,
            "lang": lang or "en",
            "intent_type": intent_type,
            "topic": topic,
            "trust_score": 0.8,
            "review_status": "ai_reviewed_safe",
            "jurisdiction": "nigeria",
            "last_used_at": now_iso,
        }

        update_errors: list[str] = []
        updated_count = 0

        # Prefer direct row id when the underlying save returned it.
        row_id = result.get("id")
        if row_id:
            for payload in (full_payload, core_payload):
                err, count = _update_by_id(svc, row_id=row_id, payload=payload)
                if err:
                    update_errors.append(err)
                elif count != 0:
                    updated_count += 1
                    break

        # Critical fix: update by normalized_question only. Older candidate rows
        # may have a null/different canonical_key, and filtering by canonical_key
        # can silently match zero rows, causing repeated AI charges.
        if updated_count == 0:
            for payload in (full_payload, core_payload):
                err, count = _update_by_normalized(svc, normalized=normalized, payload=payload)
                if err:
                    update_errors.append(err)
                elif count != 0:
                    updated_count += 1
                    break

        insert_err = None
        if updated_count == 0:
            insert_err = _insert_reusable_cache_row(svc, full_payload=full_payload, core_payload=core_payload)

        updated = dict(result)
        updated.update(
            {
                "schema_mode": "high_risk_strong_disclaimer_auto_reuse",
                "review_status": "ai_reviewed_safe",
                "enabled": True,
                "trust_score": 0.8,
                "risk": "high",
                "reusable_without_credit": True,
                "normalized_question": normalized,
                "canonical_key": canonical,
                "high_risk_reuse_updated_count": updated_count,
                "high_risk_reuse_inserted": insert_err is None and updated_count == 0,
            }
        )
        if update_errors:
            updated["high_risk_reuse_update_warnings"] = update_errors[:3]
        if insert_err:
            updated["high_risk_reuse_insert_error"] = insert_err
        return updated

    patched._ntg_high_risk_reuse_patch_applied = True
    svc._save_ai_answer_to_cache = patched
