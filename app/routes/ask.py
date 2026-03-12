from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.ask_service import ask_guarded
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("ask", __name__)


def _safe_json():
    return request.get_json(silent=True) or {}


@bp.post("/ask")
def ask():
    body = _safe_json()

    question = str(body.get("question") or "").strip()
    lang = str(body.get("lang") or "en").strip() or "en"
    channel = str(body.get("channel") or "web").strip() or "web"

    if not question:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "question_required",
                    "message": "Please enter a question.",
                }
            ),
            400,
        )

    try:
        account_id = get_account_id_from_request(request)
    except Exception as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "auth_resolution_failed",
                    "message": "Could not resolve authenticated account.",
                    "details": str(e),
                }
            ),
            401,
        )

    if not account_id:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "unauthorized",
                    "message": "Authentication required.",
                }
            ),
            401,
        )

    try:
        result = ask_guarded(
            account_id=account_id,
            question=question,
            lang=lang,
            channel=channel,
        )
    except Exception as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "internal_error",
                    "message": "We could not complete your request right now.",
                    "details": str(e),
                }
            ),
            500,
        )

    if not isinstance(result, dict):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "invalid_service_response",
                    "message": "Ask service returned an invalid response.",
                }
            ),
            500,
        )

    if result.get("ok") is True:
        return jsonify(result), 200

    error = str(result.get("error") or "").strip().lower()

    if error in {"insufficient_credits", "insufficient_credits_uncached"}:
        return jsonify(result), 402

    if error in {"unauthorized", "auth_resolution_failed", "account_required"}:
        return jsonify(result), 401

    if error in {"question_required"}:
        return jsonify(result), 400

    return jsonify(result), 200
