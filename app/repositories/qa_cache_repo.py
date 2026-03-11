from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def find_exact_cache(normalized_question: str, lang: str = "en", jurisdiction: str = "nigeria") -> List[Dict[str, Any]]:
    sb = _sb()
    res = (
        sb.table("qa_cache")
        .select("*")
        .eq("normalized_question", normalized_question)
        .eq("lang", lang)
        .eq("jurisdiction", jurisdiction)
        .limit(10)
        .execute()
    )
    return res.data or []


def find_by_canonical_key(canonical_key: str, lang: str = "en", jurisdiction: str = "nigeria") -> List[Dict[str, Any]]:
    sb = _sb()
    res = (
        sb.table("qa_cache")
        .select("*")
        .eq("canonical_key", canonical_key)
        .eq("lang", lang)
        .eq("jurisdiction", jurisdiction)
        .limit(10)
        .execute()
    )
    return res.data or []


def keyword_cache_search(topic: str, intent_type: str, lang: str = "en", jurisdiction: str = "nigeria", limit: int = 10) -> List[Dict[str, Any]]:
    sb = _sb()

    q = (
        sb.table("qa_cache")
        .select("*")
        .eq("lang", lang)
        .eq("jurisdiction", jurisdiction)
        .eq("review_status", "approved")
        .limit(limit)
    )

    if topic and topic != "general":
        q = q.eq("topic", topic)

    if intent_type and intent_type != "general":
        q = q.eq("intent_type", intent_type)

    res = q.execute()
    return res.data or []


def create_history_row(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sb = _sb()
    res = sb.table("qa_history").insert(payload).select("*").execute()
    rows = res.data or []
    return rows[0] if rows else None
