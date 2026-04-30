# app/services/ask_service.py
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase
from app.services.query_classifier import classify_query
from app.services.answer_composer import (
    compose_ai_answer,
    compose_clarification,
    compose_direct_cache_answer,
    compose_insufficient_uncached,
    compose_rules_engine_answer,
    looks_like_internal_or_broken_answer,
    render_answer,
)
from app.services.qa_library_service import find_library_answer, find_library_candidates
from app.services.semantic_cache_service import retrieve_ranked_candidates, ranked_debug_dump
from app.services.usage_guard_service import get_ai_usage_state
from app.services.billing_guard_service import get_billing_state
from app.services.ai_service import generate_grounded_answer
from app.services.credits_service import (
    check_credit_balance,
    consume_credits,
    get_credit_balance_details,
    get_daily_usage,
    increment_daily_usage,
)
from app.services.tax_grounding_service import build_grounded_answer, grounding_prompt_context
from app.services.response_refiner import refine_response
from app.services.tax_rules.vat_rules import can_handle_vat_rule, resolve_vat_rule
from app.services.tax_rules.paye_rules import can_handle_paye_rule, resolve_paye_rule
from app.services.tax_rules.personal_income_tax_rules import can_handle_pit_rule, resolve_pit_rule
from app.services.tax_rules.tin_rules import can_handle_tin_rule, resolve_tin_rule
from app.services.tax_rules.tax_authority_rules import try_answer as try_tax_authority_answer
from app.services.tax_rules.withholding_tax_rules import try_answer as try_withholding_tax_rule_answer
from app.services.tax_rules.company_income_tax_rules import try_answer as try_company_income_tax_rule_answer
from app.services.tax_process_composer import try_compose

from app.services.qa_cache_service import (
    find_best_cached_answer,
    increment_cache_use,
    upsert_ai_answer_to_cache_best_effort,
    _normalize_question as _normalize_for_cache,
)


CHANNEL_ALIASES = {
    "wa": "whatsapp",
    "whatsapp": "whatsapp",
    "tg": "telegram",
    "telegram": "telegram",
    "web": "web",
    "web_chat": "web_chat",
    "chat": "web_chat",
}

TOPIC_ALIASES = {
    "vat": {"vat", "value_added_tax", "value added tax"},
    "value_added_tax": {"vat", "value_added_tax", "value added tax"},
    "paye": {"paye", "pay as you earn", "payroll", "payroll tax"},
    "personal_income_tax": {"personal_income_tax", "personal income tax", "pit"},
    "withholding_tax": {"withholding_tax", "withholding tax", "wht"},
    "company_income_tax": {"company_income_tax", "company income tax", "companies income tax", "cit", "cita"},
    "freelancer": {"freelancer", "self_employed", "self employed", "sole proprietor", "sole proprietorship"},
    "self_employed": {"freelancer", "self_employed", "self employed", "sole proprietor", "sole proprietorship"},
    "tin": {"tin", "tax identification number", "tax id"},
    "tax_clearance_certificate": {"tcc", "tax clearance certificate", "tax_clearance_certificate"},
    "general": {"general"},
}

GENERIC_INTENTS = {"", "general", "guidance", "definition"}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "you",
    "your",
}

ACTION_KEYWORDS = {
    "verify": {"verify", "verification", "validate", "validation", "confirm", "check", "authenticity", "genuine", "status"},
    "apply": {"apply", "application", "obtain", "get", "request"},
    "register": {"register", "registration", "enrol", "enroll", "enrollment"},
    "file": {"file", "filing", "submit", "return"},
    "pay": {"pay", "payment", "remit", "remittance"},
    "calculate": {"calculate", "computation", "compute", "rate", "percentage"},
    "exempt": {"exempt", "exemption", "zero", "rated", "zero-rated", "zero-rated"},
    "use": {"use", "used", "purpose", "needed"},
}


def _sb():
    return supabase() if callable(supabase) else supabase


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _include_debug() -> bool:
    return _truthy(os.getenv("DEBUG_AI")) or _truthy(os.getenv("SHOW_ASK_DEBUG"))


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    try:
        return int(raw) if raw else default
    except Exception:
        return default


def _tax_kb_enabled() -> bool:
    return _truthy(os.getenv("ENABLE_TAX_KB", "1"))


def _tax_kb_direct_threshold() -> int:
    return _env_int("TAX_KB_DIRECT_THRESHOLD", 55)


def _tax_kb_result_limit() -> int:
    return _env_int("TAX_KB_RESULT_LIMIT", 5)


def _normalize_channel(channel: Optional[str]) -> str:
    raw = _safe_str(channel).lower()
    return CHANNEL_ALIASES.get(raw, raw or "web")


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(value: str) -> List[str]:
    text = _normalize_text(value)
    if not text:
        return []
    return [t for t in text.split(" ") if t]


def _meaningful_tokens(value: str) -> List[str]:
    return [t for t in _tokenize(value) if t not in STOPWORDS]


def _detect_action_label(value: str) -> Optional[str]:
    tokens = set(_meaningful_tokens(value))
    if not tokens:
        return None

    for label, keywords in ACTION_KEYWORDS.items():
        if tokens.intersection(keywords):
            return label
    return None


def _action_conflicts(question: str, row_text: str) -> bool:
    q_action = _detect_action_label(question)
    row_action = _detect_action_label(row_text)
    return bool(q_action and row_action and q_action != row_action)


def _infer_topic_from_question(question: str, fallback: str = "general") -> str:
    q = _normalize_text(question)

    if any(x in q for x in ["tax clearance certificate", "tax_clearance_certificate"]) or re.search(r"\btcc\b", q):
        return "tax_clearance_certificate"
    if any(x in q for x in ["tax identification number", "tax identification", "tax id"]) or re.search(r"\btin\b", q):
        return "tin"
    if "withholding tax" in q or re.search(r"\bwht\b", q):
        return "withholding_tax"
    if any(x in q for x in ["company income tax", "companies income tax", "corporate income tax"]) or re.search(r"\bcit\b", q):
        return "company_income_tax"
    if any(x in q for x in ["pay as you earn", "payroll", "payroll tax"]) or re.search(r"\bpaye\b", q):
        return "paye"
    if "personal income tax" in q or re.search(r"\bpit\b", q):
        return "personal_income_tax"
    if any(x in q for x in ["vat", "value added tax", "value_added_tax"]):
        return "vat"
    if any(x in q for x in ["freelancer", "self employed", "self_employed", "sole proprietor", "sole proprietorship"]):
        return "freelancer"

    return str(fallback or "general").strip().lower()


def _infer_intent_from_question(question: str, fallback: str = "general") -> str:
    q = _normalize_text(question)

    if any(x in q for x in [
        "which tax authority",
        "what tax authority",
        "which authority",
        "who handles",
        "does firs or state",
        "does nrs or state",
        "who issues",
        "who receives",
        "who should receive",
        "which portal should i use",
        "which portal do i use",
    ]):
        return "authority"

    if any(x in q for x in ["verify", "verification", "validate", "validation", "check tin", "confirm tcc"]):
        return "verification"

    if any(x in q for x in ["what records", "what documents", "records should i keep", "documentation", "evidence", "requirements for"]):
        if "document" in q or "requirements" in q:
            return "documents"
        return "records"

    if any(x in q for x in ["register", "registration", "apply for", "apply to get", "obtain a tin", "get a tin"]):
        return "registration"

    if any(x in q for x in ["file", "filing", "submit", "return"]):
        return "filing"

    if any(x in q for x in ["pay", "payment", "remit", "remittance", "settle"]):
        return "payment"

    if any(x in q for x in ["rate", "percentage", "how much"]):
        return "rate"

    if any(x in q for x in ["exempt", "exemption", "zero rated", "zero rated", "zero-rated"]):
        return "exemption"

    if any(x in q for x in ["calculate", "computation", "compute"]):
        return "calculation"

    if any(x in q for x in [
        "who pays",
        "who must",
        "must i",
        "must we",
        "am i required",
        "should i charge",
        "who should",
        "who needs to",
        "comply with",
        "does it apply",
        "is liable",
        "deduct",
    ]):
        return "obligation"

    if q.startswith("what is ") or q.startswith("define ") or "meaning of" in q or "what does" in q:
        return "definition"

    return str(fallback or "general").strip().lower()


def _classification_to_meta(classification: Any, question: str = "") -> Dict[str, Any]:
    raw_topic = _safe_str(getattr(classification, "topic", "")).lower()
    raw_intent = _safe_str(getattr(classification, "intent_type", "")).lower()

    normalized_topic = _infer_topic_from_question(question, raw_topic or "general")
    normalized_intent = _infer_intent_from_question(question, raw_intent or "general")

    return {
        "topic": normalized_topic,
        "intent_type": normalized_intent,
        "jurisdiction": _safe_str(getattr(classification, "jurisdiction", "") or "nigeria") or "nigeria",
        "complexity": _safe_str(getattr(classification, "complexity", "")),
        "risk_level": _safe_str(getattr(classification, "risk_level", "")),
        "normalized_question": _safe_str(getattr(classification, "normalized_question", "") or _normalize_text(question)),
        "canonical_key": _safe_str(getattr(classification, "canonical_key", "")),
        "classifier_topic": raw_topic,
        "classifier_intent_type": raw_intent,
    }


def _with_usage_meta(
    result: Dict[str, Any],
    *,
    usage_state: Dict[str, Any],
    balance: int | None = None,
    daily_usage: int | None = None,
) -> Dict[str, Any]:
    payload = dict(result or {})
    meta = dict(payload.get("meta") or {})
    meta.setdefault("ai_used_month", _safe_int(usage_state.get("monthly_ai_usage"), 0))
    meta.setdefault("monthly_ai_used", _safe_int(usage_state.get("monthly_ai_usage"), 0))
    meta.setdefault("daily_usage", _safe_int(daily_usage if daily_usage is not None else usage_state.get("daily_ai_usage"), 0))
    meta.setdefault("daily_limit", _safe_int(usage_state.get("daily_ai_limit"), 0))
    meta.setdefault("credit_balance", _safe_int(balance if balance is not None else usage_state.get("credits_left"), 0))
    payload["meta"] = meta
    return payload


def _filtered_debug(debug: Dict[str, Any]) -> Dict[str, Any]:
    if _include_debug():
        return debug
    return {}


def _credit_balance_for_account(account_id: Optional[str]) -> int:
    if not account_id:
        return 0

    try:
        bal = get_credit_balance_details(account_id)
        if isinstance(bal, dict):
            for key in ["balance", "credits_left", "credit_balance"]:
                if key in bal:
                    return _safe_int(bal.get(key), 0)
    except Exception:
        pass

    try:
        bal = check_credit_balance(account_id)
        if isinstance(bal, dict):
            for key in ["balance", "credits_left", "credit_balance"]:
                if key in bal:
                    return _safe_int(bal.get(key), 0)
        return _safe_int(bal, 0)
    except Exception:
        return 0


def _daily_usage_for_account(account_id: Optional[str]) -> int:
    if not account_id:
        return 0
    try:
        result = get_daily_usage(account_id)
        if isinstance(result, dict):
            for key in ["count", "daily_usage", "used", "usage"]:
                if key in result:
                    return _safe_int(result.get(key), 0)
        return _safe_int(result, 0)
    except Exception:
        return 0


def _usage_state_for_account(account_id: Optional[str]) -> Dict[str, Any]:
    state = {
        "monthly_ai_usage": 0,
        "daily_ai_usage": 0,
        "daily_ai_limit": 0,
        "credits_left": 0,
        "has_ai_credit": False,
    }

    if not account_id:
        return state

    try:
        raw = get_ai_usage_state(account_id)
        if isinstance(raw, dict):
            state.update(raw)
    except Exception:
        pass

    balance = _credit_balance_for_account(account_id)
    daily_usage = _daily_usage_for_account(account_id)

    state["credits_left"] = balance
    state["has_ai_credit"] = balance > 0
    state["daily_ai_usage"] = daily_usage if daily_usage >= 0 else _safe_int(state.get("daily_ai_usage"), 0)
    return state


def _billing_state_for_account(account_id: Optional[str]) -> Dict[str, Any]:
    if not account_id:
        return {"ok": False}
    try:
        raw = get_billing_state(account_id)
        return raw if isinstance(raw, dict) else {"ok": False}
    except Exception:
        return {"ok": False}


def _candidate_to_dict(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)

    return {
        "candidate_id": getattr(candidate, "candidate_id", None),
        "question": getattr(candidate, "question", None),
        "answer": getattr(candidate, "answer", None),
        "canonical_key": getattr(candidate, "canonical_key", None),
        "intent_type": getattr(candidate, "intent_type", None),
        "topic": getattr(candidate, "topic", None),
        "jurisdiction": getattr(candidate, "jurisdiction", None),
        "lang": getattr(candidate, "lang", None),
        "trust_score": getattr(candidate, "trust_score", None),
        "review_status": getattr(candidate, "review_status", None),
        "source_authority_score": getattr(candidate, "source_authority_score", None),
        "authority_score": getattr(candidate, "source_authority_score", None),
        "similarity": getattr(candidate, "similarity", None),
        "match_type": getattr(candidate, "match_type", None),
        "rank_score": getattr(candidate, "rank_score", None),
        "source": "cache",
    }


def _topic_matches(question_meta: Dict[str, Any], row: Dict[str, Any], question: str) -> bool:
    row_topic = _safe_str(row.get("topic")).lower()
    meta_topic = _safe_str(question_meta.get("topic")).lower()
    inferred_topic = _infer_topic_from_question(question, meta_topic or "general")

    if not row_topic:
        return False
    if row_topic == meta_topic or row_topic == inferred_topic:
        return True

    row_aliases = TOPIC_ALIASES.get(row_topic, {row_topic})
    meta_aliases = TOPIC_ALIASES.get(meta_topic, {meta_topic}) | TOPIC_ALIASES.get(inferred_topic, {inferred_topic})
    return bool(row_aliases.intersection(meta_aliases))


def _intent_matches(question_meta: Dict[str, Any], row: Dict[str, Any], question: str) -> bool:
    row_intent = _safe_str(row.get("intent_type")).lower()
    meta_intent = _safe_str(question_meta.get("intent_type")).lower()
    inferred_intent = _infer_intent_from_question(question, meta_intent or "general")

    if not row_intent:
        return False

    if row_intent == meta_intent or row_intent == inferred_intent:
        return True

    if row_intent in GENERIC_INTENTS or meta_intent in GENERIC_INTENTS or inferred_intent in GENERIC_INTENTS:
        return False

    return False


def _question_is_short(question: str) -> bool:
    nq = _normalize_text(question)
    return bool(nq) and len(_tokenize(nq)) <= 3


def _looks_already_structured(text: str) -> bool:
    raw = _safe_str(text).lower()
    return (
        "answer:" in raw
        or "what this means:" in raw
        or "what to do next:" in raw
        or "steps:" in raw
    )


def _render_once(answer_text: str, question_meta: Dict[str, Any]) -> str:
    raw = _safe_str(answer_text)
    if not raw:
        return ""
    if _looks_already_structured(raw):
        return raw
    return render_answer(raw, question_meta=question_meta)


def _build_source_line(source_title: Optional[str]) -> str:
    title = _safe_str(source_title)
    return f"Source: {title}" if title else ""


def _chunk_to_direct_answer(row: Dict[str, Any], question_meta: Dict[str, Any]) -> str:
    summary = _safe_str(row.get("summary"))
    text_content = _safe_str(row.get("text_content"))
    source_title = _safe_str(row.get("source_title") or row.get("title"))

    base = summary or text_content
    if not base:
        return ""

    cleaned = re.sub(r"\s+", " ", base).strip()
    if len(cleaned) > 520:
        cleaned = cleaned[:520].rsplit(" ", 1)[0].strip() + "."

    if source_title and "source:" not in cleaned.lower():
        cleaned = f"{cleaned}\n\nSource: {source_title}"

    return _render_once(cleaned, question_meta)


def _library_row_question_text(row: Dict[str, Any]) -> str:
    return _safe_str(
        row.get("normalized_question")
        or row.get("question")
        or row.get("canonical_key")
    )


def _library_overlap_count(row: Dict[str, Any], question: str) -> int:
    row_tokens = set(_meaningful_tokens(_library_row_question_text(row)))
    q_tokens = set(_meaningful_tokens(question))
    return len(row_tokens.intersection(q_tokens))


def _is_strong_library_direct_match(row: Dict[str, Any], question_meta: Dict[str, Any], question: str) -> bool:
    norm_q = _normalize_text(question)
    row_text = _library_row_question_text(row)
    row_norm = _normalize_text(row_text)

    if _action_conflicts(question, row_text):
        return False

    meta_topic = _safe_str(question_meta.get("topic")).lower()
    row_topic = _safe_str(row.get("topic")).lower()

    meta_intent = _safe_str(question_meta.get("intent_type")).lower()
    row_intent = _safe_str(row.get("intent_type")).lower()

    meta_ck = _normalize_text(_safe_str(question_meta.get("canonical_key")))
    row_ck = _normalize_text(_safe_str(row.get("canonical_key")))

    short_q = _question_is_short(question)
    overlap = _library_overlap_count(row, question)

    exact_norm = bool(norm_q and row_norm and norm_q == row_norm)
    exact_topic = bool(meta_topic and row_topic and meta_topic == row_topic)
    exact_intent = bool(meta_intent and row_intent and meta_intent == row_intent)

    if exact_norm:
        return True

    if meta_ck and row_ck and meta_ck == row_ck and exact_topic and (exact_intent or row_intent in GENERIC_INTENTS):
        return True

    if short_q and exact_topic and (exact_intent or row_intent in GENERIC_INTENTS):
        return True

    if not short_q and exact_topic and exact_intent and row_intent not in GENERIC_INTENTS and overlap >= 2:
        return True

    return False


def _is_strong_library_candidate_match(row: Dict[str, Any], question_meta: Dict[str, Any], question: str) -> bool:
    score = _safe_int(row.get("library_score"), 0)
    norm_q = _normalize_text(question)
    row_text = _library_row_question_text(row)
    row_norm = _normalize_text(row_text)

    if _action_conflicts(question, row_text):
        return False

    meta_topic = _safe_str(question_meta.get("topic")).lower()
    row_topic = _safe_str(row.get("topic")).lower()

    meta_intent = _safe_str(question_meta.get("intent_type")).lower()
    row_intent = _safe_str(row.get("intent_type")).lower()

    short_q = _question_is_short(question)
    overlap = _library_overlap_count(row, question)

    exact_norm = bool(norm_q and row_norm and norm_q == row_norm)
    exact_topic = bool(meta_topic and row_topic and meta_topic == row_topic)
    exact_intent = bool(meta_intent and row_intent and meta_intent == row_intent)

    if exact_norm and score >= 60:
        return True

    if short_q and exact_topic and (exact_intent or row_intent in GENERIC_INTENTS) and score >= 60:
        return True

    if not short_q and exact_topic and exact_intent and row_intent not in GENERIC_INTENTS and overlap >= 2 and score >= 70:
        return True

    if exact_topic and overlap >= 3 and score >= 85:
        return True

    return False


def _keyword_overlap_count(row: Dict[str, Any], question: str) -> int:
    q_tokens = set(_meaningful_tokens(question))
    keywords = row.get("keywords") or []
    keyword_tokens: List[str] = []

    if isinstance(keywords, list):
        for item in keywords:
            keyword_tokens.extend(_tokenize(str(item)))
    else:
        keyword_tokens.extend(_tokenize(str(keywords)))

    return len(q_tokens.intersection({t for t in keyword_tokens if t not in STOPWORDS}))


def _fetch_tax_source_rows(question_meta: Dict[str, Any], question: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not _tax_kb_enabled():
        return []

    try:
        res = (
            _sb()
            .table("tax_source_chunks")
            .select("chunk_id,source_id,chunk_order,topic,subtopic,intent_type,risk_level,jurisdiction,text_content,summary,keywords,law_version,is_current,source_priority")
            .eq("approved", True)
            .eq("is_current", True)
            .order("source_priority", desc=True)
            .order("chunk_order")
            .limit(max(20, limit * 4))
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception:
        return []

    if not isinstance(rows, list):
        return []

    source_ids = [str(r.get("source_id") or "").strip() for r in rows if str(r.get("source_id") or "").strip()]
    titles_by_source: Dict[str, str] = {}

    if source_ids:
        try:
            src_res = (
                _sb()
                .table("tax_source_registry")
                .select("source_id,title")
                .in_("source_id", list(dict.fromkeys(source_ids)))
                .execute()
            )
            src_rows = getattr(src_res, "data", None) or []
            for row in src_rows:
                sid = _safe_str((row or {}).get("source_id"))
                if sid:
                    titles_by_source[sid] = _safe_str((row or {}).get("title"))
        except Exception:
            pass

    results: List[Dict[str, Any]] = []

    for row in rows:
        score = 0.0
        reasons: List[str] = []

        if _topic_matches(question_meta, row, question):
            score += 40
            reasons.append("topic_match:+40")

        if _intent_matches(question_meta, row, question):
            score += 20
            reasons.append("intent_match:+20")

        overlap = _keyword_overlap_count(row, question)
        if overlap > 0:
            bonus = min(20, overlap * 4)
            score += bonus
            reasons.append(f"keyword_overlap:+{bonus}")

        priority_bonus = min(12, _safe_int(row.get("source_priority"), 0))
        if priority_bonus:
            score += priority_bonus
            reasons.append(f"source_priority:+{priority_bonus}")

        summary = _safe_str(row.get("summary"))
        if summary:
            score += 4
            reasons.append("has_summary:+4")

        if score <= 0:
            continue

        item = dict(row)
        item["source_title"] = titles_by_source.get(_safe_str(row.get("source_id")), "")
        item["kb_score"] = round(score, 3)
        item["kb_reasons"] = reasons
        item["keyword_overlap_count"] = overlap
        results.append(item)

    results.sort(
        key=lambda r: (
            _safe_float(r.get("kb_score"), 0.0),
            _safe_int(r.get("source_priority"), 0),
        ),
        reverse=True,
    )
    return results[:limit]


def _is_strong_kb_direct_match(row: Dict[str, Any], question_meta: Dict[str, Any], question: str) -> bool:
    score = _safe_float(row.get("kb_score"), 0.0)
    overlap = _safe_int(row.get("keyword_overlap_count"), 0)
    short_q = _question_is_short(question)
    row_text = _safe_str(row.get("summary") or row.get("text_content") or row.get("source_title"))

    if _action_conflicts(question, row_text):
        return False

    row_topic = _safe_str(row.get("topic")).lower()
    meta_topic = _safe_str(question_meta.get("topic")).lower()

    row_intent = _safe_str(row.get("intent_type")).lower()
    meta_intent = _safe_str(question_meta.get("intent_type")).lower()

    exact_topic = row_topic and meta_topic and row_topic == meta_topic
    exact_intent = row_intent and meta_intent and row_intent == meta_intent and row_intent not in GENERIC_INTENTS

    if short_q and exact_topic and (exact_intent or row_intent in GENERIC_INTENTS) and score >= _tax_kb_direct_threshold():
        return True

    if exact_topic and exact_intent and score >= _tax_kb_direct_threshold():
        return True

    if exact_topic and overlap >= 2 and score >= 60:
        return True

    if score >= 80:
        return True

    return False


def _extract_rule_answer(result: Any) -> Optional[str]:
    if isinstance(result, dict):
        if result.get("ok") and _safe_str(result.get("answer")):
            return _safe_str(result.get("answer"))
        return None

    answer = _safe_str(result)
    return answer or None


def _resolve_rules(question: str, topic: str, intent_type: str) -> Optional[str]:
    try:
        authority_answer = _extract_rule_answer(
            try_tax_authority_answer(question=question, topic=topic, intent_type=intent_type)
        )
        if authority_answer:
            return authority_answer
    except Exception:
        pass

    try:
        if can_handle_tin_rule(question, topic, intent_type):
            return resolve_tin_rule(question, intent_type)
    except Exception:
        pass

    try:
        if can_handle_vat_rule(question, topic, intent_type):
            return resolve_vat_rule(question, intent_type)
    except Exception:
        pass

    try:
        if can_handle_paye_rule(question, topic, intent_type):
            return resolve_paye_rule(question, intent_type)
    except Exception:
        pass

    try:
        if can_handle_pit_rule(question, topic, intent_type):
            return resolve_pit_rule(question, intent_type)
    except Exception:
        pass

    try:
        wht_answer = _extract_rule_answer(
            try_withholding_tax_rule_answer(question=question, topic=topic, intent_type=intent_type)
        )
        if wht_answer:
            return wht_answer
    except Exception:
        pass

    try:
        cit_answer = _extract_rule_answer(
            try_company_income_tax_rule_answer(question=question, topic=topic, intent_type=intent_type)
        )
        if cit_answer:
            return cit_answer
    except Exception:
        pass

    return None


def _build_uncached_block_response(
    *,
    question_meta: Dict[str, Any],
    debug: Dict[str, Any],
    usage_state: Dict[str, Any],
    balance: int,
    error: str,
) -> Dict[str, Any]:
    # Friendly message for insufficient credits
    daily_limit = usage_state.get("daily_ai_limit", 0)
    
    friendly_message = f"""⚠️ **Insufficient AI Credits**

You have run out of AI credits to generate new answers.

**Current Status:**
- Available credits: 0
- Daily limit: {daily_limit} credits/day

**How to get more credits:**
1. **Upgrade your plan** - Higher tier plans include more monthly AI credits
2. **Purchase additional credits** - Available in the Billing section
3. **Wait for next month** - Monthly credits reset on your billing cycle

**What you can still do:**
- Ask basic tax questions from our knowledge base
- File your taxes
- Access your dashboard and history
- Get support from our help center

Need immediate help? Visit our Help Center or contact support."""
    
    res = compose_insufficient_uncached(question_meta=question_meta, debug=_filtered_debug(debug))
    payload = res.__dict__
    payload["error"] = error
    payload["answer"] = friendly_message
    payload["message"] = friendly_message
    return _with_usage_meta(payload, usage_state=usage_state, balance=balance)


def _finalize_ai_success(
    *,
    result: Dict[str, Any],
    account_id: str,
    usage_state: Dict[str, Any],
    debug: Dict[str, Any],
) -> Dict[str, Any]:
    consume_result = consume_credits(account_id, cost=1)
    if not consume_result.get("ok"):
        debug["credit_consume_result"] = consume_result
        payload = dict(result or {})
        payload.update(
            {
                "ok": False,
                "error": consume_result.get("error") or "credit_consume_failed",
                "message": "Your answer was generated but credit finalization failed.",
                "fix": consume_result.get("fix"),
                "root_cause": consume_result.get("root_cause"),
                "details": consume_result.get("details"),
                "answer": "",
            }
        )
        return _with_usage_meta(payload, usage_state=usage_state)

    daily_result = increment_daily_usage(account_id, inc=1)
    debug["credit_consume_result"] = consume_result
    debug["daily_usage_increment_result"] = daily_result

    balance_after = _safe_int(consume_result.get("balance_after"), 0)
    daily_after = _safe_int((daily_result or {}).get("count"), _safe_int(usage_state.get("daily_ai_usage"), 0) + 1)
    monthly_after = _safe_int(usage_state.get("monthly_ai_usage"), 0) + 1

    payload = dict(result
