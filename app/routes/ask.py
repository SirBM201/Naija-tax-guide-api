# app/routes/ask.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.core import config as CFG
from app.services.ask_service import ask_guarded
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("ask", __name__)


def _get_bearer_token() -> str:
    h = (request.headers.get("Authorization") or "").strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return ""


def _is_dev_bypass_request() -> bool:
    """
    Bypass is allowed ONLY when:
      - server config allows it (CFG.ALLOW_SUBSCRIPTION_BYPASS)
      - AND request provides correct bypass token (Authorization Bearer or X-Auth-Token)

    This prevents accidental open bypass when someone sets DEV_BYPASS_SUBSCRIPTION=1.
    """
    if not CFG.ALLOW_SUBSCRIPTION_BYPASS:
        return False

    expected = (CFG.BYPASS_TOKEN or CFG.DEV_BYPASS_TOKEN or "").strip()
    if not expected:
        return False

    bearer = _get_bearer_token()
    x_token = (request.headers.get("X-Auth-Token") or "").strip()
    return bearer == expected or x_token == expected


@bp.post("/ask")
def ask():
    body = request.get_json(silent=True) or {}

    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question_required"}), 400

    # Dev bypass support (token-protected)
    if _is_dev_bypass_request():
        body["__bypass"] = True

    # If account_id not provided, derive from cookie/bearer session automatically
    if not (body.get("account_id") or "").strip():
        account_id, source = get_account_id_from_request(request)
        if account_id:
            body["account_id"] = account_id
            body.setdefault("provider", "web")
            body.setdefault("__auth_source", source)

    try:
        resp = ask_guarded(body)

        status = 200
        if not resp.get("ok"):
            if resp.get("error") in {"question_required", "invalid_request", "account_required", "account_invalid"}:
                status = 400
            elif resp.get("error") in {"unauthorized", "missing_token", "invalid_token", "session_expired"}:
                status = 401
            elif resp.get("error") in {"insufficient_credits"}:
                status = 402
            else:
                status = 500

        return jsonify(resp), status

    except Exception as e:
        # Always expose root cause for server-side crash here (helps you debug fast)
        return jsonify(
            {
                "ok": False,
                "error": "ask_failed",
                "root_cause": f"{type(e).__name__}: {str(e)}",
                "fix": "Check server logs for full traceback. Confirm ask_service, credits_service, ai_service are healthy.",
            }
        ), 500
