# app/services/ask_service.py - Self-contained version
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.ai_service import call_ai


def _sb():
    return supabase() if callable(supabase) else supabase


def _is_uuid(v: str) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except Exception:
        return False


def resolve_canonical_account_id(raw_account_id: str) -> Dict[str, Any]:
    v = (raw_account_id or "").strip()
    if not v:
        return {"ok": False, "error": "account_required"}

    if not _is_uuid(v):
        return {"ok": False, "error": "account_invalid"}

    try:
        q = _sb().table("accounts").select("id,account_id").eq("account_id", v).limit(1).execute()
        rows = getattr(q, "data", None) or []
        if rows:
            return {"ok": True, "account_id": str(rows[0].get("account_id") or v)}
    except Exception:
        pass

    try:
        q = _sb().table("accounts").select("id,account_id").eq("id", v).limit(1).execute()
        rows = getattr(q, "data", None) or []
        if rows:
            row = rows[0]
            canonical = str(row.get("account_id") or "").strip()
            if not canonical:
                canonical = v
            return {"ok": True, "account_id": canonical}
    except Exception:
        pass

    return {"ok": False, "error": "account_not_found"}


def ask_guarded(body: Dict[str, Any]) -> Dict[str, Any]:
    question = (body.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "question_required", "answer": ""}

    raw_account_id = (body.get("account_id") or "").strip()
    resolved = resolve_canonical_account_id(raw_account_id)
    if not resolved.get("ok"):
        return resolved

    account_id = str(resolved["account_id"]).strip()

    try:
        ai = call_ai(question=question, lang=(body.get("lang") or "en"), channel=(body.get("channel") or "web"))
    except Exception as e:
        return {"ok": False, "error": "ai_call_failed", "root_cause": str(e), "answer": ""}

    if not isinstance(ai, dict) or not ai.get("ok"):
        return {"ok": False, "error": "ai_failed", "answer": ""}

    return {"ok": True, "answer": ai.get("answer"), "from_cache": False, "account_id": account_id}


def process_ask_request(question: str, **kwargs) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def handle_ask_request(question: str, **kwargs) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def ask_question(question: str, **kwargs) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})


def execute_ask(question: str, **kwargs) -> Dict[str, Any]:
    return ask_guarded({"question": question, **kwargs})
