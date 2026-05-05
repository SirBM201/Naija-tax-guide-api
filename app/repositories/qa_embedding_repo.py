from __future__ import annotations

from typing import Any, Dict, List

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def semantic_search(
    input_question: str,
    *,
    lang: str = "en",
    jurisdiction: str = "nigeria",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    sb = _sb()

    # Uses your new SQL function: public.match_qa_embeddings(...)
    try:
        res = sb.rpc(
            "match_qa_embeddings",
            {
                "input_question": input_question,
                "match_limit": limit,
                "match_lang": lang,
                "match_jurisdiction": jurisdiction,
            },
        ).execute()
        return res.data or []
    except Exception:
        return []


def semantic_search_v2(
    query_embedding: List[float],
    *,
    lang: str = "en",
    jurisdiction: str = "nigeria",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    sb = _sb()

    try:
        res = sb.rpc(
            "match_qa_embeddings_v2",
            {
                "query_embedding": query_embedding,
                "match_limit": limit,
                "match_lang": lang,
                "match_jurisdiction": jurisdiction,
            },
        ).execute()
        return res.data or []
    except Exception:
        return []
