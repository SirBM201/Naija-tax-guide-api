from __future__ import annotations

import os
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.supabase_client import supabase
from app.services.ai_service import call_ai
from app.services.qa_cache_service import (
    answer_from_cache,
    derive_canonical_key,
    increment_cache_use,
    normalize_question_for_cache,
)
from app.services.qa_logging_service import log_qa_event_best_effort
from app.services.response_refiner import refine_answer


# =========================================================
# Boot-safe helpers
# =========================================================
def _sb():
    return supabase() if callable(supabase) else supabase


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _month_start_iso() -> str:
    now = _now_utc()
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return month_start.isoformat()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        n = int(v)
        return n
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
        return n
    except Exception:
        return default


def _clip(s: str, n: int = 240) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def _debug_enabled() -> bool:
    return _truthy(_env("ASK_DEBUG", "1")) or _truthy(_env("DEBUG", "0"))


def _dbg(msg: str) -> None:
    if _debug_enabled():
        print(msg, flush=True)


# =========================================================
# Tunables
# =========================================================
DEFAULT_MONTHLY_AI_LIMIT = _safe_int(_env("DEFAULT_MONTHLY_AI_LIMIT", "200"), 200)

# Strict semantic rules:
SEMANTIC_THRESHOLD_WITH_CREDITS = _safe_float(
    _env("SEMANTIC_THRESHOLD_WITH_CREDITS", "0.88"), 0.88
)
SEMANTIC_THRESHOLD_NO_CREDITS = _safe_float(
    _env("SEMANTIC_THRESHOLD_NO_CREDITS", "0.93"), 0.93
)

MIN_TRUST_WITH_CREDITS = _safe_float(_env("SEMANTIC_MIN_TRUST_WITH_CREDITS", "0.80"), 0.80)
MIN_TRUST_NO_CREDITS = _safe_float(_env("SEMANTIC_MIN_TRUST_NO_CREDITS", "0.90"), 0.90)

AI_CREDIT_COST_PER_ANSWER = _safe_int(_env("AI_CREDIT_COST_PER_ANSWER", "1"), 1)

QA_HISTORY_TABLE = _env("QA_HISTORY_TABLE", "qa_history")
AI_CREDIT_TABLE = _env("AI_CREDIT_TABLE", "ai_credit_balances")
EMBED_MATCH_RPC = _env("EMBED_MATCH_RPC", "match_qa_embeddings")


# =========================================================
# Intent / topic classification
# =========================================================
def _clean_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _infer_topic(question: str, canonical_key: Optional[str] = None) -> str:
    q = _clean_text(question)
    ck = (canonical_key or "").strip().lower()

    joined = f"{q} {ck}".strip()

    if "vat" in joined or "value added tax" in joined:
        return "vat"
    if "paye" in joined or "pay as you earn" in joined:
        return "paye"
    if "withholding tax" in joined or "wht" in joined:
        return "wht"
    if "personal income tax" in joined or "pit" in joined:
        return "pit"
    if "company income tax" in joined or "cit" in joined:
        return "cit"
    if "tin" in joined or "tax identification" in joined:
        return "tin"
    if "deduct" in joined or "deductible" in joined or "expense" in joined:
        return "deductions"
    if "register" in joined or "registration" in joined:
        return "registration"
    if "file" in joined or "return" in joined or "remit" in joined:
        return "filing"

    return "general"


def _infer_intent_type(question: str, canonical_key: Optional[str] = None) -> str:
    q = _clean_text(question)
    ck = (canonical_key or "").strip().lower()
    joined = f"{q} {ck}".strip()

    # Very important: registration/process should not match simple definition
    if any(x in joined for x in ["how do i", "how to", "steps", "process", "procedure"]):
        if any(x in joined for x in ["register", "registration", "sign up", "vat registration"]):
            return "registration_process"
        if any(x in joined for x in ["file", "filing", "return", "remit", "remittance"]):
            return "filing_process"
        return "how_to"

    if any(x in joined for x in ["what is", "meaning", "define", "stands for", "explain meaning"]):
        return "definition"

    if "rate" in joined or "percentage" in joined:
        return "rate_lookup"

    if any(x in joined for x in ["where do i pay", "where to pay", "pay where"]):
        return "payment_location"

    if any(x in joined for x in ["can i deduct", "deduct", "expense", "allowable"]):
        return "deductibility"

    if any(x in joined for x in ["register", "registration"]):
        return "registration_process"

    if any(x in joined for x in ["file", "filing", "return", "remit"]):
        return "filing_process"

    return "general"


def _intent_compatible(query_intent: str, candidate_intent: str) -> bool:
    q = (query_intent or "general").strip().lower()
    c = (candidate_intent or "general").strip().lower()

    if q == c:
        return True

    compatible_groups = [
        {"definition", "general"},
        {"how_to", "general"},
        {"registration_process", "how_to"},
        {"filing_process", "how_to"},
    ]

    for group in compatible_groups:
        if q in group and c in group:
            return True

    return False


def _topic_compatible(query_topic: str, candidate_topic: str) -> bool:
    q = (query_topic or "general").strip().lower()
    c = (candidate_topic or "general").strip().lower()

    if q == c:
        return True

    if q == "general" or c == "general":
        return True

    # registration/filing are process labels, not domain topics
    if q in {"registration", "filing"} and c in {"vat", "paye", "pit", "cit", "wht", "tin"}:
        return True
    if c in {"registration", "filing"} and q in {"vat", "paye", "pit", "cit", "wht", "tin"}:
        return True

    return False


# =========================================================
# Billing / usage
# =========================================================
def _get_credit_balance(account_id: str) -> int:
    """
    Reads visible credit balance from ai_credit_balances.
    Boot-safe and tolerant to column naming differences.
    """
    if not account_id:
        return 0

    try:
        res = (
            _sb()
            .table(AI_CREDIT_TABLE)
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return 0

        row = rows[0]
        for k in ("balance", "credits_left", "credit_balance", "available_credits"):
            if k in row:
                return max(_safe_int(row.get(k), 0), 0)

        return 0
    except Exception:
        return 0


def _decrement_credit_best_effort(account_id: str, cost: int = 1) -> None:
    if not account_id or cost <= 0:
        return

    try:
        res = (
            _sb()
            .table(AI_CREDIT_TABLE)
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return

        row = rows[0]
        current = None
        key = None

        for k in ("balance", "credits_left", "credit_balance", "available_credits"):
            if k in row:
                current = _safe_int(row.get(k), 0)
                key = k
                break

        if key is None:
            return

        new_value = max(current - cost, 0)
        (
            _sb()
            .table(AI_CREDIT_TABLE)
            .update({key: new_value})
            .eq("account_id", account_id)
            .execute()
        )
    except Exception:
        return


def _get_monthly_ai_usage(account_id: str) -> int:
    """
    Monthly AI usage from qa_history.
    Counts successful AI-generated responses in current month.
    """
    if not account_id:
        return 0

    try:
        month_start = _month_start_iso()
        res = (
            _sb()
            .table(QA_HISTORY_TABLE)
            .select("id", count="exact")
            .eq("account_id", account_id)
            .eq("source", "ai")
            .gte("created_at", month_start)
            .execute()
        )

        count = getattr(res, "count", None)
        if count is not None:
            return _safe_int(count, 0)

        rows = getattr(res, "data", None) or []
        return len(rows)
    except Exception:
        return 0


def _get_monthly_ai_limit(account_id: str) -> int:
    """
    For now, use a safe default or env.
    Later you can make this plan-specific.
    """
    _ = account_id
    return DEFAULT_MONTHLY_AI_LIMIT


# =========================================================
# Persistence / history
# =========================================================
def _save_history_best_effort(
    *,
    account_id: str,
    question: str,
    answer: str,
    source: str,
    provider: str,
    lang: str,
    normalized_question: Optional[str] = None,
    canonical_key: Optional[str] = None,
) -> None:
    if not account_id or not question or not answer:
        return

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "question": question,
        "answer": answer,
        "source": source,
        "provider": provider,
        "lang": lang,
    }

    if normalized_question is not None:
        payload["normalized_question"] = normalized_question
    if canonical_key is not None:
        payload["canonical_key"] = canonical_key

    try:
        _sb().table(QA_HISTORY_TABLE).insert(payload).execute()
    except Exception:
        return


# =========================================================
# Semantic matching
# =========================================================
def _semantic_search_best_effort(
    *,
    question: str,
    lang: str,
    jurisdiction: str = "nigeria",
    match_limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Calls the semantic matcher RPC.
    Assumes embedding generation happens elsewhere or in RPC pipeline.
    Boot-safe: returns [] if anything fails.
    """
    try:
        res = _sb().rpc(
            EMBED_MATCH_RPC,
            {
                "input_question": question,
                "match_limit": match_limit,
                "match_lang": lang,
                "match_jurisdiction": jurisdiction,
            },
        ).execute()

        rows = getattr(res, "data", None) or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _pick_semantic_candidate(
    *,
    question: str,
    lang: str,
    canonical_key: Optional[str],
    credits_left: int,
    semantic_rows: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    query_intent = _infer_intent_type(question, canonical_key)
    query_topic = _infer_topic(question, canonical_key)

    threshold = SEMANTIC_THRESHOLD_WITH_CREDITS if credits_left > 0 else SEMANTIC_THRESHOLD_NO_CREDITS
    min_trust = MIN_TRUST_WITH_CREDITS if credits_left > 0 else MIN_TRUST_NO_CREDITS

    debug_rows: List[Dict[str, Any]] = []

    best: Optional[Dict[str, Any]] = None

    for row in semantic_rows:
        similarity = _safe_float(
            row.get("similarity")
            or row.get("score")
            or row.get("semantic_score")
            or 0.0,
            0.0,
        )
        trust_score = _safe_float(row.get("trust_score") or 0.0, 0.0)
        candidate_intent = (row.get("intent_type") or "").strip().lower() or "general"
        candidate_topic = (row.get("topic") or "").strip().lower() or "general"
        review_status = (row.get("review_status") or "").strip().lower() or "unknown"

        intent_ok = _intent_compatible(query_intent, candidate_intent)
        topic_ok = _topic_compatible(query_topic, candidate_topic)
        threshold_ok = similarity >= threshold
        trust_ok = trust_score >= min_trust
        review_ok = review_status in {"approved", "reviewed", "trusted", "verified", "high_confidence"}

        debug_rows.append(
            {
                "cache_id": row.get("cache_id"),
                "embedding_id": row.get("id"),
                "similarity": round(similarity, 4),
                "trust_score": round(trust_score, 4),
                "intent_type": candidate_intent,
                "topic": candidate_topic,
                "review_status": review_status,
                "intent_ok": intent_ok,
                "topic_ok": topic_ok,
                "threshold_ok": threshold_ok,
                "trust_ok": trust_ok,
                "review_ok": review_ok,
            }
        )

        if not (intent_ok and topic_ok and threshold_ok and trust_ok and review_ok):
            continue

        if best is None:
            best = row
            continue

        prev_score = _safe_float(best.get("similarity") or best.get("score") or 0.0, 0.0)
        if similarity > prev_score:
            best = row

    debug = {
        "query_intent": query_intent,
        "query_topic": query_topic,
        "threshold": threshold,
        "min_trust": min_trust,
        "candidates": debug_rows,
    }
    return best, debug


def _get_answer_text_from_semantic_row(row: Dict[str, Any]) -> Optional[str]:
    for k in ("answer", "cache_answer", "matched_answer", "response_text"):
        v = (row.get(k) or "").strip()
        if v:
            return v
    return None


# =========================================================
# Main service
# =========================================================
def ask_guarded(
    *,
    account_id: str,
    question: str,
    lang: str = "en",
    channel: str = "web",
    jurisdiction: str = "nigeria",
) -> Dict[str, Any]:
    started = _now_utc()
    q = (question or "").strip()
    lang = (lang or "en").strip().lower() or "en"
    channel = (channel or "web").strip().lower() or "web"

    if not account_id:
        return {
            "ok": False,
            "error": "account_required",
            "fix": "Authenticate first before asking a question.",
        }

    if not q:
        return {
            "ok": False,
            "error": "question_required",
            "fix": "Please enter a question.",
        }

    normalized_question = normalize_question_for_cache(q)
    canonical_key = derive_canonical_key(q, lang=lang)

    debug: Dict[str, Any] = {
        "canonical_key": canonical_key,
        "normalized_question": normalized_question,
        "channel": channel,
        "lang": lang,
        "jurisdiction": jurisdiction,
    }

    cache_hit = False
    semantic_hit = False
    ai_used = False
    source = None
    ai_credit_cost = 0

    try:
        credits_left = _get_credit_balance(account_id)
        monthly_ai_usage = _get_monthly_ai_usage(account_id)
        monthly_ai_limit = _get_monthly_ai_limit(account_id)

        debug["billing"] = {
            "credits_left": credits_left,
            "monthly_ai_usage": monthly_ai_usage,
            "monthly_ai_limit": monthly_ai_limit,
        }

        # -------------------------------------------------
        # STEP 1: exact / canonical cache (always allowed)
        # -------------------------------------------------
        cache_row = answer_from_cache(q, lang=lang, canonical_key=canonical_key)
        if cache_row:
            answer_text = (cache_row.get("answer") or "").strip()
            if answer_text:
                refined = refine_answer(answer_text, lang=lang, source="cache", provider=channel) or answer_text

                increment_cache_use(cache_row.get("id"))
                _save_history_best_effort(
                    account_id=account_id,
                    question=q,
                    answer=refined,
                    source="cache",
                    provider=channel,
                    lang=lang,
                    normalized_question=normalized_question,
                    canonical_key=canonical_key,
                )

                latency_ms = int((_now_utc() - started).total_seconds() * 1000)
                log_qa_event_best_effort(
                    account_id=account_id,
                    mode=channel,
                    lang=lang,
                    question_raw=q,
                    normalized_question=normalized_question,
                    canonical_key=canonical_key,
                    outcome="ok",
                    reason=None,
                    source="cache",
                    cache_hit=True,
                    library_hit=False,
                    ai_used=False,
                    ai_credit_cost=0,
                    latency_ms=latency_ms,
                )

                debug["decision"] = "exact_cache"
                debug["cache_id"] = cache_row.get("id")

                return {
                    "ok": True,
                    "answer": refined,
                    "source": "cache",
                    "canonical_key": canonical_key,
                    "debug": debug,
                }

        # -------------------------------------------------
        # STEP 2: semantic cache
        # -------------------------------------------------
        semantic_rows = _semantic_search_best_effort(
            question=q,
            lang=lang,
            jurisdiction=jurisdiction,
            match_limit=5,
        )
        selected_semantic, semantic_debug = _pick_semantic_candidate(
            question=q,
            lang=lang,
            canonical_key=canonical_key,
            credits_left=credits_left,
            semantic_rows=semantic_rows,
        )
        debug["semantic_runtime"] = semantic_debug

        if selected_semantic:
            semantic_answer = _get_answer_text_from_semantic_row(selected_semantic)
            if semantic_answer:
                refined = refine_answer(
                    semantic_answer,
                    lang=lang,
                    source="semantic_cache",
                    provider=channel,
                ) or semantic_answer

                cache_id = selected_semantic.get("cache_id")
                if cache_id:
                    increment_cache_use(cache_id)

                _save_history_best_effort(
                    account_id=account_id,
                    question=q,
                    answer=refined,
                    source="semantic_cache",
                    provider=channel,
                    lang=lang,
                    normalized_question=normalized_question,
                    canonical_key=canonical_key,
                )

                latency_ms = int((_now_utc() - started).total_seconds() * 1000)
                log_qa_event_best_effort(
                    account_id=account_id,
                    mode=channel,
                    lang=lang,
                    question_raw=q,
                    normalized_question=normalized_question,
                    canonical_key=canonical_key,
                    outcome="ok",
                    reason=None,
                    source="semantic_cache",
                    cache_hit=False,
                    library_hit=True,
                    ai_used=False,
                    ai_credit_cost=0,
                    latency_ms=latency_ms,
                )

                debug["decision"] = "semantic_cache"
                debug["semantic_cache_id"] = selected_semantic.get("cache_id")
                debug["semantic_embedding_id"] = selected_semantic.get("id")

                return {
                    "ok": True,
                    "answer": refined,
                    "source": "semantic_cache",
                    "canonical_key": canonical_key,
                    "debug": debug,
                }

        # -------------------------------------------------
        # STEP 3: no credits -> friendly block
        # -------------------------------------------------
        if credits_left < AI_CREDIT_COST_PER_ANSWER:
            latency_ms = int((_now_utc() - started).total_seconds() * 1000)
            log_qa_event_best_effort(
                account_id=account_id,
                mode=channel,
                lang=lang,
                question_raw=q,
                normalized_question=normalized_question,
                canonical_key=canonical_key,
                outcome="blocked",
                reason="insufficient_credits",
                source=None,
                cache_hit=False,
                library_hit=False,
                ai_used=False,
                ai_credit_cost=0,
                latency_ms=latency_ms,
            )

            debug["decision"] = "blocked_insufficient_credits"

            return {
                "ok": False,
                "error": "insufficient_credits",
                "fix": (
                    "This question needs a fresh AI answer, but your current AI credits are exhausted. "
                    "Cached questions can still return answers. Please top up to continue with new questions."
                ),
                "canonical_key": canonical_key,
                "debug": debug,
            }

        # -------------------------------------------------
        # STEP 4: monthly AI cap check
        # -------------------------------------------------
        if monthly_ai_limit > 0 and monthly_ai_usage >= monthly_ai_limit:
            latency_ms = int((_now_utc() - started).total_seconds() * 1000)
            log_qa_event_best_effort(
                account_id=account_id,
                mode=channel,
                lang=lang,
                question_raw=q,
                normalized_question=normalized_question,
                canonical_key=canonical_key,
                outcome="blocked",
                reason="monthly_ai_limit_reached",
                source=None,
                cache_hit=False,
                library_hit=False,
                ai_used=False,
                ai_credit_cost=0,
                latency_ms=latency_ms,
            )

            debug["decision"] = "blocked_monthly_ai_limit"

            return {
                "ok": False,
                "error": "monthly_ai_limit_reached",
                "fix": (
                    "You have reached your monthly AI generation allowance for this billing cycle. "
                    "Cached questions can still return answers, but fresh AI generation is temporarily unavailable."
                ),
                "canonical_key": canonical_key,
                "debug": debug,
            }

        # -------------------------------------------------
        # STEP 5: fresh AI generation
        # -------------------------------------------------
        ai_res = call_ai(
            question=q,
            lang=lang,
            channel=channel,
            max_tokens=900,
        )

        if not ai_res.get("ok"):
            latency_ms = int((_now_utc() - started).total_seconds() * 1000)
            log_qa_event_best_effort(
                account_id=account_id,
                mode=channel,
                lang=lang,
                question_raw=q,
                normalized_question=normalized_question,
                canonical_key=canonical_key,
                outcome="error",
                reason=ai_res.get("error") or "ai_failed",
                source="ai",
                cache_hit=False,
                library_hit=False,
                ai_used=True,
                ai_credit_cost=0,
                latency_ms=latency_ms,
            )

            debug["decision"] = "ai_failed"
            debug["ai_error"] = ai_res

            return {
                "ok": False,
                "error": ai_res.get("error") or "ai_failed",
                "fix": ai_res.get("fix") or "We could not generate a fresh answer right now.",
                "canonical_key": canonical_key,
                "debug": debug,
            }

        ai_answer = (ai_res.get("answer") or "").strip()
        refined = refine_answer(ai_answer, lang=lang, source="ai", provider=channel) or ai_answer

        _decrement_credit_best_effort(account_id, AI_CREDIT_COST_PER_ANSWER)
        ai_credit_cost = AI_CREDIT_COST_PER_ANSWER

        _save_history_best_effort(
            account_id=account_id,
            question=q,
            answer=refined,
            source="ai",
            provider=channel,
            lang=lang,
            normalized_question=normalized_question,
            canonical_key=canonical_key,
        )

        latency_ms = int((_now_utc() - started).total_seconds() * 1000)
        log_qa_event_best_effort(
            account_id=account_id,
            mode=channel,
            lang=lang,
            question_raw=q,
            normalized_question=normalized_question,
            canonical_key=canonical_key,
            outcome="ok",
            reason=None,
            source="ai",
            cache_hit=False,
            library_hit=False,
            ai_used=True,
            ai_credit_cost=ai_credit_cost,
            latency_ms=latency_ms,
        )

        debug["decision"] = "fresh_ai"
        debug["ai_provider"] = ai_res.get("provider")
        debug["ai_model"] = ai_res.get("model")

        return {
            "ok": True,
            "answer": refined,
            "source": "ai",
            "canonical_key": canonical_key,
            "debug": debug,
        }

    except Exception as e:
        _dbg(f"[ask_guarded] unexpected_error: {type(e).__name__}: {_clip(str(e), 500)}")
        _dbg(traceback.format_exc())

        latency_ms = int((_now_utc() - started).total_seconds() * 1000)
        try:
            log_qa_event_best_effort(
                account_id=account_id,
                mode=channel,
                lang=lang,
                question_raw=q,
                normalized_question=normalized_question,
                canonical_key=canonical_key,
                outcome="error",
                reason="internal_error",
                source=None,
                cache_hit=False,
                library_hit=False,
                ai_used=False,
                ai_credit_cost=0,
                latency_ms=latency_ms,
            )
        except Exception:
            pass

        return {
            "ok": False,
            "error": "internal_error",
            "fix": "We could not complete this request right now. Please try again.",
            "canonical_key": canonical_key,
            "debug": {
                **debug,
                "exception": f"{type(e).__name__}: {_clip(str(e), 300)}",
            },
        }
