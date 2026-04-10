from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AskExecutionResult:
    ok: bool
    answer: str
    source: str
    needs_credit: bool = False
    debug: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


_INTERNAL_HEADER_PATTERNS = [
    r"(?im)^grounded basis:\s*$",
    r"(?im)^grounding context:\s*$",
    r"(?im)^grounding summary:\s*$",
    r"(?im)^strict rules:\s*$",
    r"(?im)^question classification:\s*$",
    r"(?im)^evidence:\s*$",
    r"(?im)^debug:\s*$",
    r"(?im)^system prompt:\s*$",
    r"(?im)^prompt:\s*$",
    r"(?im)^reasoning:\s*$",
    r"(?im)^analysis:\s*$",
    r"(?im)^final answer draft:\s*$",
]

_INTERNAL_FIELD_LINE_PATTERNS = [
    r"(?im)^-?\s*topic:\s*.*$",
    r"(?im)^-?\s*intent_type:\s*.*$",
    r"(?im)^-?\s*jurisdiction:\s*.*$",
    r"(?im)^-?\s*complexity:\s*.*$",
    r"(?im)^-?\s*risk_level:\s*.*$",
    r"(?im)^-?\s*trust_score:\s*.*$",
    r"(?im)^-?\s*similarity:\s*.*$",
    r"(?im)^-?\s*match_type:\s*.*$",
    r"(?im)^-?\s*authority_score:\s*.*$",
    r"(?im)^-?\s*source_authority_score:\s*.*$",
    r"(?im)^-?\s*rank_score:\s*.*$",
    r"(?im)^-?\s*review_status:\s*.*$",
    r"(?im)^-?\s*grounded:\s*.*$",
    r"(?im)^-?\s*grounding_mode:\s*.*$",
    r"(?im)^-?\s*confidence:\s*.*$",
    r"(?im)^-?\s*normalized_question:\s*.*$",
    r"(?im)^-?\s*canonical_key:\s*.*$",
    r"(?im)^source id:\s*.*$",
    r"(?im)^source title:\s*.*$",
    r"(?im)^chunk id:\s*.*$",
]

_PROVIDER_ERROR_PATTERNS = [
    r"incorrect api key provided",
    r"invalid_api_key",
    r"sk-proj-",
    r"status:\s*401",
    r"error code:\s*401",
    r"invalid_request_error",
    r"\bapi key\b",
]

_CLEAR_INTERNAL_MARKERS = [
    "candidate 1",
    "candidate 2",
    "candidate 3",
    "grounded basis",
    "grounding context",
    "grounding summary",
    "strict rules",
    "question classification",
    "you are answering as",
    "best supported answer",
    "based on the strongest available",
    "no evidence provided",
    "prompt:",
    "system prompt",
    "reasoning:",
    "analysis:",
    "trust_score",
    "similarity",
    "match_type",
]

_SOURCE_PREFIX_RE = re.compile(r"(?im)^source:\s*")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MULTI_SPACE_RE = re.compile(r"\s+")
_SHORT_QUESTION_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9\s/\-?]{0,20}\??$")


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _candidate_meta(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return {
            "candidate_id": candidate.get("candidate_id"),
            "canonical_key": candidate.get("canonical_key"),
            "topic": candidate.get("topic"),
            "intent_type": candidate.get("intent_type"),
            "jurisdiction": candidate.get("jurisdiction"),
            "lang": candidate.get("lang"),
            "trust_score": candidate.get("trust_score"),
            "source_authority_score": candidate.get("source_authority_score"),
            "similarity": candidate.get("similarity"),
            "match_type": candidate.get("match_type"),
            "rank_score": candidate.get("rank_score"),
            "review_status": candidate.get("review_status"),
            "source_title": candidate.get("source_title"),
        }

    return {
        "candidate_id": getattr(candidate, "candidate_id", None),
        "canonical_key": getattr(candidate, "canonical_key", None),
        "topic": getattr(candidate, "topic", None),
        "intent_type": getattr(candidate, "intent_type", None),
        "jurisdiction": getattr(candidate, "jurisdiction", None),
        "lang": getattr(candidate, "lang", None),
        "trust_score": getattr(candidate, "trust_score", None),
        "source_authority_score": getattr(candidate, "source_authority_score", None),
        "similarity": getattr(candidate, "similarity", None),
        "match_type": getattr(candidate, "match_type", None),
        "rank_score": getattr(candidate, "rank_score", None),
        "review_status": getattr(candidate, "review_status", None),
    }


def _normalize_inline(text: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", _safe_str(text)).strip()


def _clean_lines(text: str) -> List[str]:
    raw = _safe_str(text)
    if not raw:
        return []

    lines = [ln.rstrip() for ln in raw.splitlines()]
    cleaned: List[str] = []
    blank_streak = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_streak += 1
            if blank_streak <= 1:
                cleaned.append("")
            continue

        blank_streak = 0
        cleaned.append(stripped)

    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()

    return cleaned


def _ensure_sentence(text: str) -> str:
    text = _normalize_inline(text)
    if not text:
        return ""
    if text.endswith((".", "!", "?", ":")):
        return text
    return text + "."


def _extract_source_tail(lines: List[str]) -> Tuple[List[str], Optional[str]]:
    if not lines:
        return lines, None

    last = lines[-1].strip()
    if _SOURCE_PREFIX_RE.match(last):
        return lines[:-1], last

    return lines, None


def _remove_known_internal_sections(text: str) -> str:
    cleaned = _safe_str(text)
    if not cleaned:
        return ""

    for pattern in _INTERNAL_HEADER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    for pattern in _INTERNAL_FIELD_LINE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned)

    return cleaned.strip()


def _sanitize_answer_text(text: str) -> str:
    cleaned = _remove_known_internal_sections(text)
    if not cleaned:
        return ""

    lines = _clean_lines(cleaned)
    filtered: List[str] = []

    for line in lines:
        lower = line.lower().strip()

        if any(re.search(pattern, lower, flags=re.I) for pattern in _PROVIDER_ERROR_PATTERNS):
            continue

        if lower.startswith("candidate ") and lower.endswith(":"):
            continue

        if lower in {
            "grounded basis:",
            "grounding context:",
            "grounding summary:",
            "strict rules:",
            "question classification:",
            "evidence:",
            "debug:",
            "system prompt:",
            "prompt:",
            "reasoning:",
            "analysis:",
            "final answer draft:",
        }:
            continue

        filtered.append(line)

    return "\n".join(_clean_lines("\n".join(filtered))).strip()


def _count_internal_markers(text: str) -> int:
    raw = _safe_str(text).lower()
    if not raw:
        return 0

    count = 0
    for marker in _CLEAR_INTERNAL_MARKERS:
        if marker in raw:
            count += 1
    return count


def _has_provider_error(text: str) -> bool:
    raw = _safe_str(text)
    if not raw:
        return False
    return any(re.search(pattern, raw, flags=re.I) for pattern in _PROVIDER_ERROR_PATTERNS)


def looks_like_internal_or_broken_answer(text: str) -> bool:
    raw = _safe_str(text)
    if not raw:
        return True

    lower = raw.lower()

    if _has_provider_error(raw):
        return True

    marker_count = _count_internal_markers(lower)
    if marker_count >= 2:
        return True

    if "candidate 1" in lower and "candidate 2" in lower:
        return True

    if "trust_score" in lower and "similarity" in lower:
        return True

    if "grounding context" in lower and "strict rules" in lower:
        return True

    return False


def _split_intro_and_steps(lines: List[str]) -> Tuple[List[str], List[str]]:
    intro: List[str] = []
    steps: List[str] = []

    for line in lines:
        stripped = line.strip()
        if re.match(r"^\d+[.)]\s+", stripped):
            steps.append(stripped)
        elif steps and stripped:
            steps.append(stripped)
        else:
            intro.append(stripped)

    return intro, steps


def _fallback_unknown() -> str:
    return (
        "I do not yet have enough reliable guidance in the system to answer that accurately.\n\n"
        "What to do next:\n"
        "1. Ask the question in a more specific way.\n"
        "2. Mention the tax type, person or business type, and the period involved.\n"
        "3. If helpful, ask for the meaning, process, rate, exemption, or penalty directly."
    )


def _extract_source_line(lines: List[str]) -> Tuple[List[str], Optional[str]]:
    stripped_lines = [ln for ln in lines if ln.strip()]
    if not stripped_lines:
        return [], None

    kept: List[str] = []
    source_line: Optional[str] = None

    for line in stripped_lines:
        if _SOURCE_PREFIX_RE.match(line.strip()):
            source_line = line.strip()
        else:
            kept.append(line.strip())

    return kept, source_line


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for line in lines:
        key = _normalize_inline(line).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(line.strip())
    return result


def _looks_like_short_question(question_meta: Optional[Dict[str, Any]]) -> bool:
    normalized_question = _safe_str((question_meta or {}).get("normalized_question"))
    if not normalized_question:
        return False
    return bool(_SHORT_QUESTION_RE.match(normalized_question))


def _topic_label(question_meta: Optional[Dict[str, Any]]) -> str:
    topic = _safe_str((question_meta or {}).get("topic")).replace("_", " ")
    if not topic or topic == "general":
        return "this tax issue"
    return topic.upper() if topic.lower() in {"vat", "paye", "tin"} else topic


def _user_type_hint(question_meta: Optional[Dict[str, Any]]) -> str:
    topic = _safe_str((question_meta or {}).get("topic")).lower()
    if topic in {"vat", "value_added_tax"}:
        return "registered businesses or suppliers of taxable goods and services"
    if topic in {"paye", "personal_income_tax"}:
        return "employees and employers"
    if topic in {"withholding_tax"}:
        return "businesses making qualifying payments"
    if topic in {"freelancer", "self_employed"}:
        return "freelancers, consultants, and self-employed persons"
    return "the person or business involved"


def _build_plain_definition(lines: List[str], question_meta: Optional[Dict[str, Any]]) -> str:
    lines, source_line = _extract_source_line(lines)
    lines = _dedupe_lines(lines)

    if not lines:
        return _fallback_unknown()

    lead = _ensure_sentence(lines[0])
    detail_lines = lines[1:3]

    parts: List[str] = []
    parts.append(f"Answer:\n{lead}")

    meaning_block = []
    if detail_lines:
        meaning_block.extend(_ensure_sentence(x) for x in detail_lines[:2] if x.strip())

    if _looks_like_short_question(question_meta):
        topic = _topic_label(question_meta)
        meaning_block.append(
            _ensure_sentence(
                f"If you want a more exact answer, ask what {topic} means, who it applies to, or how to comply with it in Nigeria"
            )
        )

    if meaning_block:
        parts.append("What this means:\n" + "\n".join(meaning_block))

    if source_line:
        parts.append(source_line)

    return "\n\n".join(parts).strip()


def _build_plain_procedure(lines: List[str], question_meta: Optional[Dict[str, Any]]) -> str:
    lines, source_line = _extract_source_line(lines)
    lines = _dedupe_lines(lines)

    if not lines:
        return _fallback_unknown()

    intro, steps = _split_intro_and_steps(lines)

    lead = _ensure_sentence(intro[0] if intro else lines[0])
    parts: List[str] = [f"Answer:\n{lead}"]

    if len(intro) > 1:
        parts.append("What this means:\n" + "\n".join(_ensure_sentence(x) for x in intro[1:3]))

    if steps:
        rendered_steps = []
        for idx, step in enumerate(steps, start=1):
            clean = re.sub(r"^\d+[.)]\s+", "", step).strip()
            rendered_steps.append(f"{idx}. {_ensure_sentence(clean)}")
        parts.append("What to do next:\n" + "\n".join(rendered_steps[:6]))
    elif len(lines) > 1:
        rendered_steps = []
        for idx, line in enumerate(lines[1:5], start=1):
            rendered_steps.append(f"{idx}. {_ensure_sentence(line)}")
        parts.append("What to do next:\n" + "\n".join(rendered_steps))

    if source_line:
        parts.append(source_line)

    return "\n\n".join(parts).strip()


def _build_plain_obligation(lines: List[str], question_meta: Optional[Dict[str, Any]]) -> str:
    lines, source_line = _extract_source_line(lines)
    lines = _dedupe_lines(lines)

    if not lines:
        return _fallback_unknown()

    lead = _ensure_sentence(lines[0])
    meaning = [_ensure_sentence(x) for x in lines[1:3] if x.strip()]
    topic_hint = _topic_label(question_meta)
    user_hint = _user_type_hint(question_meta)

    parts: List[str] = [f"Answer:\n{lead}"]

    if meaning:
        parts.append("What this means:\n" + "\n".join(meaning))
    else:
        parts.append(
            "What this means:\n"
            + _ensure_sentence(f"This depends on whether {user_hint} is covered by the relevant rules for {topic_hint}")
        )

    next_steps = [
        f"1. Confirm whether you are asking as {user_hint}.",
        f"2. Confirm the exact tax type involved under {topic_hint}.",
        "3. Ask a narrower follow-up if you want the process, rate, or penalty.",
    ]
    parts.append("What to do next:\n" + "\n".join(next_steps))

    if source_line:
        parts.append(source_line)

    return "\n\n".join(parts).strip()


def _build_plain_calculation(lines: List[str], question_meta: Optional[Dict[str, Any]]) -> str:
    lines, source_line = _extract_source_line(lines)
    lines = _dedupe_lines(lines)

    if not lines:
        return _fallback_unknown()

    lead = _ensure_sentence(lines[0])
    parts: List[str] = [f"Answer:\n{lead}"]

    detail_lines = [_ensure_sentence(x) for x in lines[1:4] if x.strip()]
    if detail_lines:
        parts.append("What this means:\n" + "\n".join(detail_lines))

    next_steps = [
        "1. Confirm the amount, period, and tax type involved.",
        "2. Confirm whether any exemption, deduction, or special rate applies.",
        "3. Ask for a worked example if you want the calculation broken down.",
    ]
    parts.append("What to do next:\n" + "\n".join(next_steps))

    if source_line:
        parts.append(source_line)

    return "\n\n".join(parts).strip()


def _build_plain_general(lines: List[str], question_meta: Optional[Dict[str, Any]]) -> str:
    lines, source_line = _extract_source_line(lines)
    lines = _dedupe_lines(lines)

    if not lines:
        return _fallback_unknown()

    lead = _ensure_sentence(lines[0])
    detail_lines = [_ensure_sentence(x) for x in lines[1:4] if x.strip()]

    parts: List[str] = [f"Answer:\n{lead}"]

    if detail_lines:
        parts.append("What this means:\n" + "\n".join(detail_lines))

    if _looks_like_short_question(question_meta):
        topic = _topic_label(question_meta)
        next_steps = [
            f"1. Ask what {topic} means in Nigeria.",
            f"2. Ask who must comply with {topic}.",
            f"3. Ask for the registration, filing, or payment process for {topic}.",
        ]
        parts.append("What to do next:\n" + "\n".join(next_steps))

    if source_line:
        parts.append(source_line)

    return "\n\n".join(parts).strip()


def render_answer(answer_text: str, *, question_meta: Optional[Dict[str, Any]] = None) -> str:
    intent_type = _safe_str((question_meta or {}).get("intent_type")).lower()
    topic = _safe_str((question_meta or {}).get("topic")).lower()

    raw = _safe_str(answer_text)
    sanitized = _sanitize_answer_text(raw)

    if not sanitized:
        return _fallback_unknown()

    if looks_like_internal_or_broken_answer(raw) and not sanitized:
        return _fallback_unknown()

    lines = _clean_lines(sanitized)
    if not lines:
        return _fallback_unknown()

    if looks_like_internal_or_broken_answer(raw) and len(lines) <= 1:
        return _fallback_unknown()

    if intent_type == "definition":
        return _build_plain_definition(lines, question_meta)

    if intent_type in {"procedure", "how_to"}:
        return _build_plain_procedure(lines, question_meta)

    if intent_type in {"obligation", "eligibility"}:
        return _build_plain_obligation(lines, question_meta)

    if intent_type in {"calculation", "computation", "deduction"}:
        return _build_plain_calculation(lines, question_meta)

    if topic in {"penalty", "rate", "deadline"}:
        return _build_plain_calculation(lines, question_meta)

    return _build_plain_general(lines, question_meta)


def compose_direct_cache_answer(
    candidate: Any,
    *,
    answer_text: Optional[str] = None,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> AskExecutionResult:
    raw_answer = _safe_str(
        answer_text or (candidate.get("answer") if isinstance(candidate, dict) else getattr(candidate, "answer", ""))
    )
    rendered = render_answer(raw_answer, question_meta=question_meta)

    return AskExecutionResult(
        ok=True,
        answer=rendered,
        source="cache",
        needs_credit=False,
        debug=debug or {},
        meta={
            "mode": "direct_cache",
            "candidate": _candidate_meta(candidate),
            "question_meta": question_meta or {},
        },
    )


def compose_ai_answer(
    answer_text: str,
    *,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> AskExecutionResult:
    rendered = render_answer(_safe_str(answer_text), question_meta=question_meta)

    return AskExecutionResult(
        ok=True,
        answer=rendered,
        source="ai",
        needs_credit=False,
        debug=debug or {},
        meta={
            "mode": "grounded_synthesis",
            "question_meta": question_meta or {},
        },
    )


def compose_clarification(
    *,
    question_meta: Optional[Dict[str, Any]] = None,
    debug: Optional[Dict[str, Any]] = None,
) -> AskExecutionResult:
    topic = _safe_str((question_meta or {}).get("topic")).replace("_", " ") or "tax issue"
    return AskExecutionResult(
        ok=True,
        answer=(
            "Answer:\n"
            "I need a little more detail before I answer this safely.\n\n"
            "What to clarify:\n"
            f"1. Are you asking about {topic} as an employee, freelancer, sole proprietor, or company?\n"
            "2. Do you want the meaning, process, whether it applies, or the rate or penalty?\n"
            "3. If this is about filing or payment, which tax type and period are involved?"
        ),
        source="clarification",
        needs_credit=False,
        debug=debug or {},
        meta={
            "mode": "clarification",
            "question_meta": question_meta or {},
        },
    )


def compose_insufficient_uncached(
    *,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> AskExecutionResult:
    return AskExecutionResult(
        ok=False,
        answer="",
        source="none",
        needs_credit=True,
        error="insufficient_credits_uncached",
        debug=debug or {},
        meta={
            "mode": "insufficient_credits_uncached",
            "question_meta": question_meta or {},
        },
    )


def compose_rules_engine_answer(
    answer_text: str,
    *,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> AskExecutionResult:
    rendered = render_answer(_safe_str(answer_text), question_meta=question_meta)

    return AskExecutionResult(
        ok=True,
        answer=rendered,
        source="rules_engine",
        needs_credit=False,
        debug=debug or {},
        meta={
            "mode": "rules_engine",
            "question_meta": question_meta or {},
        },
    )
