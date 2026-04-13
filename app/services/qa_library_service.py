from __future__ import annotations

import re
from typing import Optional, Dict, Any, List

from app.core.supabase_client import supabase


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


def _language_answer_field(lang: str) -> str:
    code = (lang or "en").strip().lower()

    mapping = {
        "en": "answer_en",
        "pcm": "answer_pcm",
        "pidgin": "answer_pidgin",
        "yo": "answer_yo",
        "yoruba": "answer_yoruba",
        "ig": "answer_ig",
        "igbo": "answer_igbo",
        "ha": "answer_ha",
        "hausa": "answer_hausa",
    }
    return mapping.get(code, "answer_en")


def _row_best_answer(row: Dict[str, Any], lang: str) -> str:
    preferred_field = _language_answer_field(lang)
    preferred = str(row.get(preferred_field) or "").strip()
    if preferred:
        return preferred

    fallback_order = [
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
    ]
    for key in fallback_order:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_terms(row: Dict[str, Any]) -> List[str]:
    terms: List[str] = []

    for key in ["question", "normalized_question", "canonical_key", "category", "source"]:
        value = str(row.get(key) or "").strip()
        if value:
            terms.append(value)

    tags = row.get("tags") or []
    if isinstance(tags, list):
        terms.extend(str(tag).strip() for tag in tags if str(tag).strip())

    return [_normalize_text(term) for term in terms if _normalize_text(term)]


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
        score += 200
        reasons.append("canonical_key_exact:+200")

    if nq and row_nq and nq == row_nq:
        score += 180
        reasons.append("normalized_question_exact:+180")

    if nq and row_question and nq == row_question:
        score += 120
        reasons.append("question_exact:+120")

    if nq and row_nq and (nq in row_nq or row_nq in nq):
        score += 50
        reasons.append("normalized_phrase_overlap:+50")

    if ck and ck in row_terms:
        score += 40
        reasons.append("canonical_key_term_hit:+40")

    overlap = len(q_tokens.intersection(row_tokens))
    if overlap > 0:
        bonus = min(30, overlap * 6)
        score += bonus
        reasons.append(f"token_overlap:+{bonus}")

    priority = _safe_int(row.get("priority"), 0)
    if priority > 0:
        bonus = min(20, priority)
        score += bonus
        reasons.append(f"priority:+{bonus}")

    return {
        "score": score,
        "reasons": reasons,
    }


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
        res = (
            _sb()
            .table("qa_library")
            .select("*")
            .eq("enabled", True)
            .order("priority", desc=True)
            .limit(400)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception:
        return []

    if not isinstance(rows, list):
        return []

    scored: List[Dict[str, Any]] = []
    for row in rows:
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
            res = (
                _sb()
                .table("qa_library")
                .select("*")
                .eq("enabled", True)
                .eq("canonical_key", ck)
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None):
                row = dict(res.data[0])
                row["resolved_answer"] = _row_best_answer(row, lang)
                return row

        if nq:
            res = (
                _sb()
                .table("qa_library")
                .select("*")
                .eq("enabled", True)
                .eq("normalized_question", nq)
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None):
                row = dict(res.data[0])
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
