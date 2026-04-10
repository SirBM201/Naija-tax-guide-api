from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ComposedAnswer:
    ok: bool = True
    answer: str = ""
    source: str = ""
    mode: str = ""
    message: str = ""
    error: Optional[str] = None
    fix: Optional[str] = None
    root_cause: Optional[str] = None
    details: Any = None
    references: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    debug: Dict[str, Any] = field(default_factory=dict)


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_spaces(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_duplicate_leading_labels(text: str) -> str:
    lines = text.split("\n")
    cleaned: List[str] = []

    seen_answer_label = False
    for line in lines:
        stripped = line.strip()

        if stripped.lower() == "answer:":
            if seen_answer_label:
                continue
            seen_answer_label = True
            continue

        cleaned.append(line)

    return "\n".join(cleaned).strip()


def _dedupe_repeated_sections(text: str) -> str:
    text = _normalize_spaces(text)

    # remove duplicated "What to do next:" sections, keep the first full block
    marker = "What to do next:"
    first = text.find(marker)
    if first != -1:
        second = text.find(marker, first + len(marker))
        if second != -1:
            text = text[:second].rstrip()

    # remove duplicated "What this means:" sections
    marker2 = "What this means:"
    first2 = text.find(marker2)
    if first2 != -1:
        second2 = text.find(marker2, first2 + len(marker2))
        if second2 != -1:
            text = text[:second2].rstrip()

    return text.strip()


def _looks_structured(text: str) -> bool:
    raw = _safe_str(text).lower()
    return (
        "what this means:" in raw
        or "what to do next:" in raw
        or raw.startswith("answer:")
    )


def _clean_existing_structured_answer(text: str) -> str:
    cleaned = _normalize_spaces(text)
    cleaned = _strip_duplicate_leading_labels(cleaned)
    cleaned = _dedupe_repeated_sections(cleaned)
    return cleaned.strip()


def _infer_followups(question_meta: Optional[Dict[str, Any]] = None) -> List[str]:
    meta = question_meta or {}
    topic = _safe_str(meta.get("topic")).lower()
    intent_type = _safe_str(meta.get("intent_type")).lower()

    if topic == "vat":
        return [
            "Ask what VAT means in Nigeria.",
            "Ask who must comply with VAT.",
            "Ask for the registration, filing, or payment process for VAT.",
        ]

    if topic == "tin":
        return [
            "Ask how to get a TIN as an individual or business.",
            "Ask how to verify a TIN.",
            "Ask what documents may be needed for registration.",
        ]

    if topic == "tax_clearance_certificate":
        return [
            "Ask how to apply for a TCC.",
            "Ask how to verify a TCC.",
            "Ask what a TCC is used for in practice.",
        ]

    if topic == "paye":
        return [
            "Ask who must deduct PAYE.",
            "Ask how PAYE is calculated.",
            "Ask when PAYE should be filed or paid.",
        ]

    if intent_type == "procedure":
        return [
            "Ask for the exact steps.",
            "Ask what documents are usually needed.",
            "Ask where the official application or verification portal is.",
        ]

    return [
        "Ask a more specific follow-up question.",
        "Ask for the exact steps or official process.",
        "Ask for the practical meaning in your situation.",
    ]


def _build_structured_answer(
    body: str,
    *,
    question_meta: Optional[Dict[str, Any]] = None,
    source_line: Optional[str] = None,
) -> str:
    body = _normalize_spaces(body)
    body = _strip_duplicate_leading_labels(body)
    body = _dedupe_repeated_sections(body)

    followups = _infer_followups(question_meta)

    parts: List[str] = []
    parts.append("Answer")
    parts.append("")
    parts.append(body)
    parts.append("")
    parts.append("What to do next:")
    for idx, item in enumerate(followups, start=1):
        parts.append(f"{idx}. {item}")

    if source_line:
        parts.append("")
        parts.append(source_line)

    return "\n".join(parts).strip()


def render_answer(
    answer_text: str,
    *,
    question_meta: Optional[Dict[str, Any]] = None,
    source_line: Optional[str] = None,
) -> str:
    raw = _safe_str(answer_text)
    if not raw:
        return ""

    if _looks_structured(raw):
        return _clean_existing_structured_answer(raw)

    return _build_structured_answer(
        raw,
        question_meta=question_meta,
        source_line=source_line,
    )


def looks_like_internal_or_broken_answer(answer_text: str) -> bool:
    raw = _safe_str(answer_text).lower()

    bad_patterns = [
        "internal_only",
        "debug:",
        "traceback",
        "stack trace",
        "sqlstate",
        "runtimeerror",
        "importerror",
        "worker failed to boot",
        "selected_mode",
        "question_meta",
    ]

    return any(p in raw for p in bad_patterns)


def compose_direct_cache_answer(
    row: Dict[str, Any],
    *,
    answer_text: str,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> ComposedAnswer:
    source_name = _safe_str(row.get("source") or "cache")
    rendered = render_answer(answer_text, question_meta=question_meta)

    return ComposedAnswer(
        ok=True,
        answer=rendered,
        source=source_name,
        mode="direct_cache",
        references=[],
        citations=[],
        meta={},
        debug=debug or {},
    )


def compose_rules_engine_answer(
    answer_text: str,
    *,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> ComposedAnswer:
    rendered = render_answer(answer_text, question_meta=question_meta)

    return ComposedAnswer(
        ok=True,
        answer=rendered,
        source="rules_engine",
        mode="rules_engine",
        references=[],
        citations=[],
        meta={},
        debug=debug or {},
    )


def compose_ai_answer(
    answer_text: str,
    *,
    debug: Optional[Dict[str, Any]] = None,
    question_meta: Optional[Dict[str, Any]] = None,
) -> ComposedAnswer:
    rendered = render_answer(answer_text, question_meta=question_meta)

    return ComposedAnswer(
        ok=True,
        answer=rendered,
        source="ai",
        mode="ai",
        references=[],
        citations=[],
        meta={},
        debug=debug or {},
    )


def compose_clarification(
    *,
    question_meta: Optional[Dict[str, Any]] = None,
    debug: Optional[Dict[str, Any]] = None,
) -> ComposedAnswer:
    topic = _safe_str((question_meta or {}).get("topic")).lower()

    if topic == "vat":
        body = "VAT means Value Added Tax. Ask whether you want the meaning, rate, who must charge it, or how VAT works in Nigeria."
    elif topic == "tin":
        body = "TIN means Tax Identification Number. Ask whether you want to get a TIN, verify a TIN, or know who needs one."
    elif topic == "tax_clearance_certificate":
        body = "TCC means Tax Clearance Certificate. Ask whether you want to apply for one, verify one, or understand what it is used for."
    else:
        body = "Your question is too brief. Please ask a more specific Nigerian tax question so the answer can be more useful."

    rendered = render_answer(body, question_meta=question_meta)

    return ComposedAnswer(
        ok=True,
        answer=rendered,
        source="clarification",
        mode="clarification",
        references=[],
        citations=[],
        meta={},
        debug=debug or {},
    )


def compose_insufficient_uncached(
    *,
    question_meta: Optional[Dict[str, Any]] = None,
    debug: Optional[Dict[str, Any]] = None,
) -> ComposedAnswer:
    body = (
        "Your question needs a deeper uncached answer, but no AI credits are currently available. "
        "You can still ask shorter common questions already covered in the workspace library, or add credits and try again."
    )

    rendered = render_answer(body, question_meta=question_meta)

    return ComposedAnswer(
        ok=False,
        answer=rendered,
        source="guard",
        mode="insufficient_uncached",
        error="insufficient_credits_uncached",
        message="AI credits are currently unavailable for a deeper uncached answer.",
        references=[],
        citations=[],
        meta={},
        debug=debug or {},
    )
