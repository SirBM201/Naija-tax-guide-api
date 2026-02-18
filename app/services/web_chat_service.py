# app/services/web_chat_service.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.supabase_client import supabase
from ..services.ask_service import ask_guarded

SESSION_TABLE = "web_chat_sessions"
MSG_TABLE = "web_chat_messages"

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def create_session(account_id: str, title: Optional[str] = None) -> Dict[str, Any]:
    db = supabase()
    payload = {
        "account_id": account_id,
        "title": (title or "").strip() or None,
        "updated_at": _now_iso(),
    }
    res = db.table(SESSION_TABLE).insert(payload).execute()
    row = res.data[0] if res.data else payload
    return row

def list_sessions(account_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    db = supabase()
    res = (
        db.table(SESSION_TABLE)
        .select("id,account_id,title,created_at,updated_at")
        .eq("account_id", account_id)
        .order("updated_at", desc=True)
        .limit(int(limit))
        .execute()
    )
    return res.data or []

def get_session(account_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    db = supabase()
    res = (
        db.table(SESSION_TABLE)
        .select("id,account_id,title,created_at,updated_at")
        .eq("id", session_id)
        .eq("account_id", account_id)
        .limit(1)
        .execute()
    )
    return (res.data[0] if res.data else None)

def list_messages(account_id: str, session_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    db = supabase()
    # Ensure session belongs to account
    sess = get_session(account_id, session_id)
    if not sess:
        return []

    res = (
        db.table(MSG_TABLE)
        .select("id,session_id,account_id,role,content,created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=False)
        .limit(int(limit))
        .execute()
    )
    return res.data or []

def add_message(account_id: str, session_id: str, role: str, content: str) -> Dict[str, Any]:
    db = supabase()
    payload = {
        "session_id": session_id,
        "account_id": account_id,
        "role": role,
        "content": content,
    }
    res = db.table(MSG_TABLE).insert(payload).execute()
    row = res.data[0] if res.data else payload

    # touch session updated_at
    try:
        db.table(SESSION_TABLE).update({"updated_at": _now_iso()}).eq("id", session_id).eq("account_id", account_id).execute()
    except Exception:
        pass

    return row

def chat_send(account_id: str, session_id: str, user_text: str) -> Dict[str, Any]:
    user_text = (user_text or "").strip()
    if not user_text:
        return {"ok": False, "error": "missing_message"}

    sess = get_session(account_id, session_id)
    if not sess:
        return {"ok": False, "error": "session_not_found"}

    # Store user message
    user_msg = add_message(account_id, session_id, "user", user_text)

    # Generate assistant reply (single-turn for now; history available for future use)
    ai = ask_guarded(question=user_text, account_id=account_id, mode="web_chat")

    if not ai.get("ok"):
        return {"ok": False, "error": ai.get("error") or "ask_failed"}

    assistant_text = (ai.get("answer") or "").strip() or "Sorry — I couldn’t generate a response."

    # Store assistant message
    assistant_msg = add_message(account_id, session_id, "assistant", assistant_text)

    return {
        "ok": True,
        "session": {
            "id": sess.get("id"),
            "title": sess.get("title"),
            "updated_at": _now_iso(),
        },
        "messages": [
            {
                "role": "user",
                "content": user_msg.get("content"),
                "created_at": user_msg.get("created_at"),
            },
            {
                "role": "assistant",
                "content": assistant_msg.get("content"),
                "created_at": assistant_msg.get("created_at"),
            },
        ],
        # non-sensitive hint only
        "meta": {"source": ai.get("source"), "cached": bool(ai.get("cached"))},
    }
