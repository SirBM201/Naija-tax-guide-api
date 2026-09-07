from __future__ import annotations

"""Fail-safe V1 integrity checks for tax-answer source metadata."""

import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _date(value: Any) -> Optional[date]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None


def assess_source_integrity(metadata: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    risk = _lower(metadata.get("risk_level"))
    sensitivity = _lower(metadata.get("source_sensitivity"))
    status = _lower(metadata.get("review_status"))
    category = _lower(metadata.get("source_category"))
    reviewed = _date(metadata.get("last_reviewed_at"))
    max_age = int(os.getenv("NTG_SOURCE_MAX_REVIEW_AGE_DAYS", "365") or 365)
    age_days = (datetime.now(timezone.utc).date() - reviewed).days if reviewed else None

    approved_statuses = {"approved", "active", "published", "ok", "enabled", "expert_reviewed"}
    unreviewed = bool(status and status not in approved_statuses)
    stale = bool(age_days is not None and age_days > max_age)
    missing_review_date = reviewed is None
    source_sensitive = sensitivity == "source_sensitive" or risk == "high"
    ai_unreviewed = category == "ai_generated" and (unreviewed or missing_review_date)

    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if source_sensitive and unreviewed:
        blocking_reasons.append("high_risk_source_not_approved")
    if source_sensitive and stale:
        blocking_reasons.append("high_risk_source_stale")
    if source_sensitive and missing_review_date and category != "ai_generated":
        blocking_reasons.append("high_risk_source_review_date_missing")
    if ai_unreviewed:
        warnings.append("ai_generated_not_expert_reviewed")
    if stale and not source_sensitive:
        warnings.append("source_review_may_be_stale")
    if missing_review_date and not source_sensitive:
        warnings.append("source_review_date_not_available")

    return {
        "ok": not blocking_reasons,
        "blocked": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "review_age_days": age_days,
        "max_review_age_days": max_age,
    }


def integrity_fallback(metadata: Dict[str, Any], assessment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": "source_integrity_review_required",
        "answer": (
            "I can’t safely present this tax answer as current because its supporting source needs review or freshness verification. "
            "Please verify the applicable rule with the relevant Nigerian tax authority or a qualified tax professional before acting on it."
        ),
        "source": "source_integrity_guard",
        "mode": "safe_escalation",
        "next_action": "Verify the current rule/effective period before relying on this answer.",
        "meta": {"source_metadata": metadata, "source_integrity": assessment, "usage_charged": False, "credits_consumed": 0},
    }
