from __future__ import annotations

from typing import Any, Dict


ASK_HIGH_RISK_REUSE_PATCH_VERSION = "2026-06-14-v1-strong-note-reuse"

STRONG_NOTE = (
    "Important: This is high-risk general guidance only, not a final tax or legal opinion. Exact deadlines, "
    "penalties, enforcement steps, objections, appeals, waivers, and liabilities can depend on the facts, "
    "documents, tax year, applicable law, and the relevant tax authority. Confirm with FIRS, the State IRS, "
    "or a qualified tax professional before taking action."
)

HIGH_MARKERS = (
    "appeal",
    "tribunal",
    "freeze",
    "bank account",
    "enforcement",
    "personal liability",
    "personally liable",
    "director",
    "disputed tax assessment",
    "waiver",
    "waive",
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


def _review_blocks_reuse(review: Any) -> bool:
    if not isinstance(review, dict) or not review:
        return False
    risk = _low(review.get("risk"))
    if risk == "reject":
        return True
    if review.get("misleading") is True:
        return True
    if review.get("relevant") is False:
        return True
    return False


def _update_cache_row(svc: Any, *, row_id: Any, normalized: str, canonical: str, payload: Dict[str, Any]) -> str | None:
    try:
        q = svc._sb().table("qa_cache").update(payload)
        if row_id:
            q = q.eq("id", row_id)
        else:
            q = q.eq("normalized_question", normalized)
            if canonical:
                q = q.eq("canonical_key", canonical)
        q.execute()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:500]}"


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
        if risk != "high" and not _looks_high_risk(question, answer):
            return result
        if _review_blocks_reuse(review):
            return result

        normalized = svc._normalize_question(question)
        canonical = svc._canonical_key(question)
        now_iso = svc._now_iso()
        safe_answer = _with_strong_note(answer)

        full_payload = {
            "answer": safe_answer,
            "source": "ai",
            "enabled": True,
            "priority": 12,
            "trust_score": 0.8,
            "review_status": "ai_reviewed_safe",
            "last_used_at": now_iso,
            "risk_level": "high",
            "disclaimer_type": "strong",
            "review_method": "high_risk_strong_disclaimer_auto_reuse",
            "second_ai_review": review or {},
            "reusable_without_credit": True,
            "reviewed_at": now_iso,
            "reviewed_by": "policy_after_second_ai",
            "review_notes": "High-risk answer approved for reuse only as general guidance with strong disclaimer.",
        }
        minimal_payload = {
            "answer": safe_answer,
            "source": "ai",
            "enabled": True,
            "priority": 12,
            "trust_score": 0.8,
            "review_status": "ai_reviewed_safe",
            "last_used_at": now_iso,
        }

        row_id = result.get("id")
        errors: list[str] = []
        for payload in (full_payload, minimal_payload):
            err = _update_cache_row(svc, row_id=row_id, normalized=normalized, canonical=canonical, payload=payload)
            if not err:
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
                    }
                )
                return updated
            errors.append(err)

        updated = dict(result)
        updated["high_risk_reuse_patch_error"] = errors[:2]
        return updated

    patched._ntg_high_risk_reuse_patch_applied = True
    svc._save_ai_answer_to_cache = patched
