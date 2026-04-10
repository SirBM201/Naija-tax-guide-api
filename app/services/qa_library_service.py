from __future__ import annotations

import re
from typing import Optional, Dict, Any, List, Tuple

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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _language_candidates(lang: str) -> List[str]:
    raw = (lang or "en").strip().lower() or "en"
    ordered = [raw]
    if raw != "en":
        ordered.append("en")
    return ordered


def _fetch_enabled_rows(lang: str, limit: int = 400) -> List[Dict[str, Any]]:
    languages = _language_candidates(lang)
    rows: List[Dict[str, Any]] = []

    try:
        for code in languages:
            res = (
                _sb()
                .table("qa_library")
                .select("*")
                .eq("enabled", True)
                .eq("lang", code)
                .order("priority", desc=True)
                .limit(limit)
                .execute()
            )
            data = getattr(res, "data", None) or []
            if isinstance(data, list):
                rows.extend(data)
    except Exception:
        return []

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        row_id = row.get("id") or row.get("canonical_key") or row.get("normalized_question")
        if row_id in seen:
            continue
        seen.add(row_id)
        deduped.append(row)
    return deduped


def _row_text_blob(row: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(row.get("question") or ""),
            str(row.get("normalized_question") or ""),
            str(row.get("canonical_key") or ""),
            str(row.get("topic") or ""),
            str(row.get("intent_type") or ""),
            str(row.get("answer") or ""),
            str(row.get("keywords") or ""),
            str(row.get("aliases") or ""),
        ]
    ).strip()


def _extract_terms(row: Dict[str, Any]) -> List[str]:
    values: List[str] = []

    for key in ["normalized_question", "canonical_key", "topic", "intent_type"]:
        raw = str(row.get(key) or "").strip()
        if raw:
            values.append(raw)

    aliases = row.get("aliases")
    if isinstance(aliases, list):
        values.extend(str(x).strip() for x in aliases if str(x).strip())
    elif isinstance(aliases, str):
        values.extend(part.strip() for part in aliases.split(",") if part.strip())

    keywords = row.get("keywords")
    if isinstance(keywords, list):
        values.extend(str(x).strip() for x in keywords if str(x).strip())
    elif isinstance(keywords, str):
        values.extend(part.strip() for part in keywords.split(",") if part.strip())

    question = str(row.get("question") or "").strip()
    if question:
        values.append(question)

    return [_normalize_text(v) for v in values if _normalize_text(v)]


def _score_row(
    normalized_question: str,
    canonical_key: Optional[str],
    row: Dict[str, Any],
) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    nq = _normalize_text(normalized_question)
    ck = _normalize_text(canonical_key or "")
    row_nq = _normalize_text(str(row.get("normalized_question") or ""))
    row_ck = _normalize_text(str(row.get("canonical_key") or ""))

    q_tokens = set(_tokenize(nq))
    row_blob = _normalize_text(_row_text_blob(row))
    row_tokens = set(_tokenize(row_blob))
    row_terms = set(_extract_terms(row))

    if ck and row_ck and ck == row_ck:
        score += 200
        reasons.append("canonical_key_exact:+200")

    if nq and row_nq and nq == row_nq:
        score += 180
        reasons.append("normalized_question_exact:+180")

    if nq and row_nq:
        if nq in row_nq or row_nq in nq:
            score += 70
            reasons.append("normalized_phrase_match:+70")

    if ck and ck in row_terms:
        score += 50
        reasons.append("canonical_term_match:+50")

    token_overlap = len(q_tokens.intersection(row_tokens))
    if token_overlap > 0:
        overlap_bonus = min(45, token_overlap * 8)
        score += overlap_bonus
        reasons.append(f"token_overlap:+{overlap_bonus}")

    exact_term_hits = 0
    for term in row_terms:
        if term and term in nq:
            exact_term_hits += 1
    if exact_term_hits > 0:
        term_bonus = min(36, exact_term_hits * 6)
        score += term_bonus
        reasons.append(f"term_hits:+{term_bonus}")

    priority = _safe_int(row.get("priority"), 0)
    if priority > 0:
        priority_bonus = min(20, priority)
        score += priority_bonus
        reasons.append(f"priority:+{priority_bonus}")

    quality_flags = [
        bool(str(row.get("answer") or "").strip()),
        bool(str(row.get("question") or "").strip()),
        bool(row.get("enabled")),
    ]
    quality_bonus = sum(2 for flag in quality_flags if flag)
    if quality_bonus:
        score += quality_bonus
        reasons.append(f"quality:+{quality_bonus}")

    return score, reasons


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

    rows = _fetch_enabled_rows(lang=lang, limit=400)
    if not rows:
        return []

    scored: List[Dict[str, Any]] = []
    for row in rows:
        score, reasons = _score_row(nq, ck, row)
        if score <= 0:
            continue

        enriched = dict(row)
        enriched["library_score"] = round(score, 3)
        enriched["library_score_reasons"] = reasons
        scored.append(enriched)

    scored.sort(
        key=lambda item: (
            _safe_float(item.get("library_score"), 0.0),
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
                .in_("lang", _language_candidates(lang))
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None):
                return res.data[0]

        if nq:
            res = (
                _sb()
                .table("qa_library")
                .select("*")
                .eq("enabled", True)
                .eq("normalized_question", nq)
                .in_("lang", _language_candidates(lang))
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None):
                return res.data[0]
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
    best_score = _safe_float(best.get("library_score"), 0.0)

    if best_score < 40:
        return None

    return best
