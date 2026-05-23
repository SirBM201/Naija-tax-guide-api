# app/services/qa_library_service.py
from __future__ import annotations

import re
from typing import Optional, Dict, Any, List

from app.core.supabase_client import supabase

QA_LIBRARY_SERVICE_VERSION = "2026-05-23-v2-library-compatible"


def _sb():
    return supabase() if callable(supabase) else supabase


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(value: str) -> List[str]:
    text = _normalize_text(value)
    if not text:
        return []
    return [part for part in text.split(" ") if part]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _language_answer_fields(lang: str) -> List[str]:
    code = (lang or "en").strip().lower()

    mapping = {
        "en": ["answer_en", "answer"],
        "pcm": ["answer_pcm", "answer_pidgin", "answer_en", "answer"],
        "pidgin": ["answer_pidgin", "answer_pcm", "answer_en", "answer"],
        "yo": ["answer_yo", "answer_yoruba", "answer_en", "answer"],
        "yoruba": ["answer_yoruba", "answer_yo", "answer_en", "answer"],
        "ig": ["answer_ig", "answer_igbo", "answer_en", "answer"],
        "igbo": ["answer_igbo", "answer_ig", "answer_en", "answer"],
        "ha": ["answer_ha", "answer_hausa", "answer_en", "answer"],
        "hausa": ["answer_hausa", "answer_ha", "answer_en", "answer"],
    }
    return mapping.get(code, ["answer_en", "answer"])


def _row_best_answer(row: Dict[str, Any], lang: str) -> str:
    checked: set[str] = set()
    fallback_order = _language_answer_fields(lang) + [
        "resolved_answer",
        "answer_en",
        "answer_pcm",
        "answer_yo",
        "answer_ig",
        "answer_ha",
        "answer_pidgin",
        "answer_yoruba",
        "answer_igbo",
        "answer_hausa",
        "answer",
        "response",
        "content",
        "body",
        "text",
    ]
    for key in fallback_order:
        if key in checked:
            continue
        checked.add(key)
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_terms(row: Dict[str, Any]) -> List[str]:
    terms: List[str] = []

    for key in ["question", "normalized_question", "canonical_key", "category", "source", "topic", "intent_type"]:
        value = str(row.get(key) or "").strip()
        if value:
            terms.append(value)

    tags = row.get("tags") or []
    if isinstance(tags, list):
        terms.extend(str(tag).strip() for tag in tags if str(tag).strip())

    return [_normalize_text(term) for term in terms if _normalize_text(term)]


def _row_enabled(row: Dict[str, Any]) -> bool:
    enabled = row.get("enabled")
    if enabled is not None and str(enabled).strip().lower() in {"false", "0", "no", "off"}:
        return False
    status = str(row.get("status") or row.get("review_status") or "approved").strip().lower()
    if status and status not in {"approved", "active", "published", "ok", "enabled"}:
        return False
    return True


def _score_row(
    normalized_question: str,
    canonical_key: Optional[str],
    row: Dict[str, Any],
) -> Dict[str, Any]:
    score = 0
    reasons: List[str] = []

    nq = _normalize_text(normalized_question)
    ck = _normalize_text(canonical_key or "")
    row_nq = _normalize_text(str(row.get("normalized_question") or ""))
    row_ck = _normalize_text(str(row.get("canonical_key") or ""))
    row_question = _normalize_text(str(row.get("question") or ""))
    row_terms = set(_row_terms(row))

    q_tokens = set(_tokenize(nq))
    row_tokens = set(_tokenize(" ".join(row_terms)))

    if ck and row_ck and ck == row_ck:
        score += 220
        reasons.append("canonical_key_exact:+220")

    if nq and row_nq and nq == row_nq:
        score += 200
        reasons.append("normalized_question_exact:+200")

    if nq and row_question and nq == row_question:
        score += 150
        reasons.append("question_exact:+150")

    if nq and row_nq and (nq in row_nq or row_nq in nq):
        score += 60
        reasons.append("normalized_phrase_overlap:+60")

    if ck and ck in row_terms:
        score += 50
        reasons.append("canonical_key_term_hit:+50")

    overlap = len(q_tokens.intersection(row_tokens))
    if overlap > 0:
        bonus = min(40, overlap * 8)
        score += bonus
        reasons.append(f"token_overlap:+{bonus}")

    priority = _safe_int(row.get("priority"), 0)
    if priority > 0:
        bonus = min(25, priority)
        score += bonus
        reasons.append(f"priority:+{bonus}")

    return {
        "score": score,
        "reasons": reasons,
    }


def _select_enabled_query(table: str):
    q = _sb().table(table).select("*")
    try:
        q = q.eq("enabled", True)
    except Exception:
        pass
    return q


def find_library_candidates(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    nq = _normalize_text(normalized_question)
    ck = _normalize_text(canonical_key or "")

    if not nq and not ck:
        return []

    try:
        res = _select_enabled_query("qa_library").order("priority", desc=True).limit(400).execute()
        rows = getattr(res, "data", None) or []
    except Exception:
        return []

    if not isinstance(rows, list):
        return []

    scored: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not _row_enabled(row):
            continue
        score_info = _score_row(nq, ck, row)
        if score_info["score"] <= 0:
            continue

        enriched = dict(row)
        enriched["library_score"] = score_info["score"]
        enriched["library_score_reasons"] = score_info["reasons"]
        enriched["resolved_answer"] = _row_best_answer(row, lang)
        scored.append(enriched)

    scored.sort(
        key=lambda item: (
            _safe_int(item.get("library_score"), 0),
            _safe_int(item.get("priority"), 0),
        ),
        reverse=True,
    )

    return scored[: max(1, int(limit))]


def find_library_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    nq = _normalize_text(normalized_question)
    ck = _normalize_text(canonical_key or "")

    if not nq and not ck:
        return None

    try:
        if ck:
            q = _select_enabled_query("qa_library").eq("canonical_key", ck)
            try:
                q = q.order("priority", desc=True)
            except Exception:
                pass
            res = q.limit(1).execute()
            if getattr(res, "data", None):
                row = dict(res.data[0])
                if _row_enabled(row):
                    row["resolved_answer"] = _row_best_answer(row, lang)
                    return row

        if nq:
            q = _select_enabled_query("qa_library").eq("normalized_question", nq)
            try:
                q = q.order("priority", desc=True)
            except Exception:
                pass
            res = q.limit(1).execute()
            if getattr(res, "data", None):
                row = dict(res.data[0])
                if _row_enabled(row):
                    row["resolved_answer"] = _row_best_answer(row, lang)
                    return row
    except Exception:
        pass

    candidates = find_library_candidates(
        normalized_question=nq,
        lang=lang,
        canonical_key=ck,
        limit=3,
    )
    if not candidates:
        return None

    best = candidates[0]
    if _safe_int(best.get("library_score"), 0) < 40:
        return None

    return best


def get_library_answer_by_canonical(canonical_key: str, lang: str = "en") -> Optional[Dict[str, Any]]:
    """
    Compatibility function required by app.services.qa_resolver.
    """
    ck = _normalize_text(canonical_key)
    if not ck:
        return None

    row = find_library_answer(normalized_question="", lang=lang, canonical_key=ck)
    if not row:
        return None

    answer = str(row.get("resolved_answer") or _row_best_answer(row, lang) or "").strip()
    if not answer:
        return None

    return {
        "ok": True,
        "answer": answer,
        "lang_used": lang,
        "canonical_key": row.get("canonical_key") or ck,
        "row": row,
        "source": "qa_library",
    }
