# app/services/ask_service.py
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.supabase_client import supabase
from app.services.ai_service import call_ai, last_ai_error
from app.services.credit_usage_service import (
    check_credit_access,
    deduct_credits,
    get_credit_balance,
    is_manual_precharged_action,
)

try:
    from app.services.qa_library_service import find_library_answer
except Exception:  # pragma: no cover
    find_library_answer = None  # type: ignore

ASK_SERVICE_VERSION = "2026-05-23-v6-cache-library-paid-ai-no-double-debit"


def _sb():
    return supabase() if callable(supabase) else supabase


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _clip(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _debug_enabled() -> bool:
    return _truthy(os.getenv("DEBUG_AI")) or _truthy(os.getenv("SHOW_ASK_DEBUG")) or _truthy(os.getenv("ASK_DEBUG"))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _normalize_question(question: str) -> str:
    text = _lower(question)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonical_key(question: str) -> str:
    return re.sub(r"\s+", "_", _normalize_question(question))[:180]


def _hash_ref(*parts: str) -> str:
    raw = "|".join([_clean(p) for p in parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _query_rows(
    table: str,
    select_cols: str = "*",
    *,
    limit: int = 10,
    **eq_filters: Any,
) -> Tuple[list[dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        res = q.limit(limit).execute()
        rows = getattr(res, "data", None) or []
        return [r for r in rows if isinstance(r, dict)], None
    except Exception as exc:
        return [], f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_insert(table: str, payload: Dict[str, Any]) -> Optional[str]:
    try:
        _sb().table(table).insert(payload).execute()
        return None
    except Exception as exc:
        return f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _answer_from_row(row: Dict[str, Any]) -> str:
    for key in (
        "resolved_answer",
        "answer",
        "answer_en",
        "response",
        "content",
        "body",
        "text",
    ):
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _row_review_ok(row: Dict[str, Any]) -> bool:
    status = _lower(row.get("review_status") or row.get("status") or "approved")
    if status and status not in {"approved", "active", "published", "ok", "enabled"}:
        return False

    enabled = row.get("enabled")
    if enabled is not None and str(enabled).strip().lower() in {"false", "0", "no", "off"}:
        return False

    try:
        score = float(row.get("trust_score") if row.get("trust_score") is not None else 1.0)
    except Exception:
        score = 1.0

    return score >= 0.5


def _strip_markdown_noise(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""

    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"^\s*[-*_]{2,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*More details\s*:?\s*$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_professional_answer_shape(answer: str, question: str = "") -> str:
    answer = _strip_markdown_noise(answer)
    if not answer:
        return ""

    lowered = answer.lower()
    has_direct = "direct answer:" in lowered or "short answer:" in lowered
    has_key = "key points:" in lowered or "key point:" in lowered
    has_action = "what to do:" in lowered or "next steps:" in lowered

    # WhatsApp Q5 asks for a short explanation; do not wrap it heavily.
    q = _lower(question)
    if "quiz explanation" in q or "maximum 90 words" in q:
        return answer

    if has_direct and (has_key or has_action):
        return answer

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", answer) if p.strip()]
    if not paragraphs:
        return answer

    first = paragraphs[0]
    rest = paragraphs[1:]
    rebuilt = [f"Direct answer: {first}"]

    if rest:
        rebuilt.append("Key points:")
        for idx, item in enumerate(rest[:5], start=1):
            clean_item = re.sub(r"^\s*(?:\d+[\.)]|•)\s*", "", item).strip()
            rebuilt.append(f"{idx}. {clean_item}")

    return "\n\n".join(rebuilt).strip()


def _find_database_answer(question: str, lang: str = "en") -> Dict[str, Any]:
    normalized = _normalize_question(question)
    canonical = _canonical_key(question)
    errors: list[str] = []

    # 1. Current qa_cache exact matches.
    for filters in (
        {"normalized_question": normalized, "lang": lang, "jurisdiction": "nigeria"},
        {"canonical_key": canonical, "lang": lang, "jurisdiction": "nigeria"},
        {"normalized_question": normalized},
        {"canonical_key": canonical},
    ):
        rows, err = _query_rows("qa_cache", "*", limit=10, **filters)
        if err:
            errors.append(err)
            continue
        for row in rows:
            answer = _answer_from_row(row)
            if answer and _row_review_ok(row):
                return {
                    "ok": True,
                    "found": True,
                    "answer": _ensure_professional_answer_shape(answer, question),
                    "source": "database",
                    "mode": "direct_cache",
                    "table": "qa_cache",
                    "row": row,
                    "normalized_question": normalized,
                    "canonical_key": canonical,
                }

    # 2. Library table support, if service exists and table is present.
    if find_library_answer is not None:
        try:
            lib_row = find_library_answer(normalized_question=normalized, lang=lang, canonical_key=canonical)
            if isinstance(lib_row, dict):
                answer = _answer_from_row(lib_row)
                if answer:
                    return {
                        "ok": True,
                        "found": True,
                        "answer": _ensure_professional_answer_shape(answer, question),
                        "source": "library",
                        "mode": "library_match",
                        "table": "qa_library",
                        "row": lib_row,
                        "normalized_question": normalized,
                        "canonical_key": canonical,
                    }
        except Exception as exc:
            errors.append(f"qa_library: {type(exc).__name__}: {_clip(exc)}")

    # 3. Small broad qa_cache match. This avoids expensive full-text calls.
    tokens = [t for t in normalized.split() if len(t) >= 4][:5]
    try:
        q = _sb().table("qa_cache").select("*").limit(30)
        try:
            q = q.eq("lang", lang).eq("jurisdiction", "nigeria")
        except Exception:
            pass
        res = q.execute()
        rows = getattr(res, "data", None) or []
        best: Optional[Dict[str, Any]] = None
        best_score = 0
        for row in rows:
            if not isinstance(row, dict) or not _row_review_ok(row):
                continue
            haystack = " ".join(
                [
                    _lower(row.get("question")),
                    _lower(row.get("normalized_question")),
                    _lower(row.get("canonical_key")),
                    _lower(row.get("topic")),
                    _lower(row.get("intent_type")),
                ]
            )
            score = sum(1 for t in tokens if t in haystack)
            if score > best_score:
                best = row
                best_score = score

        if best and best_score >= 2:
            answer = _answer_from_row(best)
            if answer:
                return {
                    "ok": True,
                    "found": True,
                    "answer": _ensure_professional_answer_shape(answer, question),
                    "source": "database",
                    "mode": "direct_cache",
                    "table": "qa_cache",
                    "row": best,
                    "match_score": best_score,
                    "normalized_question": normalized,
                    "canonical_key": canonical,
                }
    except Exception as exc:
        errors.append(f"qa_cache.broad: {type(exc).__name__}: {_clip(exc)}")

    return {
        "ok": True,
        "found": False,
        "source": "database",
        "mode": "no_cache",
        "errors": errors[:8],
        "normalized_question": normalized,
        "canonical_key": canonical,
    }


def _save_ai_answer_to_cache(
    *,
    question: str,
    answer: str,
    lang: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = _normalize_question(question)
    canonical = _canonical_key(question)
    clean_answer = _ensure_professional_answer_shape(answer, question)

    primary_payload = {
        "normalized_question": normalized,
        "canonical_key": canonical,
        "answer": clean_answer,
        "tags": ["ai", "naija-tax-guide"],
        "source": "ai",
        "enabled": True,
        "priority": 20,
        "lang": lang or "en",
        "intent_type": metadata.get("intent_type") or "general",
        "topic": metadata.get("topic") or "general",
        "trust_score": 0.75,
        "review_status": "approved",
        "jurisdiction": "nigeria",
        "last_used_at": _now_iso(),
    }

    minimal_payload = {
        "normalized_question": normalized,
        "answer": clean_answer,
        "source": "ai",
        "lang": lang or "en",
    }

    errors: list[str] = []
    existing_rows, existing_err = _query_rows("qa_cache", "*", limit=1, normalized_question=normalized)
    if existing_rows:
        return {
            "ok": True,
            "table": "qa_cache",
            "mode": "already_exists",
            "id": existing_rows[0].get("id"),
        }
    if existing_err:
        errors.append(existing_err)

    for payload in (primary_payload, minimal_payload):
        err = _safe_insert("qa_cache", payload)
        if not err:
            return {
                "ok": True,
                "table": "qa_cache",
                "schema_mode": "production_qa_cache",
                "normalized_question": normalized,
                "canonical_key": canonical,
            }
        errors.append(err)

    return {
        "ok": False,
        "error": "cache_insert_failed",
        "errors": errors[:4],
    }


def _free_plan_response_no_ai(question: str, lang: str, channel: str, database_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": "paid_plan_required",
        "message": (
            "I could not find a database/library answer for this question. "
            "AI-powered tax answers require an active paid plan with Usage Credits."
        ),
        "answer": (
            "I could not find a database/library answer for this question yet.\n\n"
            "AI-powered tax answers require an active paid plan with Usage Credits. "
            "Free users can still use basic calculators, database/library answers, and non-AI quiz attempts."
        ),
        "mode": "free_database_only_no_match",
        "source": "database",
        "meta": {
            "question": question,
            "lang": lang,
            "channel": channel,
            "database_result": {
                "found": database_result.get("found"),
                "normalized_question": database_result.get("normalized_question"),
                "canonical_key": database_result.get("canonical_key"),
            },
            "usage_charged": False,
            "credits_consumed": 0,
        },
    }


def _call_ai_answer(question: str, lang: str = "en", channel: str = "web", **kwargs: Any) -> Dict[str, Any]:
    try:
        ai = call_ai(question=question, lang=lang, channel=channel, **kwargs)
        if isinstance(ai, dict) and ai.get("ok") and _clean(ai.get("answer")):
            return {
                "ok": True,
                "answer": _ensure_professional_answer_shape(_clean(ai.get("answer")), question),
                "provider": ai.get("provider") or "openai",
                "model": ai.get("model") or os.getenv("OPENAI_MODEL") or os.getenv("AI_MODEL") or "gpt-4o-mini",
            }
        return {
            "ok": False,
            "error": (ai or {}).get("error") if isinstance(ai, dict) else "ai_failed",
            "root_cause": (ai or {}).get("root_cause") if isinstance(ai, dict) else last_ai_error(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "ai_exception",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
        }


def ask_guarded(
    payload: Optional[Dict[str, Any]] = None,
    *,
    account_id: str = "",
    question: str = "",
    query: str = "",
    text: str = "",
    message: str = "",
    user_message: str = "",
    user_query: str = "",
    lang: str = "en",
    channel: str = "web",
    provider: Optional[str] = None,
    provider_user_id: Optional[str] = None,
    action_code: str = "ai_tax_answer",
    **extra: Any,
) -> Dict[str, Any]:
    """
    Guarded tax Ask service.

    Policy:
      - Database/cache/library answers: free, no credit deduction.
      - AI fallback: active paid plan required.
      - Normal AI answer: deducts 1 Usage Credit after AI answer succeeds.
      - WhatsApp Q5 manual-credit action: does NOT deduct again because
        whatsapp.py v17 already debits before calling this service.
    """
    if isinstance(payload, dict):
        account_id = _clean(payload.get("account_id") or account_id)
        question = _clean(payload.get("question") or question)
        query = _clean(payload.get("query") or query)
        text = _clean(payload.get("text") or text)
        message = _clean(payload.get("message") or message)
        user_message = _clean(payload.get("user_message") or user_message)
        user_query = _clean(payload.get("user_query") or user_query)
        lang = _clean(payload.get("lang") or lang)
        channel = _clean(payload.get("channel") or channel)
        provider = _clean(payload.get("provider") or provider or "")
        provider_user_id = _clean(payload.get("provider_user_id") or provider_user_id or "")
        action_code = _clean(payload.get("action_code") or action_code)
        extra = {**payload, **extra}

    account_id = _clean(account_id)
    question_text = _clean(question or query or text or message or user_message or user_query)
    lang = _lower(lang) or "en"
    channel = _lower(channel or provider or "web") or "web"
    provider = _lower(provider or channel) or channel
    action_code = _lower(action_code) or "ai_tax_answer"
    already_charged = bool(extra.get("__credit_already_deducted") or is_manual_precharged_action(action_code))

    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "message": "A signed-in account is required.",
        }

    if not question_text:
        return {
            "ok": False,
            "error": "missing_question",
            "message": "Question is required.",
        }

    # 1. Free layer: database/library/cache.
    database_result = _find_database_answer(question_text, lang=lang)
    if database_result.get("found") and database_result.get("answer"):
        access = check_credit_access(
            account_id=account_id,
            action_code=action_code,
            source_kind=database_result.get("source") or "database",
            channel=channel,
            already_charged=already_charged,
        )
        return {
            "ok": True,
            "answer": _ensure_professional_answer_shape(str(database_result["answer"]), question_text),
            "source": database_result.get("source") or "database",
            "mode": database_result.get("mode") or "direct_cache",
            "meta": {
                "account_id": account_id,
                "channel": channel,
                "provider": provider,
                "provider_user_id": provider_user_id,
                "plan_code": access.get("plan_code"),
                "source_kind": database_result.get("source") or "database",
                "usage_charged": False,
                "credits_consumed": 0,
                "credit_cost": 0,
                "credits_left": access.get("balance"),
                "credit_balance": access.get("balance"),
                "normalized_question": database_result.get("normalized_question"),
                "canonical_key": database_result.get("canonical_key"),
                "cache_table": database_result.get("table"),
            },
            "debug": database_result if _debug_enabled() else None,
        }

    # 2. Paid AI fallback access check before calling OpenAI.
    precheck = check_credit_access(
        account_id=account_id,
        action_code=action_code,
        source_kind="ai",
        channel=channel,
        already_charged=already_charged,
    )

    if not precheck.get("allowed"):
        if precheck.get("reason") == "paid_plan_required":
            return _free_plan_response_no_ai(question_text, lang, channel, database_result)

        return {
            "ok": False,
            "error": precheck.get("error") or "credit_access_denied",
            "message": precheck.get("message") or "This action cannot be completed with the current credit state.",
            "answer": precheck.get("message") or "This action cannot be completed with the current credit state.",
            "mode": "credit_blocked",
            "source": "credit_guard",
            "meta": {
                **precheck,
                "database_result": {
                    "found": database_result.get("found"),
                    "normalized_question": database_result.get("normalized_question"),
                    "canonical_key": database_result.get("canonical_key"),
                },
                "usage_charged": False,
                "credits_consumed": 0,
            },
        }

    # 3. AI call. No user-visible answer is returned until credit state is valid.
    ai_result = _call_ai_answer(
        question_text,
        lang=lang,
        channel=channel,
        max_words=extra.get("max_words"),
        max_output_tokens=extra.get("max_output_tokens"),
    )
    if not ai_result.get("ok"):
        return {
            "ok": False,
            "error": ai_result.get("error") or "ai_answer_failed",
            "message": "AI answer could not be generated right now.",
            "answer": "AI answer could not be generated right now. Please try again shortly.",
            "mode": "ai_failed_before_charge" if not already_charged else "ai_failed_after_manual_charge",
            "source": "ai",
            "meta": {
                "account_id": account_id,
                "channel": channel,
                "provider": provider,
                "provider_user_id": provider_user_id,
                "usage_charged": bool(already_charged),
                "credits_consumed": 0,
                "credit_access": precheck,
            },
            "debug": ai_result if _debug_enabled() else None,
        }

    answer = _ensure_professional_answer_shape(_clean(ai_result.get("answer")), question_text)

    # 4. Deduct after successful AI only, except manual-precharged actions.
    reference = f"ASK-{_hash_ref(account_id, channel, question_text, _now_iso())}"
    deduction = deduct_credits(
        account_id=account_id,
        action_code=action_code,
        source_kind="ai",
        channel=channel,
        description="AI tax answer" if not already_charged else "AI tax answer already charged by caller",
        reference=reference,
        already_charged=already_charged,
        metadata={
            "question": question_text[:400],
            "provider": provider,
            "provider_user_id": provider_user_id,
            "lang": lang,
            "model": ai_result.get("model"),
            "already_charged": already_charged,
        },
    )

    if not deduction.get("ok") or (not already_charged and precheck.get("charge") and not deduction.get("deducted")):
        return {
            "ok": False,
            "error": deduction.get("error") or "credit_deduction_failed",
            "message": deduction.get("message") or "Credit deduction failed before the answer could be delivered.",
            "answer": "Credit deduction failed before the answer could be delivered. Please refresh credits and try again.",
            "mode": "credit_deduction_failed_after_ai",
            "source": "credit_guard",
            "meta": {
                "account_id": account_id,
                "channel": channel,
                "usage_charged": False,
                "credits_consumed": 0,
                "deduction": deduction,
            },
            "debug": {"ai_answer_generated_but_not_released": True} if _debug_enabled() else None,
        }

    cache_result = _save_ai_answer_to_cache(
        question=question_text,
        answer=answer,
        lang=lang,
        metadata={
            "account_id": account_id,
            "channel": channel,
            "provider": provider,
            "model": ai_result.get("model"),
            "reference": reference,
        },
    )

    current_balance = get_credit_balance(account_id)
    credits_consumed = 0 if already_charged else int(deduction.get("credits_deducted") or 0)
    usage_charged = True if already_charged else bool(credits_consumed > 0)

    return {
        "ok": True,
        "answer": answer,
        "source": "ai",
        "mode": "ai_grounded_manual_precharged" if already_charged else "ai_grounded",
        "meta": {
            "account_id": account_id,
            "channel": channel,
            "provider": provider,
            "provider_user_id": provider_user_id,
            "plan_code": deduction.get("plan_code") or precheck.get("plan_code"),
            "source_kind": "ai",
            "usage_charged": usage_charged,
            "manual_precharged": already_charged,
            "credits_consumed": credits_consumed,
            "credit_cost": 0 if already_charged else int(deduction.get("credit_cost") or 0),
            "credits_left_before": deduction.get("balance_before"),
            "credits_left": deduction.get("balance_after", current_balance.get("balance")),
            "credit_balance": deduction.get("balance_after", current_balance.get("balance")),
            "reference": reference,
            "cache_result": cache_result,
        },
        "debug": {
            "precheck": precheck,
            "deduction": deduction,
            "ai": {k: v for k, v in ai_result.items() if k != "answer"},
            "database_result": database_result,
        } if _debug_enabled() else None,
    }


# Backward-compatible wrappers expected by older route files.
def process_ask_request(question: str, **kwargs: Any) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def handle_ask_request(question: str, **kwargs: Any) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def ask_question(question: str, **kwargs: Any) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def execute_ask(question: str, **kwargs: Any) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})
