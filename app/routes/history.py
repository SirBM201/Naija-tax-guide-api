from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from app.services.web_auth_service import get_account_id_from_request
from app.services.qa_history_service import list_history_items, history_table_ready

bp = Blueprint("history", __name__)


def _safe_int(v: Any, default: int = 50) -> int:
    try:
        return int(v)
    except Exception:
        return default


@bp.get("/history/health")
def history_health():
    return jsonify({"ok": True, "route_group": "history", "storage": history_table_ready()}), 200


@bp.get("/history")
def get_history():
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    limit = _safe_int(request.args.get("limit"), 50)
    source = (request.args.get("source") or "").strip().lower() or None
    query = (request.args.get("q") or "").strip() or None

    result = list_history_items(
        account_id=account_id,
        limit=limit,
        source=source,
        query=query,
    )

    if not result.get("ok"):
        status = 404 if result.get("error") == "history_table_missing" else 400
        return jsonify(result), status

    return (
        jsonify(
            {
                "ok": True,
                "account_id": account_id,
                "items": result.get("items", []),
                "count": result.get("count", 0),
                "debug": {"auth": auth_debug},
            }
        ),
        200,
    )
