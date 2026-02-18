from __future__ import annotations

from flask import Blueprint, request, jsonify, g

from app.core.auth import require_web_auth
from app.services.ask_service import ask_guarded

bp = Blueprint("web_ask", __name__)

@bp.route("/web/ask", methods=["POST"])
def web_ask():
    ok, resp = require_web_auth()
    if not ok:
        return resp

    body = request.get_json(silent=True) or {}

    # Inject identity from web session token
    body["account_id"] = g.account_id
    body["source"] = "web"

    result, status = ask_guarded(body)
    return jsonify(result), status
