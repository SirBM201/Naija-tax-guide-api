from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase
from app.services.ai_service import create_embedding


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(v: Any, n: int = 260) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "..."


def semantic_match_question(
    *,
    question: str,
    lang: str = "en",
    jurisdiction: str = "nigeria",
    match_count: int = 5,
    min_trust: float = 0.75,
) -> Dict[str, Any]:
    question = (question or "").strip()
    lang = (lang or "en").strip() or "en"
    jurisdiction = (jurisdiction or "nigeria").strip().lower() or "nigeria"

    if not question:
        return {
            "ok": False,
            "error": "question_required",
            "root_cause": "missing_question",
            "fix": "Provide a non-empty question for semantic matching.",
        }

    emb = create_embedding(question)
    if not emb.get("ok"):
        return emb

    vector = emb.get("embedding")
    if not vector:
        return {
            "ok": False,
            "error": "embedding_missing",
            "root_cause": "Embedding provider returned no vector.",
            "fix": "Check embedding provider configuration.",
        }

    try:
        res = _sb().rpc(
            "match_qa_embeddings",
            {
                "query_embedding": vector,
                "match_count": int(match_count),
                "match_lang": lang,
                "match_jurisdiction": jurisdiction,
                "min_trust": float(min_trust),
            },
        ).execute()

        rows = getattr(res, "data", None) or []
        return {
            "ok": True,
            "matches": rows,
            "count": len(rows),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": "semantic_match_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check pgvector setup, SQL function, and RPC access.",
        }


def choose_best_semantic_match(
    matches: List[Dict[str, Any]],
    *,
    direct_threshold: float = 0.92,
    review_threshold: float = 0.85,
) -> Dict[str, Any]:
    if not matches:
        return {
            "ok": True,
            "decision": "miss",
            "best_match": None,
            "reason": "no_matches",
        }

    best = matches[0]
    similarity = float(best.get("similarity") or 0.0)
    trust_score = float(best.get("trust_score") or 0.0)

    adjusted_score = similarity * 0.85 + trust_score * 0.15

    if adjusted_score >= direct_threshold:
        return {
            "ok": True,
            "decision": "direct_hit",
            "best_match": best,
            "score": adjusted_score,
        }

    if adjusted_score >= review_threshold:
        return {
            "ok": True,
            "decision": "review_hit",
            "best_match": best,
            "score": adjusted_score,
        }

    return {
        "ok": True,
        "decision": "miss",
        "best_match": best,
        "score": adjusted_score,
    }


def increment_embedding_hit_best_effort(embedding_id: str) -> None:
    embedding_id = (embedding_id or "").strip()
    if not embedding_id:
        return

    try:
        current = (
            _sb()
            .table("qa_embeddings")
            .select("hit_count")
            .eq("id", embedding_id)
            .limit(1)
            .execute()
        )
        rows = getattr(current, "data", None) or []
        count = int((rows[0].get("hit_count") if rows else 0) or 0)

        _sb().table("qa_embeddings").update(
            {
                "hit_count": count + 1,
                "last_used_at": "now()",
            }
        ).eq("id", embedding_id).execute()
    except Exception:
        return
