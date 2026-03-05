# app/routes/ask.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request

from app.services.ask_service import ask_guarded
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("ask", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _debug_enabled() -> bool:
    return _truthy(_env("ASK_DEBUG", "0")) or _truthy(_env("DEBUG", "0"))


def _safe_json():
    return request.get_json(silent=True) or {}


def _fail(status: int, *, error: str, stage: str, hint: str = "", root_cause: str = "", debug=None, extra=None):
    payload = {"ok": False, "error": error, "stage": stage}
    if hint:
        payload["hint"] = hint
    if root_cause:
        payload["root_cause"] = root_cause
    if debug is not None:
        payload["debug"] = debug
    if extra:
        payload.update(extra)
    return jsonify(payload), status


@bp.post("/ask")
def ask():
    """
    Unified guarded AI endpoint (AUTH REQUIRED).

    Expected body:
    {
      "question": "<text>",
      "lang": "en|pcm|yo|ig|ha" (optional),
      "channel": "<optional>"
    }

    Backwards compatible:
    {
      "account_id": "<uuid>" OR
      "provider": "wa|tg|web",
      "provider_user_id": "<id>",
      "question": "<text>",
      "lang": "...",
      "channel": "..."
    }
    """
    body = _safe_json()

    question = (body.get("question") or "").strip()
    if not question:
        return _fail(
            400,
            error="question_required",
            stage="validate_input",
            hint="Send JSON: {\"question\":\"...\"}",
        )

    # If account_id not provided, derive from cookie/bearer session automatically
    if not (body.get("account_id") or "").strip():
        account_id, auth_debug = get_account_id_from_request(request)
        if not account_id:
            # This is now strict: no bypass allowed
            return _fail(
                401,
                error="unauthorized",
                stage="auth",
                hint="Login via OTP first, then call /ask with Authorization: Bearer <token> (or cookie).",
                debug=auth_debug,
            )

        body["account_id"] = account_id
        body.setdefault("provider", "web")
        body.setdefault("__auth_source", auth_debug)

    # Hard safety: explicitly ensure bypass cannot be injected from client
    body.pop("__bypass", None)

    try:
        resp = ask_guarded(body)

        # status mapping
        status = 200
        if not resp.get("ok"):
            if resp.get("error") in {"question_required", "invalid_request", "account_required", "account_invalid"}:
                status = 400
            elif resp.get("error") in {"unauthorized", "missing_token", "invalid_token", "session_expired"}:
                status = 401
            elif resp.get("error") in {"insufficient_credits"}:
                status = 402
            elif resp.get("error") in {"subscription_inactive", "payment_required"}:
                # if you later gate by subscription, use 402 or 403 depending on your policy
                status = 402
            else:
                status = 500

        # Add extra failure exposure in debug mode
        if _debug_enabled() and status >= 400:
            resp = {
                **resp,
                "_debug": {
                    "stage": "ask_guarded",
                    "has_account_id": bool(body.get("account_id")),
                    "provider": body.get("provider"),
                    "headers_present": {
                        "authorization": bool((request.headers.get("Authorization") or "").strip()),
                        "cookie": bool(request.cookies),
                    },
                },
            }

        return jsonify(resp), status

    except Exception as e:
        if _debug_enabled():
            return _fail(
                500,
                error="ask_failed",
                stage="exception",
                root_cause=f"{type(e).__name__}: {str(e)}",
                hint="Check server logs for traceback. Confirm ask_service + dependencies are deployed.",
            )

        return jsonify({"ok": False, "error": "ask_failed"}), 500
