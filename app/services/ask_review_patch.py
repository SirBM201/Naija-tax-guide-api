from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


ASK_REVIEW_PATCH_VERSION = "2026-06-14-v4-cost-aware-review-policy"

STANDARD_DISCLAIMER = (
    "Note: This is general information for guidance only. Tax outcomes depend on the taxpayer's facts, "
    "records, current law, and the relevant tax authority. Confirm the position before filing, paying, "
    "objecting, or relying on it for a real compliance decision."
)

STRONG_DISCLAIMER = (
    "Important: This is high-risk general guidance only, not a final tax/legal opinion. Exact deadlines, "
    "penalties, enforcement steps, objections, appeals, waivers, and liabilities can depend on the facts, "
    "documents, tax year, applicable law, and the relevant tax authority. Confirm with FIRS, the State IRS, "
    "or a qualified tax professional before taking action."
)

HIGH_RISK_TERMS = {
    "appeal",
    "tribunal",
    "court",
    "freeze",
    "bank account",
    "enforcement",
    "investigation",
    "criminal",
    "fraud",
    "prosecution",
    "director liable",
    "personal liability",
    "object within",
    "30 days",
    "waiver",
    "waive",
    "penalty amount",
    "how much is the penalty",
    "exact penalty",
}

MEDIUM_RISK_TERMS = {
    "penalty",
    "late return",
    "late filing",
    "late payment",
    "interest",
    "outstanding tax",
    "withholding tax credit",
    "wht credit",
    "unutilized",
    "tax audit",
    "additional assessment",
    "assessment notice",
    "firs",
    "state irs",
    "objection",
    "remit",
    "filing deadline",
}

MEDIUM_REVIEW_TRIGGER_TERMS = {
    "exact",
    "how much",
    "calculate",
    "appeal",
    "tribunal",
    "court",
    "freeze",
    "bank account",
    "enforcement",
    "personal liability",
    "director",
    "criminal",
    "fraud",
    "waive",
    "waiver",
    "object within",
    "days",
}


_json_object_re = re.compile(r"\{[\s\S]*\}")


def _contains_any(text: str, terms: set[str]) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in terms)


def _has_disclaimer(answer: str) -> bool:
    lower = (answer or "").lower()
    return (
        "general information" in lower
        or "guidance only" in lower
        or "confirm" in lower and ("tax authority" in lower or "professional" in lower)
    )


def _append_disclaimer(answer: str) -> str:
    clean = (answer or "").strip()
    if not clean:
        return STANDARD_DISCLAIMER
    if _has_disclaimer(clean):
        return clean
    return f"{clean}\n\n{STANDARD_DISCLAIMER}"


def _append_strong_disclaimer(answer: str) -> str:
    clean = (answer or "").strip()
    if not clean:
        return STRONG_DISCLAIMER
    if "high-risk general guidance" in clean.lower() or "qualified tax professional" in clean.lower():
        return clean
    return f"{clean}\n\n{STRONG_DISCLAIMER}"


def _fallback_risk(question: str, answer: str) -> str:
    joined = f"{question}\n{answer}".lower()
    if _contains_any(joined, HIGH_RISK_TERMS):
        return "high"
    if _contains_any(joined, MEDIUM_RISK_TERMS):
        return "medium"
    return "low"


def _looks_incomplete_or_uncertain(answer: str) -> bool:
    clean = (answer or "").strip()
    lower = clean.lower()
    if len(clean) < 180:
        return True
    uncertain_terms = {
        "i am not sure",
        "i'm not sure",
        "may be wrong",
        "unclear",
        "cannot determine",
        "not enough information",
        "depends" ,
    }
    if any(term in lower for term in uncertain_terms):
        return True
    if "direct answer" not in lower and "key points" not in lower:
        return True
    return False


def _medium_needs_second_ai(question: str, answer: str) -> bool:
    joined = f"{question}\n{answer}".lower()
    if _contains_any(joined, MEDIUM_REVIEW_TRIGGER_TERMS):
        return True
    if _looks_incomplete_or_uncertain(answer):
        return True
    if not _has_disclaimer(answer):
        # Medium answers can still be auto-approved with disclaimer added by code;
        # this alone should not force a second AI call.
        return False
    return False


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    match = _json_object_re.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _second_ai_review(question: str, answer: str, fallback_risk: str) -> Dict[str, Any]:
    try:
        from app.services.ai_service import ask_ai_chat, last_ai_error
    except Exception as exc:
        return {
            "ok": False,
            "risk": fallback_risk,
            "decision": "queue",
            "reason": f"review_ai_import_failed: {type(exc).__name__}",
        }

    prompt = f"""
You are the independent quality reviewer for Naija Tax Guide.
Review the candidate answer for reuse in a Nigerian tax guidance app.

Classify risk:
- low: basic definition, glossary, app usage, non-actionable general explanation.
- medium: routine compliance guidance, deadlines, penalties, interest, WHT credit, filings, documents, assessment notices, or FIRS/State IRS process guidance where a disclaimer is enough.
- high: legal strategy, appeal/objection consequences, enforcement, bank-freezing, personal liability, disputed facts, exact penalty amount with uncertainty, or guidance that can materially affect taxpayer action.
- reject: answer is irrelevant to the question, clearly wrong, overconfident, unsafe, or not Nigerian-tax-specific enough.

Approval rule:
- low can be approved if relevant and not misleading.
- medium can be approved if relevant and not misleading; disclaimer will be attached.
- high can be approved only if converted to safe general guidance with a strong disclaimer. Do not approve high-risk text that sounds like final tax/legal advice.
- reject must not be approved.

Return ONLY valid JSON:
{{
  "risk": "low" | "medium" | "high" | "reject",
  "relevant": true | false,
  "misleading": true | false,
  "auto_approve": true | false,
  "safe_answer": "optional safer answer or empty string",
  "reason": "short reason"
}}

Question:
{question}

Candidate answer:
{answer}
""".strip()

    try:
        review_text = ask_ai_chat(
            [
                {
                    "role": "system",
                    "content": "You are a strict Nigerian tax answer quality reviewer. Output only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            lang="en",
        )
    except Exception as exc:
        return {
            "ok": False,
            "risk": fallback_risk,
            "decision": "queue",
            "reason": f"review_ai_exception: {type(exc).__name__}",
        }

    data = _parse_json_object(review_text or "")
    if not data:
        return {
            "ok": False,
            "risk": fallback_risk,
            "decision": "queue",
            "reason": last_ai_error() or "review_ai_invalid_json",
            "raw": (review_text or "")[:500],
        }

    risk = str(data.get("risk") or fallback_risk).strip().lower()
    if risk not in {"low", "medium", "high", "reject"}:
        risk = fallback_risk

    relevant = bool(data.get("relevant") is True)
    misleading = bool(data.get("misleading") is True)
    auto_approve = bool(data.get("auto_approve") is True)
    safe_answer = str(data.get("safe_answer") or "").strip()

    if risk == "reject" or not relevant or misleading:
        decision = "queue"
        auto_approve = False
    elif risk in {"low", "medium"} and auto_approve:
        decision = "approve"
    elif risk == "high" and auto_approve:
        decision = "approve_safe_guidance"
    else:
        decision = "queue"
        auto_approve = False

    return {
        "ok": True,
        "risk": risk,
        "decision": decision,
        "auto_approve": auto_approve,
        "relevant": relevant,
        "misleading": misleading,
        "safe_answer": safe_answer[:4000],
        "reason": str(data.get("reason") or "").strip()[:500],
    }


def _safe_update_cache_row(svc: Any, *, row: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    row_id = row.get("id")
    normalized = payload.get("normalized_question")
    canonical = payload.get("canonical_key")

    try:
        query = svc._sb().table("qa_cache").update(payload)
        if row_id is not None:
            query = query.eq("id", row_id)
        elif normalized:
            query = query.eq("normalized_question", normalized)
            if canonical:
                try:
                    query = query.eq("canonical_key", canonical)
                except Exception:
                    pass
        else:
            return "qa_cache: cannot update without id or normalized_question"
        query.execute()
        return None
    except Exception as exc:
        return f"qa_cache.update: {type(exc).__name__}: {str(exc)[:700]}"


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
            enabled = str(row.get("enabled") if row.get("enabled") is not None else "").strip().lower()
            try:
                trust_score = float(row.get("trust_score") if row.get("trust_score") is not None else 0)
            except Exception:
                trust_score = 0.0

            if status not in {"approved", "active", "published", "ok", "enabled", "ai_reviewed_safe"}:
                return False
            if enabled in {"false", "0", "no", "off", ""}:
                return False
            if trust_score < 0.8:
                return False
        return base_ok(row)

    def save_for_review(*, question: str, answer: str, lang: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        normalized = svc._normalize_question(question)
        canonical = svc._canonical_key(question)
        clean_answer = svc._ensure_professional_answer_shape(answer, question)
        fallback_risk = _fallback_risk(question, clean_answer)
        review: Dict[str, Any] = {
            "ok": True,
            "risk": fallback_risk,
            "decision": "rule_auto_approve",
            "auto_approve": True,
            "reason": "Rule-based approval without second AI review.",
        }

        if fallback_risk == "low":
            risk = "low"
            approved = True
            answer_to_store = clean_answer
            schema_mode = "rule_auto_approved_low"
        elif fallback_risk == "medium" and not _medium_needs_second_ai(question, clean_answer):
            risk = "medium"
            approved = True
            answer_to_store = _append_disclaimer(clean_answer)
            schema_mode = "rule_auto_approved_medium_with_disclaimer"
        else:
            review = _second_ai_review(question, clean_answer, fallback_risk)
            risk = str(review.get("risk") or fallback_risk).lower()
            decision = str(review.get("decision") or "queue")
            approved = decision in {"approve", "approve_safe_guidance"} and risk in {"low", "medium", "high"}
            safe_answer = str(review.get("safe_answer") or "").strip()
            if risk == "medium":
                answer_to_store = _append_disclaimer(safe_answer or clean_answer)
                schema_mode = "second_ai_approved_medium_with_disclaimer" if approved else "review_queue_medium"
            elif risk == "high":
                answer_to_store = _append_strong_disclaimer(safe_answer or clean_answer)
                schema_mode = "second_ai_approved_high_safe_guidance" if approved else "review_queue_high"
            elif risk == "low":
                answer_to_store = safe_answer or clean_answer
                schema_mode = "second_ai_approved_low" if approved else "review_queue_low"
            else:
                answer_to_store = _append_strong_disclaimer(clean_answer)
                schema_mode = "review_queue_reject"
                approved = False

        now_iso = svc._now_iso()

        if approved:
            if risk == "low":
                trust_score = 0.9
                status = "approved"
                priority = 30
            elif risk == "medium":
                trust_score = 0.82
                status = "approved"
                priority = 22
            else:
                trust_score = 0.8
                status = "ai_reviewed_safe"
                priority = 12
            enabled = True
            tags = ["ai", "auto-reviewed", f"risk-{risk}"]
            reusable = True
        else:
            trust_score = 0.25 if risk == "reject" else 0.35
            status = "candidate"
            enabled = False
            priority = 5
            tags = ["ai", "review-candidate", f"risk-{risk}"]
            reusable = False

        existing, err = svc._query_rows("qa_cache", "*", limit=10, normalized_question=normalized)
        errors = [err] if err else []

        base = {
            "normalized_question": normalized,
            "canonical_key": canonical,
            "answer": answer_to_store,
            "tags": tags,
            "source": "ai",
            "enabled": enabled,
            "priority": priority,
            "lang": lang or "en",
            "intent_type": metadata.get("intent_type") or "general",
            "topic": metadata.get("topic") or "general",
            "trust_score": trust_score,
            "review_status": status,
            "jurisdiction": "nigeria",
            "last_used_at": now_iso,
        }

        exact_existing: Optional[Dict[str, Any]] = None
        for row in existing:
            if not isinstance(row, dict):
                continue
            row_key = svc._clean(row.get("canonical_key"))
            if row_key and row_key != canonical:
                continue
            exact_existing = row
            break

        if exact_existing:
            update_payloads = (
                base,
                {
                    "answer": answer_to_store,
                    "enabled": enabled,
                    "review_status": status,
                    "trust_score": trust_score,
                    "source": "ai",
                    "last_used_at": now_iso,
                },
                {
                    "answer": answer_to_store,
                    "enabled": enabled,
                    "review_status": status,
                    "trust_score": trust_score,
                },
            )
            for payload in update_payloads:
                update_err = _safe_update_cache_row(svc, row=exact_existing, payload=payload)
                if not update_err:
                    return {
                        "ok": True,
                        "table": "qa_cache",
                        "mode": "updated_existing_review_state",
                        "id": exact_existing.get("id"),
                        "schema_mode": schema_mode,
                        "review_status": status,
                        "enabled": enabled,
                        "trust_score": trust_score,
                        "risk": risk,
                        "second_ai_review": review,
                        "reusable_without_credit": reusable,
                        "normalized_question": normalized,
                        "canonical_key": canonical,
                    }
                errors.append(update_err)

            return {
                "ok": True,
                "table": "qa_cache",
                "mode": "already_exists_update_failed",
                "id": exact_existing.get("id"),
                "review_status": exact_existing.get("review_status") or exact_existing.get("status") or "unknown",
                "enabled": exact_existing.get("enabled"),
                "trust_score": exact_existing.get("trust_score"),
                "risk": risk,
                "second_ai_review": review,
                "reusable_without_credit": bool(row_ok(exact_existing)),
                "errors": errors[:6],
            }

        payloads = (
            {**base, "question": question[:800]},
            base,
            {
                "normalized_question": normalized,
                "canonical_key": canonical,
                "answer": answer_to_store,
                "source": "ai",
                "enabled": enabled,
                "review_status": status,
                "trust_score": trust_score,
                "lang": lang or "en",
            },
        )
        for payload in payloads:
            insert_err = svc._safe_insert("qa_cache", payload)
            if not insert_err:
                return {
                    "ok": True,
                    "table": "qa_cache",
                    "schema_mode": schema_mode,
                    "review_status": status,
                    "enabled": enabled,
                    "trust_score": trust_score,
                    "risk": risk,
                    "second_ai_review": review,
                    "reusable_without_credit": reusable,
                    "normalized_question": normalized,
                    "canonical_key": canonical,
                }
            errors.append(insert_err)

        return {
            "ok": False,
            "error": "review_cache_insert_failed",
            "errors": errors[:6],
            "risk": risk,
            "second_ai_review": review,
            "normalized_question": normalized,
            "canonical_key": canonical,
        }

    svc._row_review_ok = row_ok
    svc._save_ai_answer_to_cache = save_for_review
