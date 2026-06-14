from __future__ import annotations

from typing import Any, Dict


ASK_RESPONSE_POLICY_PATCH_VERSION = "2026-06-14-v1"

STANDARD_NOTE = (
    "Note: This is general information for guidance only. Tax outcomes depend on the taxpayer's facts, "
    "records, current law, and the relevant tax authority. Confirm the position before filing, paying, "
    "objecting, or relying on it for a real compliance decision."
)

STRONG_NOTE = (
    "Important: This is high-risk general guidance only, not a final tax or legal opinion. Exact deadlines, "
    "penalties, enforcement steps, objections, appeals, waivers, and liabilities can depend on the facts, "
    "documents, tax year, applicable law, and the relevant tax authority. Confirm with FIRS, the State IRS, "
    "or a qualified tax professional before taking action."
)

HIGH_MARKERS = (
    "appeal", "tribunal", "court", "freeze", "bank account", "enforcement",
    "investigation", "personal liability", "personally liable", "object within",
    "30 days", "waiver", "waive", "disputed tax assessment",
)

MEDIUM_MARKERS = (
    "penalty", "late filing", "late payment", "interest", "outstanding tax",
    "withholding tax credit", "assessment", "firs", "state irs", "tax authority",
)


def _low(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_note(answer: str) -> bool:
    lower = _low(answer)
    return "general information" in lower or "guidance only" in lower or "qualified tax professional" in lower


def _risk_from_answer(answer: str) -> str:
    lower = _low(answer)
    if any(marker in lower for marker in HIGH_MARKERS):
        return "high"
    if any(marker in lower for marker in MEDIUM_MARKERS):
        return "medium"
    return "low"


def _risk_from_result(result: Dict[str, Any]) -> str:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    cache_result = meta.get("cache_result") if isinstance(meta.get("cache_result"), dict) else {}
    risk = _low(cache_result.get("risk") or meta.get("risk_level") or result.get("risk_level"))
    if risk in {"low", "medium", "high", "reject"}:
        return risk
    return _risk_from_answer(str(result.get("answer") or ""))


def _apply_note(answer: str, risk: str) -> str:
    clean = str(answer or "").strip()
    if not clean or _has_note(clean):
        return clean
    if risk == "high":
        return f"{clean}\n\n{STRONG_NOTE}"
    if risk == "medium":
        return f"{clean}\n\n{STANDARD_NOTE}"
    return clean


def _postprocess(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    answer = str(result.get("answer") or "").strip()
    if not answer:
        return result
    risk = _risk_from_result(result)
    if risk not in {"medium", "high"}:
        return result
    updated = dict(result)
    updated["answer"] = _apply_note(answer, risk)
    meta = dict(updated.get("meta") or {})
    meta["risk_level"] = risk
    meta["disclaimer_applied"] = _has_note(updated["answer"])
    updated["meta"] = meta
    return updated


def apply_ask_response_policy_patch() -> None:
    try:
        from app.services import ask_service as svc
    except Exception:
        svc = None

    if svc is not None:
        original = getattr(svc, "ask_guarded", None)
        if original is not None and not getattr(original, "_ntg_response_policy_applied", False):
            def guarded_wrapper(*args, **kwargs):
                return _postprocess(original(*args, **kwargs))
            guarded_wrapper._ntg_response_policy_applied = True
            svc.ask_guarded = guarded_wrapper

    try:
        from app.routes import ask as ask_route
    except Exception:
        ask_route = None

    if ask_route is not None:
        route_guarded = getattr(ask_route, "ask_guarded", None)
        if route_guarded is not None and not getattr(route_guarded, "_ntg_response_policy_applied", False):
            def route_guarded_wrapper(*args, **kwargs):
                return _postprocess(route_guarded(*args, **kwargs))
            route_guarded_wrapper._ntg_response_policy_applied = True
            ask_route.ask_guarded = route_guarded_wrapper

        route_sanitize = getattr(ask_route, "_sanitize_result_answer", None)
        if route_sanitize is not None and not getattr(route_sanitize, "_ntg_response_policy_applied", False):
            def route_sanitize_wrapper(result):
                return _postprocess(route_sanitize(result))
            route_sanitize_wrapper._ntg_response_policy_applied = True
            ask_route._sanitize_result_answer = route_sanitize_wrapper
