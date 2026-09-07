from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, g, jsonify, request, session

from app.services.channel_activation_service import (
    get_channel_activation_snapshot,
    select_active_channels,
)

bp = Blueprint("channel_activation", __name__)
ROUTE_VERSION = "2026-09-07-v1-channel-selection-api"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _extract_account_id(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("account_id", "id", "user_id", "auth_user_id"):
            val = value.get(key)
            if val:
                return _clean(val) or None
    return None


def _resolve_account_id() -> Optional[str]:
    for key in ("account_id", "user_id", "auth_user_id"):
        account_id = _extract_account_id(getattr(g, key, None))
        if account_id:
            return account_id
    for key in ("account_id", "user_id", "auth_user_id"):
        account_id = _extract_account_id(session.get(key))
        if account_id:
            return account_id
    try:
        from app.services import web_auth_service
        for name in ("get_account_id_from_request", "resolve_account_id_from_request", "get_current_account_id"):
            fn = getattr(web_auth_service, name, None)
            if callable(fn):
                try:
                    account_id = _extract_account_id(fn(request))
                except TypeError:
                    account_id = _extract_account_id(fn())
                if account_id:
                    return account_id
    except Exception:
        pass
    return None


def _auth_or_401():
    account_id = _resolve_account_id()
    if not account_id:
        return None, (jsonify({"ok": False, "error": "authentication_required"}), 401)
    return account_id, None


@bp.get("/channels/activation")
def activation_snapshot():
    account_id, error = _auth_or_401()
    if error:
        return error
    result = get_channel_activation_snapshot(account_id)
    result["route_version"] = ROUTE_VERSION
    return jsonify(result), 200 if result.get("ok") else 400


@bp.post("/channels/activation")
def choose_active_channels():
    account_id, error = _auth_or_401()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    channels = body.get("channels")
    if not isinstance(channels, list):
        return jsonify({"ok": False, "error": "channels_must_be_list"}), 400
    result = select_active_channels(account_id, channels)
    result["route_version"] = ROUTE_VERSION
    return jsonify(result), 200 if result.get("ok") else 400
