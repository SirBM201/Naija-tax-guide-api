# app/routes/web_chat.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..core.auth import require_auth_plus
from ..services.web_chat_service import (
    create_session,
    list_sessions,
    list_messages,
    get_session,
    chat_send,
)

bp = Blueprint("web_chat", __name__)

def _json() -> dict:
    try:
        return request.get_json(force=True) or {}
    except Exception:
        return {}

@bp.post("/web/chat/sessions")
@require_auth_plus
def create_chat_session():
    account_id = getattr(request, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 401

    data = _json()
    title = (data.get("title") or "").strip() or None
    sess = create_session(account_id=account_id, title=title)
    return jsonify({"ok": True, "session": sess})

@bp.get("/web/chat/sessions")
@require_auth_plus
def list_chat_sessions():
    account_id = getattr(request, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 401

    limit = int((request.args.get("limit") or "50").strip() or "50")
    rows = list_sessions(account_id=account_id, limit=limit)
    return jsonify({"ok": True, "sessions": rows})

@bp.get("/web/chat/sessions/<session_id>")
@require_auth_plus
def get_chat_session(session_id: str):
    account_id = getattr(request, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 401

    sess = get_session(account_id=account_id, session_id=session_id)
    if not sess:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    msgs = list_messages(account_id=account_id, session_id=session_id, limit=200)
    return jsonify({"ok": True, "session": sess, "messages": msgs})

@bp.post("/web/chat/sessions/<session_id>/messages")
@require_auth_plus
def post_chat_message(session_id: str):
    account_id = getattr(request, "account_id", None)
    if not account_id:
        return jsonify({"ok": False, "error": "missing_account_id"}), 401

    data = _json()
    text = (data.get("message") or "").strip()
    out = chat_send(account_id=account_id, session_id=session_id, user_text=text)
    status = 200 if out.get("ok") else 400
    return jsonify(out), status
