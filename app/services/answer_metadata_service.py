# app/services/answer_metadata_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.ai_service import classify_source_sensitivity, classify_tax_safety_risk
from app.services.tax_source_catalog import get_source_category

ANSWER_METADATA_SERVICE_VERSION = "2026-07-03-v1-source-date-risk-metadata"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _first_value(row: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _date_only(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except Exception:
        return raw[:10]


def infer_source_category(row: Optional[Dict[str, Any]] = None, *, source_kind: str = "", question: str = "") -> str:
    row = row or {}
    explicit = _lower(
        row.get("source_category")
        or row.get("source_category_code")
        or row.get("source_code")
        or row.get("source_type_code")
    )
    if explicit:
        return explicit

    source = _lower(source_kind or row.get("source") or row.get("source_kind"))
    topic = _lower(row.get("topic") or row.get("category") or row.get("intent_type") or question)

    if source.startswith("ai"):
        return "ai_generated"
    if "state" in topic or "paye" in topic or "personal income" in topic or "residence" in topic:
        return "state_authority_practice"
    if "vat" in topic or "company income" in topic or "cit" in topic or "firs" in topic or "federal" in topic:
        return "federal_authority_guidance"
    if source in {"library", "qa_library", "database", "cache", "qa_cache"}:
        return "reviewed_internal_answer"
    return "reviewed_internal_answer"


def infer_risk_level(row: Optional[Dict[str, Any]] = None, *, question: str = "") -> str:
    row = row or {}
    explicit = _lower(row.get("risk_level") or row.get("answer_risk") or row.get("review_risk"))
    if explicit in {"low", "medium", "high"}:
        return explicit

    route = classify_tax_safety_risk(question)
    if route in {"refuse", "escalate"}:
        return "high"

    if classify_source_sensitivity(question) == "source_sensitive":
        return "high"

    return "medium"


def build_source_metadata(
    *,
    row: Optional[Dict[str, Any]] = None,
    source_kind: str = "",
    table: str = "",
    question: str = "",
    mode: str = "",
    ai_model: str = "",
) -> Dict[str, Any]:
    row = row or {}
    source_category = infer_source_category(row, source_kind=source_kind, question=question)
    catalog = get_source_category(source_category)
    source_label = _first_value(row, "source_label", "source_name", "authority", "reference_title")
    if not source_label and catalog:
        source_label = catalog.label
    if not source_label:
        source_label = "AI-generated guidance" if source_category == "ai_generated" else "Reviewed internal guidance"

    review_status = _lower(row.get("review_status") or row.get("status"))
    if source_category == "ai_generated" and not review_status:
        review_status = "ai_generated_unreviewed"
    elif not review_status:
        review_status = "approved" if row else "not_reviewed"

    last_reviewed = _date_only(
        row.get("last_reviewed_at")
        or row.get("reviewed_at")
        or row.get("last_checked_at")
        or row.get("updated_at")
    )

    tax_year = _first_value(row, "tax_year", "year", "fiscal_year")
    jurisdiction = _first_value(row, "jurisdiction", "state", "country") or "Nigeria"
    source_url = _first_value(row, "source_url", "reference_url", "url")
    risk_level = infer_risk_level(row, question=question)
    route = classify_tax_safety_risk(question)
    source_sensitivity = classify_source_sensitivity(question)

    metadata: Dict[str, Any] = {
        "version": ANSWER_METADATA_SERVICE_VERSION,
        "source_kind": source_kind or row.get("source") or "answer",
        "source_category": source_category,
        "source_label": source_label,
        "source_type": catalog.source_type if catalog else ("internal_review" if source_category != "ai_generated" else "internal_review"),
        "risk_level": risk_level,
        "review_status": review_status,
        "last_reviewed_at": last_reviewed or None,
        "tax_year": tax_year or None,
        "jurisdiction": jurisdiction,
        "source_url": source_url or None,
        "table": table or None,
        "mode": mode or None,
        "ai_model": ai_model or None,
        "safety_route": route,
        "source_sensitivity": source_sensitivity,
        "requires_professional_review": route == "escalate" or risk_level == "high" and source_sensitivity == "source_sensitive",
    }

    if catalog:
        metadata["review_rule"] = catalog.review_rule

    return {k: v for k, v in metadata.items() if v not in ("", None)}


def source_metadata_note(metadata: Dict[str, Any]) -> str:
    if not isinstance(metadata, dict) or not metadata:
        return ""

    parts: list[str] = []
    label = _clean(metadata.get("source_label"))
    category = _clean(metadata.get("source_category"))
    risk = _clean(metadata.get("risk_level"))
    reviewed = _clean(metadata.get("last_reviewed_at"))
    jurisdiction = _clean(metadata.get("jurisdiction"))
    tax_year = _clean(metadata.get("tax_year"))
    status = _clean(metadata.get("review_status"))

    if label:
        parts.append(f"Source basis: {label}")
    elif category:
        parts.append(f"Source category: {category.replace('_', ' ').title()}")

    if reviewed:
        parts.append(f"Last reviewed: {reviewed}")
    elif status and "unreviewed" in status:
        parts.append("Review status: AI-generated and not yet expert-reviewed")
    else:
        parts.append("Last reviewed: not shown")

    if jurisdiction:
        parts.append(f"Jurisdiction: {jurisdiction}")
    if tax_year:
        parts.append(f"Tax year: {tax_year}")
    if risk:
        parts.append(f"Risk level: {risk}")

    if not parts:
        return ""

    return "Source details: " + " | ".join(parts) + "."


def append_source_metadata_note(answer: str, metadata: Dict[str, Any]) -> str:
    text = _clean(answer)
    if not text:
        return text
    if "Source details:" in text:
        return text
    note = source_metadata_note(metadata)
    if not note:
        return text
    return f"{text}\n\n{note}"


def public_metadata_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    source_metadata = meta.get("source_metadata") if isinstance(meta.get("source_metadata"), dict) else {}
    if source_metadata:
        return source_metadata
    return build_source_metadata(
        source_kind=_clean(result.get("source") or result.get("mode") or "answer"),
        mode=_clean(result.get("mode")),
    )
