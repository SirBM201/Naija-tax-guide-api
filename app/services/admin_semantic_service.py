from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(v: Any, n: int = 260) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "..."


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def semantic_dashboard_summary() -> Dict[str, Any]:
    try:
        emb = _sb().table("qa_embeddings").select("id,trust_score,review_status,hit_count").execute()
        emb_rows = getattr(emb, "data", None) or []

        fb = _sb().table("qa_feedback").select("id,embedding_id,helpful,followup_needed").execute()
        fb_rows = getattr(fb, "data", None) or []

        total_embeddings = len(emb_rows)
        approved = 0
        candidate = 0
        blocked = 0
        low_trust = 0
        high_trust = 0
        total_hits = 0

        for row in emb_rows:
            trust = _safe_float(row.get("trust_score"), 0.0)
            status = str(row.get("review_status") or "").strip().lower()
            hits = _safe_int(row.get("hit_count"), 0)

            total_hits += hits

            if status == "approved":
                approved += 1
            elif status == "candidate":
                candidate += 1
            elif status == "blocked":
                blocked += 1

            if trust < 0.75:
                low_trust += 1
            if trust >= 0.90:
                high_trust += 1

        helpful_yes = 0
        helpful_no = 0
        followups = 0

        for row in fb_rows:
            helpful = row.get("helpful")
            if helpful is True:
                helpful_yes += 1
            elif helpful is False:
                helpful_no += 1

            if bool(row.get("followup_needed")):
                followups += 1

        return {
            "ok": True,
            "summary": {
                "total_embeddings": total_embeddings,
                "approved_embeddings": approved,
                "candidate_embeddings": candidate,
                "blocked_embeddings": blocked,
                "low_trust_embeddings": low_trust,
                "high_trust_embeddings": high_trust,
                "total_embedding_hits": total_hits,
                "total_feedback": len(fb_rows),
                "helpful_yes": helpful_yes,
                "helpful_no": helpful_no,
                "followups": followups,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "semantic_dashboard_summary_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings / qa_feedback table access and schema.",
        }


def list_embeddings(
    *,
    review_status: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    try:
        q = (
            _sb()
            .table("qa_embeddings")
            .select(
                "id,cache_id,question,normalized_question,canonical_key,lang,"
                "jurisdiction,tax_type,audience,trust_score,review_status,"
                "hit_count,source_type,policy_version,created_at,updated_at"
            )
            .order("trust_score", desc=False)
            .order("hit_count", desc=True)
            .limit(int(limit))
        )

        if review_status:
            q = q.eq("review_status", review_status.strip().lower())

        res = q.execute()
        rows = getattr(res, "data", None) or []

        return {
            "ok": True,
            "embeddings": rows,
            "count": len(rows),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "list_embeddings_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings table, columns, and DB access.",
        }


def get_embedding_detail(embedding_id: str) -> Dict[str, Any]:
    embedding_id = (embedding_id or "").strip()
    if not embedding_id:
        return {
            "ok": False,
            "error": "embedding_id_required",
            "root_cause": "missing_embedding_id",
        }

    try:
        emb = (
            _sb()
            .table("qa_embeddings")
            .select("*")
            .eq("id", embedding_id)
            .limit(1)
            .execute()
        )
        emb_rows = getattr(emb, "data", None) or []
        if not emb_rows:
            return {
                "ok": False,
                "error": "embedding_not_found",
                "root_cause": "qa_embeddings row not found",
            }

        embedding = emb_rows[0]

        feedback = (
            _sb()
            .table("qa_feedback")
            .select("*")
            .eq("embedding_id", embedding_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        feedback_rows = getattr(feedback, "data", None) or []

        cache_id = str(embedding.get("cache_id") or "").strip()
        cache_row = None
        if cache_id:
            cache = (
                _sb()
                .table("qa_cache")
                .select("*")
                .eq("id", cache_id)
                .limit(1)
                .execute()
            )
            cache_rows = getattr(cache, "data", None) or []
            cache_row = cache_rows[0] if cache_rows else None

        return {
            "ok": True,
            "embedding": embedding,
            "cache": cache_row,
            "feedback": feedback_rows,
            "feedback_count": len(feedback_rows),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_detail_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings / qa_feedback / qa_cache access.",
        }


def update_embedding_review_status(
    *,
    embedding_id: str,
    review_status: str,
) -> Dict[str, Any]:
    embedding_id = (embedding_id or "").strip()
    review_status = (review_status or "").strip().lower()

    allowed = {"approved", "candidate", "blocked"}
    if not embedding_id:
        return {
            "ok": False,
            "error": "embedding_id_required",
            "root_cause": "missing_embedding_id",
        }
    if review_status not in allowed:
        return {
            "ok": False,
            "error": "invalid_review_status",
            "root_cause": f"review_status must be one of {sorted(allowed)}",
        }

    try:
        upd = (
            _sb()
            .table("qa_embeddings")
            .update({"review_status": review_status})
            .eq("id", embedding_id)
            .execute()
        )
        rows = getattr(upd, "data", None) or []

        return {
            "ok": True,
            "embedding": rows[0] if rows else {
                "id": embedding_id,
                "review_status": review_status,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_review_status_update_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings update access.",
        }


def update_embedding_trust_manually(
    *,
    embedding_id: str,
    trust_score: float,
) -> Dict[str, Any]:
    embedding_id = (embedding_id or "").strip()
    trust_score = max(0.0, min(0.99, float(trust_score)))

    if not embedding_id:
        return {
            "ok": False,
            "error": "embedding_id_required",
            "root_cause": "missing_embedding_id",
        }

    try:
        upd = (
            _sb()
            .table("qa_embeddings")
            .update({"trust_score": trust_score})
            .eq("id", embedding_id)
            .execute()
        )
        rows = getattr(upd, "data", None) or []

        return {
            "ok": True,
            "embedding": rows[0] if rows else {
                "id": embedding_id,
                "trust_score": trust_score,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_trust_update_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings update access.",
        }


def block_embedding_and_cache(embedding_id: str) -> Dict[str, Any]:
    embedding_id = (embedding_id or "").strip()
    if not embedding_id:
        return {
            "ok": False,
            "error": "embedding_id_required",
            "root_cause": "missing_embedding_id",
        }

    try:
        res = (
            _sb()
            .table("qa_embeddings")
            .select("id,cache_id")
            .eq("id", embedding_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return {
                "ok": False,
                "error": "embedding_not_found",
                "root_cause": "qa_embeddings row not found",
            }

        row = rows[0]
        cache_id = str(row.get("cache_id") or "").strip() or None

        emb_upd = (
            _sb()
            .table("qa_embeddings")
            .update({"review_status": "blocked", "trust_score": 0.10})
            .eq("id", embedding_id)
            .execute()
        )
        emb_out = getattr(emb_upd, "data", None) or []

        cache_out = None
        if cache_id:
            try:
                c_upd = (
                    _sb()
                    .table("qa_cache")
                    .update({"enabled": False})
                    .eq("id", cache_id)
                    .execute()
                )
                c_rows = getattr(c_upd, "data", None) or []
                cache_out = c_rows[0] if c_rows else {"id": cache_id, "enabled": False}
            except Exception:
                cache_out = {"id": cache_id, "enabled": False, "warning": "cache_update_not_returned"}

        return {
            "ok": True,
            "embedding": emb_out[0] if emb_out else {"id": embedding_id, "review_status": "blocked", "trust_score": 0.10},
            "cache": cache_out,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "block_embedding_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings and qa_cache update access.",
        }


def low_trust_embeddings(limit: int = 50) -> Dict[str, Any]:
    try:
        res = (
            _sb()
            .table("qa_embeddings")
            .select(
                "id,cache_id,question,canonical_key,lang,jurisdiction,"
                "trust_score,review_status,hit_count,updated_at"
            )
            .lt("trust_score", 0.75)
            .order("trust_score", desc=False)
            .limit(int(limit))
            .execute()
        )
        rows = getattr(res, "data", None) or []

        return {
            "ok": True,
            "embeddings": rows,
            "count": len(rows),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "low_trust_embeddings_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings read access.",
        }


def top_reused_embeddings(limit: int = 50) -> Dict[str, Any]:
    try:
        res = (
            _sb()
            .table("qa_embeddings")
            .select(
                "id,cache_id,question,canonical_key,lang,jurisdiction,"
                "trust_score,review_status,hit_count,updated_at"
            )
            .order("hit_count", desc=True)
            .limit(int(limit))
            .execute()
        )
        rows = getattr(res, "data", None) or []

        return {
            "ok": True,
            "embeddings": rows,
            "count": len(rows),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "top_reused_embeddings_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings read access.",
        }
