from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(v: Any, n: int = 260) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "..."


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def log_feedback(
    *,
    history_id: Optional[str] = None,
    cache_id: Optional[str] = None,
    embedding_id: Optional[str] = None,
    account_id: Optional[str] = None,
    helpful: Optional[bool] = None,
    followup_needed: bool = False,
    wrong_reason: Optional[str] = None,
    user_comment: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "history_id": (history_id or "").strip() or None,
        "cache_id": (cache_id or "").strip() or None,
        "embedding_id": (embedding_id or "").strip() or None,
        "account_id": (account_id or "").strip() or None,
        "helpful": helpful if helpful is None else bool(helpful),
        "followup_needed": bool(followup_needed),
        "wrong_reason": (wrong_reason or "").strip() or None,
        "user_comment": (user_comment or "").strip() or None,
    }

    try:
        res = _sb().table("qa_feedback").insert(payload).execute()
        rows = getattr(res, "data", None) or []
        return {
            "ok": True,
            "feedback": rows[0] if rows else payload,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "feedback_log_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_feedback table, columns, and backend DB access.",
        }


def get_feedback_stats_for_embedding(embedding_id: str) -> Dict[str, Any]:
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
            .table("qa_feedback")
            .select("helpful,followup_needed,wrong_reason")
            .eq("embedding_id", embedding_id)
            .execute()
        )
        rows = getattr(res, "data", None) or []

        helpful_yes = 0
        helpful_no = 0
        followups = 0

        for row in rows:
            helpful = row.get("helpful")
            if helpful is True:
                helpful_yes += 1
            elif helpful is False:
                helpful_no += 1

            if _safe_bool(row.get("followup_needed")):
                followups += 1

        total = len(rows)

        return {
            "ok": True,
            "embedding_id": embedding_id,
            "total_feedback": total,
            "helpful_yes": helpful_yes,
            "helpful_no": helpful_no,
            "followups": followups,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "feedback_stats_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_feedback table and query access.",
        }


def compute_trust_score(
    *,
    helpful_yes: int,
    helpful_no: int,
    followups: int = 0,
    base_score: float = 0.85,
) -> float:
    """
    Bayesian-style smoothing:
    - start from a prior
    - reward helpful feedback
    - penalize unhelpful feedback
    - lightly penalize frequent followups
    """
    helpful_yes = max(0, int(helpful_yes or 0))
    helpful_no = max(0, int(helpful_no or 0))
    followups = max(0, int(followups or 0))
    base_score = _safe_float(base_score, 0.85)

    # Prior centered near base_score
    alpha = 1.0 + (base_score * 4.0)
    beta = 1.0 + ((1.0 - base_score) * 4.0)

    posterior = (alpha + helpful_yes) / (alpha + beta + helpful_yes + helpful_no)

    # small followup penalty
    penalty = min(0.10, followups * 0.01)

    score = posterior - penalty
    score = max(0.05, min(0.99, score))
    return round(score, 4)


def update_embedding_trust_score(embedding_id: str) -> Dict[str, Any]:
    embedding_id = (embedding_id or "").strip()
    if not embedding_id:
        return {
            "ok": False,
            "error": "embedding_id_required",
            "root_cause": "missing_embedding_id",
        }

    try:
        cur = (
            _sb()
            .table("qa_embeddings")
            .select("id,trust_score,review_status")
            .eq("id", embedding_id)
            .limit(1)
            .execute()
        )
        rows = getattr(cur, "data", None) or []
        if not rows:
            return {
                "ok": False,
                "error": "embedding_not_found",
                "root_cause": "qa_embeddings row not found",
                "details": {"embedding_id": embedding_id},
            }

        current = rows[0] or {}
        current_trust = _safe_float(current.get("trust_score"), 0.85)

        stats = get_feedback_stats_for_embedding(embedding_id)
        if not stats.get("ok"):
            return stats

        new_score = compute_trust_score(
            helpful_yes=int(stats.get("helpful_yes") or 0),
            helpful_no=int(stats.get("helpful_no") or 0),
            followups=int(stats.get("followups") or 0),
            base_score=current_trust,
        )

        # auto-review state changes
        if new_score >= 0.90:
            new_status = "approved"
        elif new_score >= 0.75:
            new_status = "approved"
        elif new_score >= 0.55:
            new_status = "candidate"
        else:
            new_status = "blocked"

        upd = (
            _sb()
            .table("qa_embeddings")
            .update(
                {
                    "trust_score": new_score,
                    "review_status": new_status,
                }
            )
            .eq("id", embedding_id)
            .execute()
        )
        out = getattr(upd, "data", None) or []

        return {
            "ok": True,
            "embedding_id": embedding_id,
            "trust_score": new_score,
            "review_status": new_status,
            "stats": stats,
            "embedding": out[0] if out else {
                "id": embedding_id,
                "trust_score": new_score,
                "review_status": new_status,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "trust_score_update_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings table and update access.",
        }


def log_feedback_and_recalculate(
    *,
    history_id: Optional[str] = None,
    cache_id: Optional[str] = None,
    embedding_id: Optional[str] = None,
    account_id: Optional[str] = None,
    helpful: Optional[bool] = None,
    followup_needed: bool = False,
    wrong_reason: Optional[str] = None,
    user_comment: Optional[str] = None,
) -> Dict[str, Any]:
    logged = log_feedback(
        history_id=history_id,
        cache_id=cache_id,
        embedding_id=embedding_id,
        account_id=account_id,
        helpful=helpful,
        followup_needed=followup_needed,
        wrong_reason=wrong_reason,
        user_comment=user_comment,
    )
    if not logged.get("ok"):
        return logged

    if embedding_id:
        rescored = update_embedding_trust_score(embedding_id)
        return {
            "ok": rescored.get("ok", False),
            "feedback": logged.get("feedback"),
            "rescored": rescored,
        }

    return {
        "ok": True,
        "feedback": logged.get("feedback"),
        "rescored": None,
    }
