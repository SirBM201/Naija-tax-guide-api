# app/routes/web_ask.py
from __future__ import annotations

from flask import Blueprint, jsonify, request, g

from app.core.auth import require_auth_plus
from app.services.ask_service import ask_guarded

bp = Blueprint("web_ask", __name__)


@bp.post("/web/ask")
@require_auth_plus
def web_ask():
    """
    Web-only Ask endpoint (token protected).

    POST /api/web/ask
    Headers:
      Authorization: Bearer <token>

    Body:
      { "question": "<text>", "lang": "en|pcm|yo|ig|ha" (optional), "mode": "text|voice" (optional) }
    """
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400

    account_id = getattr(g, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    payload = {
        "account_id": account_id,
        "question": question,
        "lang": (body.get("lang") or "en").strip() or "en",
        "mode": (body.get("mode") or "text").strip().lower() or "text",
    }

    resp = ask_guarded(payload)
    return jsonify(resp), 200
