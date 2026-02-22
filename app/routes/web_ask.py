# app/routes/web_ask.py
from __future__ import annotations

from typing import Any, Dict
from flask import Blueprint, jsonify, request, g

from app.core.auth import require_auth_plus
from app.services.ask_service import ask_guarded

bp = Blueprint("web_ask", __name__)


@bp.post("/web/ask")
@require_auth_plus
def web_ask():
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    # ✅ Force authenticated account context
    body["account_id"] = getattr(g, "account_id", None)
    body.setdefault("provider", "web")
    body.setdefault("provider_user_id", getattr(g, "account_id", None))

    res = ask_guarded(body)

    # Keep status code predictable:
    # - 200 for ok or business-rule blocks (subscription_required, cache_limit, no_credits)
    # - 400 only for malformed requests
    status = 200
    if not res.get("ok") and res.get("error") in {"invalid_request", "account_required", "question_required"}:
        status = 400

    return jsonify(res), status
