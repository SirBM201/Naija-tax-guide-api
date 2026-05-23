# app/core/response_safety.py
from __future__ import annotations

import copy
import os
from typing import Any, Mapping, Optional

try:
    from flask import Request
except Exception:  # pragma: no cover
    Request = Any  # type: ignore


TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}

# Keys that are useful during development but should not be exposed to normal
# public/browser responses because they can reveal internals, token metadata,
# database call details, fingerprints, or raw third-party rows.
SENSITIVE_DEBUG_KEYS = {
    "debug",
    "root_cause",
    "details",
    "trace",
    "traceback",
    "exception",
    "stack",
    "stacktrace",
    "web_token_debug",
    "token_debug",
    "token_row",
    "fingerprint",
    "request_fingerprint",
    "stored_fingerprint",
    "rotation",
    "supabase",
    "headers",
    "authorization",
    "cookie",
    "cookies",
    "raw",
    "raw_response",
    "raw_result",
    "non_fatal_errors",
    "non_fatal_lookup_errors",
    "web_token_lookup_errors",
    "flask_session_user_keys",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy_env(name: str) -> bool:
    return _clean(os.getenv(name)).lower() in TRUE_VALUES


def debug_response_allowed(request_obj: Optional[Request] = None) -> bool:
    """
    Decide whether full debug information may be returned in a response.

    Default: do not expose debug data.

    To enable temporarily:
      Option A: set NTG_EXPOSE_DEBUG_RESPONSES=true in the backend env.
      Option B: set NTG_DEBUG_RESPONSE_KEY in env, then send:
                ?debug=1&debug_key=<same key>
             or header:
                X-NTG-Debug-Key: <same key>
    """
    if _truthy_env("NTG_EXPOSE_DEBUG_RESPONSES"):
        return True

    expected_key = _clean(os.getenv("NTG_DEBUG_RESPONSE_KEY") or os.getenv("DEBUG_RESPONSE_KEY"))
    if not expected_key or request_obj is None:
        return False

    debug_flag = _clean(request_obj.args.get("debug") if hasattr(request_obj, "args") else "").lower()
    if debug_flag not in TRUE_VALUES:
        return False

    supplied_key = _clean(
        (request_obj.headers.get("X-NTG-Debug-Key") if hasattr(request_obj, "headers") else "")
        or (request_obj.args.get("debug_key") if hasattr(request_obj, "args") else "")
    )
    return bool(supplied_key and supplied_key == expected_key)


def sanitize_response_payload(payload: Any, request_obj: Optional[Request] = None) -> Any:
    """
    Return a public-safe version of a response payload.

    If debug responses are enabled, the payload is returned unchanged.
    Otherwise, known debug/internal keys are removed recursively.
    """
    if debug_response_allowed(request_obj):
        return payload

    return _sanitize(copy.deepcopy(payload))


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in SENSITIVE_DEBUG_KEYS:
                continue
            cleaned[key_text] = _sanitize(item)
        return cleaned

    if isinstance(value, list):
        return [_sanitize(item) for item in value]

    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]

    return value


def production_debug_note() -> dict[str, Any]:
    """
    Small safe diagnostic note that can be returned publicly.
    It confirms that debug filtering is active without exposing internals.
    """
    return {
        "debug_filtered": not _truthy_env("NTG_EXPOSE_DEBUG_RESPONSES"),
        "debug_mode_env": _truthy_env("NTG_EXPOSE_DEBUG_RESPONSES"),
        "debug_key_configured": bool(_clean(os.getenv("NTG_DEBUG_RESPONSE_KEY") or os.getenv("DEBUG_RESPONSE_KEY"))),
    }
