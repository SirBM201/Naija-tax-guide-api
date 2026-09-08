from __future__ import annotations

"""Naija Tax Guide V1 Guided AI Tax Assistant with cost and source-integrity guards."""

import re
from typing import Any, Dict, Optional

from app.services import ask_service as _ask_service
from app.services.assistant_telemetry_service import record_assistant_event
from app.services.answer_metadata_service import build_source_metadata
from app.services.source_integrity_guard import assess_source_integrity, integrity_fallback

GUIDED_TAX_ASSISTANT_VERSION = "2026-09-08-v1-13-preserve-validated-source"

_GUIDANCE = {
    "menu": {"answer": "I can guide you through Naija Tax Guide.\n\nYou can ask a Nigeria tax question, use a tax calculator, check your plan or credits, review deadlines, take the quiz, or get help with your account. Tell me what you want to do.", "next_action": "Choose: tax question, calculator, deadlines, quiz, plan/credits, or account help."},
    "calculator": {"answer": "Use the calculator when you want a tax estimate from figures you already know. Calculator use is deterministic and should not consume AI credits. Select the relevant calculator, enter the requested figures, then review the result and assumptions.", "next_action": "Open the calculator and choose the tax you want to estimate."},
    "credits": {"answer": "Your Usage Credits are for paid AI tax answers. Database/library answers and approved free deterministic features do not consume AI credits. Check your current balance before requesting a paid AI answer.", "next_action": "Open Credits to view your balance or top up if your plan permits top-ups."},
    "plan": {"answer": "Your plan controls access to paid AI answers and other entitlements. Free users keep access to approved database/library answers, calculators and the non-AI quiz allowance; AI answers require eligible paid access.", "next_action": "Open Plan to review your current subscription or available upgrades."},
    "deadlines": {"answer": "The Deadlines area helps you review Nigeria tax filing and payment dates available in Naija Tax Guide. Because deadlines can depend on tax type and taxpayer circumstances, select the relevant tax and verify the effective period shown.", "next_action": "Open Deadlines and select the tax or obligation you need to check."},
    "quiz": {"answer": "The quiz helps you test your Nigeria tax knowledge. The approved free allowance is non-AI and should not consume AI credits; AI-generated explanations, where offered, remain subject to the applicable plan/credit rules.", "next_action": "Open Quiz and start an available attempt."},
    "account": {"answer": "I can guide you with account access, linking or unlinking supported channels, plan status and credits. I will not request passwords, OTPs or other authentication secrets in chat.", "next_action": "Tell me whether you need sign-in, channel linking, plan, or credit help."},
    "help": {"answer": "Tell me what you are trying to accomplish, not which screen you think you need. For example: ‘I want to estimate PAYE’, ‘When is VAT due?’, ‘How many credits do I have?’, or ‘I want to take the quiz’.", "next_action": "Describe your goal in one sentence."},
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _guidance_intent(message: str) -> Optional[str]:
    q = _normalized(message)
    if not q:
        return "menu"
    tax_terms = {"tax", "paye", "vat", "income tax", "company income", "withholding", "wht", "capital gains", "stamp duty", "levy", "firs", "revenue service", "taxable"}
    question_terms = {"what", "when", "who", "which", "why", "how much", "rate", "due", "calculate my"}
    if any(term in q for term in tax_terms) and any(term in q for term in question_terms):
        return None
    rules = (("calculator", ("calculator", "calculate", "estimate")), ("credits", ("credit", "balance", "top up", "topup")), ("plan", ("plan", "subscription", "upgrade", "pricing")), ("deadlines", ("deadline", "calendar", "due date")), ("quiz", ("quiz", "test my knowledge", "practice question")), ("account", ("account", "sign in", "login", "link whatsapp", "link telegram", "unlink")), ("help", ("help", "stuck", "confused", "what can you do", "guide me")), ("menu", ("menu", "start", "home")))
    for intent, terms in rules:
        if any(term in q for term in terms):
            return intent
    return None


def _deterministic_response(intent: str, *, account_id: str, channel: str) -> Dict[str, Any]:
    item = _GUIDANCE[intent]
    return {"ok": True, "answer": item["answer"], "source": "guided_assistant", "mode": "deterministic_guidance", "next_action": item["next_action"], "meta": {"assistant_version": GUIDED_TAX_ASSISTANT_VERSION, "intent": intent, "account_id": account_id, "channel": channel, "cost_route": "deterministic", "ai_called": False, "usage_charged": False, "credits_consumed": 0, "credit_cost": 0}}


def _record(account_id: str, channel: str, result: Dict[str, Any]) -> Dict[str, Any]:
    record_assistant_event(account_id=account_id, channel=channel, route="guided_tax_assistant", result=result)
    return result


def _source_metadata(result: Dict[str, Any], question: str) -> Dict[str, Any]:
    meta = dict(result.get("meta") or {})
    source_meta = meta.get("source_metadata") if isinstance(meta.get("source_metadata"), dict) else None
    if source_meta:
        return source_meta
    row = result.get("_source_row") if isinstance(result.get("_source_row"), dict) else {}
    if row:
        return build_source_metadata(source_kind=_clean(result.get("source") or result.get("mode") or "answer"), question=question, mode=_clean(result.get("mode")), row=row, ai_model=_clean(meta.get("ai_model") or meta.get("model")))
    return build_source_metadata(source_kind=_clean(result.get("source") or result.get("mode") or "answer"), question=question, mode=_clean(result.get("mode")), ai_model=_clean(meta.get("ai_model") or meta.get("model")))


def _apply_source_integrity(result: Dict[str, Any], question: str) -> Dict[str, Any]:
    result = dict(result)
    meta = dict(result.get("meta") or {})
    source_meta = _source_metadata(result, question)
    assessment = assess_source_integrity(source_meta)
    meta["source_metadata"] = source_meta
    meta["source_integrity"] = assessment
    result["meta"] = meta
    result.pop("_source_row", None)
    if assessment.get("blocked"):
        return integrity_fallback(source_meta, assessment)
    return result


def _precharge_integrity_find(original_find, evidence: Dict[str, Any]):
    """Validate cache/library material before charging and retain that exact
    validated evidence for the final public response. This avoids rebuilding
    source metadata after ask_guarded intentionally compacts its result.
    """
    def wrapped(question: str, lang: str = "en"):
        found = original_find(question, lang=lang)
        if not isinstance(found, dict) or not found.get("found") or not found.get("answer"):
            return found
        row = found.get("row") if isinstance(found.get("row"), dict) else {}
        source_meta = build_source_metadata(source_kind=_clean(found.get("source") or found.get("mode") or "database"), question=question, mode=_clean(found.get("mode")), row=row)
        assessment = assess_source_integrity(source_meta)
        evidence.clear()
        evidence.update({"source_metadata": source_meta, "source_integrity": assessment, "row": row})
        if assessment.get("blocked"):
            fallback = integrity_fallback(source_meta, assessment)
            return {"ok": True, "found": True, "answer": fallback.get("answer"), "source": "source_integrity_guard", "mode": "safe_escalation", "table": found.get("table"), "row": row, "normalized_question": found.get("normalized_question"), "canonical_key": found.get("canonical_key"), "source_metadata": source_meta, "source_integrity": assessment}
        found = dict(found)
        found["source_metadata"] = source_meta
        found["source_integrity"] = assessment
        return found
    return wrapped


def guide_or_answer(*, account_id: str, message: str, lang: str = "en", channel: str = "web", provider_user_id: str = "", action_code: str = "ai_tax_answer", **extra: Any) -> Dict[str, Any]:
    account_id = _clean(account_id)
    message = _clean(message)
    channel = _clean(channel).lower() or "web"
    if not account_id:
        return {"ok": False, "error": "account_id_required", "message": "A signed-in account is required."}
    if not message:
        return _record(account_id, channel, _deterministic_response("menu", account_id=account_id, channel=channel))
    intent = _guidance_intent(message)
    if intent:
        return _record(account_id, channel, _deterministic_response(intent, account_id=account_id, channel=channel))

    validated_evidence: Dict[str, Any] = {}
    original_find = _ask_service._find_database_answer
    _ask_service._find_database_answer = _precharge_integrity_find(original_find, validated_evidence)
    try:
        result = _ask_service.ask_guarded(account_id=account_id, question=message, lang=lang, channel=channel, provider=channel, provider_user_id=provider_user_id, action_code=action_code, **extra)
    finally:
        _ask_service._find_database_answer = original_find

    if not isinstance(result, dict):
        result = {"ok": False, "error": "assistant_invalid_result", "message": "I could not generate an answer right now."}
    else:
        result = dict(result)
    meta = dict(result.get("meta") or {})
    source = _clean(result.get("source")).lower()

    # If the free knowledge boundary already validated the exact source used
    # for this answer, carry that evidence forward instead of reconstructing it.
    validated_meta = validated_evidence.get("source_metadata")
    validated_assessment = validated_evidence.get("source_integrity")
    if source in {"database", "library", "cache"} and isinstance(validated_meta, dict) and isinstance(validated_assessment, dict):
        meta["source_metadata"] = validated_meta
        meta["source_integrity"] = validated_assessment

    cost_route = "approved_knowledge" if source in {"database", "library", "cache"} else "paid_ai" if source == "ai" else source or "guarded_ask"
    meta.update({"assistant_version": GUIDED_TAX_ASSISTANT_VERSION, "cost_route": cost_route, "ai_called": source == "ai"})
    result["meta"] = meta
    result.setdefault("next_action", "Ask a follow-up tax question or choose another Naija Tax Guide workflow.")
    result = _apply_source_integrity(result, message)
    return _record(account_id, channel, result)
