# app/routes/ask.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..services.ask_service import ask_guarded

bp = Blueprint("ask", __name__)


@bp.post("/ask")
def ask():
    """
    Unified guarded AI endpoint.

    Body:
    {
      "account_id": "<uuid>"  OR
      "provider": "wa|tg|web",
      "provider_user_id": "<id>",
      "question": "<text>",
      "lang": "en|pcm|yo|ig|ha" (optional)
    }
    """
    body = request.get_json(silent=True) or {}

    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question_required"}), 400

    try:
        resp = ask_guarded(body)  # ✅ ask_guarded now supports dict payloads
        # Business-rule failures still return 200 (frontend-friendly), except malformed input
        status = 200
        if not resp.get("ok") and resp.get("error") in {
            "invalid_request",
            "account_required",
            "question_required",
        }:
            status = 400
        return jsonify(resp), status
    except Exception:
        # Keep response safe (no stack traces / secrets)
        return jsonify({"ok": False, "error": "ask_failed"}), 500
