from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.services.qa_history_service import (
    clear_history_items,
    delete_history_item,
    get_history_item,
    history_summary,
    history_table_ready,
    list_history_items,
)
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("history", __name__)


def _safe_int(value: Any, default: int = 50) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _extract_account_id(auth_result: Any) -> Tuple[Optional[str], Dict[str, Any]]:
    if isinstance(auth_result, str):
        account_id = auth_result.strip()
        return (account_id or None, {})

    if isinstance(auth_result, tuple):
        first = auth_result[0] if len(auth_result) > 0 else None
        second = auth_result[1] if len(auth_result) > 1 and isinstance(auth_result[1], dict) else {}

        if isinstance(first, str):
            account_id = first.strip()
            return (account_id or None, second)

        return (None, {"error": "invalid_auth_tuple", "raw": repr(auth_result)})

    if isinstance(auth_result, dict):
        account_id = str(auth_result.get("account_id") or "").strip()
        return (account_id or None, dict(auth_result))

    return (None, {"error": "unsupported_auth_result", "raw_type": str(type(auth_result))})


def _get_authenticated_account() -> Tuple[Optional[str], Dict[str, Any]]:
    auth_raw = get_account_id_from_request(request)
    return _extract_account_id(auth_raw)


def _unauthorized(auth_debug: Dict[str, Any]):
    return jsonify({"ok": False, "error": "unauthorized", "message": "Authentication required.", "debug": auth_debug}), 401


@bp.get("/history/health")
def history_health():
    return jsonify({"ok": True, "route_group": "history", "storage": history_table_ready()}), 200


@bp.get("/history")
def get_history():
    account_id, auth_debug = _get_authenticated_account()
    if not account_id:
        return _unauthorized(auth_debug)

    limit = _safe_int(request.args.get("limit"), 50)
    source = _safe_text(request.args.get("source"))
    channel = _safe_text(request.args.get("channel"))
    query = _safe_text(request.args.get("q"))

    result = list_history_items(
        account_id=account_id,
        limit=limit,
        source=source,
        channel=channel,
        query=query,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "history_table_missing" else 400
        return jsonify(result), status

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "items": result.get("items", []),
            "count": result.get("count", 0),
            "debug": {"auth": auth_debug},
        }
    ), 200


@bp.get("/history/summary")
def get_history_summary():
    account_id, auth_debug = _get_authenticated_account()
    if not account_id:
        return _unauthorized(auth_debug)

    source = _safe_text(request.args.get("source"))
    channel = _safe_text(request.args.get("channel"))
    query = _safe_text(request.args.get("q"))

    result = history_summary(
        account_id=account_id,
        source=source,
        channel=channel,
        query=query,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "history_table_missing" else 400
        return jsonify(result), status

    result["debug"] = {"auth": auth_debug}
    return jsonify(result), 200


@bp.get("/history/<item_id>")
def get_history_detail(item_id: str):
    account_id, auth_debug = _get_authenticated_account()
    if not account_id:
        return _unauthorized(auth_debug)

    result = get_history_item(account_id=account_id, item_id=item_id)
    if not result.get("ok"):
        status = 404 if result.get("error") in {"history_table_missing", "history_item_not_found"} else 400
        return jsonify(result), status

    result["debug"] = {"auth": auth_debug}
    return jsonify(result), 200


@bp.delete("/history/<item_id>")
def delete_history_detail(item_id: str):
    account_id, auth_debug = _get_authenticated_account()
    if not account_id:
        return _unauthorized(auth_debug)

    result = delete_history_item(account_id=account_id, item_id=item_id)
    if not result.get("ok"):
        status = 404 if result.get("error") in {"history_table_missing", "history_item_not_found"} else 400
        return jsonify(result), status

    result["debug"] = {"auth": auth_debug}
    return jsonify(result), 200


@bp.route("/history/clear", methods=["DELETE", "POST"], strict_slashes=False)
def clear_history():
    account_id, auth_debug = _get_authenticated_account()
    if not account_id:
        return _unauthorized(auth_debug)

    body = request.get_json(silent=True) or {}
    source = _safe_text(request.args.get("source") or body.get("source"))
    channel = _safe_text(request.args.get("channel") or body.get("channel"))

    result = clear_history_items(
        account_id=account_id,
        source=source,
        channel=channel,
    )
    if not result.get("ok"):
        status = 404 if result.get("error") == "history_table_missing" else 400
        return jsonify(result), status

    result["debug"] = {"auth": auth_debug}
    return jsonify(result), 200
