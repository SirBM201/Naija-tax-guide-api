# app/services/qa_cache_service.py
from __future__ import annotations

from typing import Optional, Dict, Any
from datetime import datetime, timezone

from ..core.supabase_client import supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_question(q: str) -> str:
    """Basic normalization: lowercase, strip, remove extra spaces."""
    if not q:
        return ""
    return " ".join(q.strip().lower().split())


def find_cached_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Legacy function – kept for compatibility."""
    nq = (normalized_question or "").strip()
    if not nq:
        return None
    lang = (lang or "en").strip() or "en"

    try:
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            res = (
                supabase().table("qa_cache")
                .select("*")
                .eq("enabled", True)
                .eq("canonical_key", ck)
                .eq("lang", lang)
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None):
                return res.data[0]

        res = (
            supabase().table("qa_cache")
            .select("*")
            .eq("enabled", True)
            .eq("normalized_question", nq)
            .eq("lang", lang)
            .order("priority", desc=True)
            .limit(1)
            .execute()
        )
        if getattr(res, "data", None):
            return res.data[0]
        return None
    except Exception:
        return None


def find_best_cached_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns the best answer from cache searching in order:
    1. Exact canonical_key match
    2. source='seeded'
    3. source='library'
    4. source='ai'
    """
    nq = _normalize_question(normalized_question) if normalized_question else ""
    if not nq and not canonical_key:
        return None

    lang = (lang or "en").strip() or "en"

    try:
        # 1. canonical_key match
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            res = (
                supabase().table("qa_cache")
                .select("*")
                .eq("enabled", True)
                .eq("canonical_key", ck)
                .eq("lang", lang)
                .order("priority", desc=True)
                .limit(1)
                .execute()
            )
            if getattr(res, "data", None) and len(res.data) > 0:
                return res.data[0]

        # 2. source priority order
        if nq:
            for source in ["seeded", "library", "ai"]:
                res = (
                    supabase().table("qa_cache")
                    .select("*")
                    .eq("enabled", True)
                    .eq("normalized_question", nq)
                    .eq("lang", lang)
                    .eq("source", source)
                    .order("priority", desc=True)
                    .limit(1)
                    .execute()
                )
                if getattr(res, "data", None) and len(res.data) > 0:
                    return res.data[0]
        return None
    except Exception as e:
        print(f"find_best_cached_answer error: {e}")
        return None


def touch_cache_best_effort(cache_id: str) -> None:
    cid = (cache_id or "").strip()
    if not cid:
        return
    try:
        res = supabase().table("qa_cache").select("use_count").eq("id", cid).limit(1).execute()
        current = 0
        if getattr(res, "data", None):
            current = int(res.data[0].get("use_count") or 0)

        supabase().table("qa_cache").update(
            {"use_count": current + 1, "last_used_at": _now_iso()}
        ).eq("id", cid).execute()
    except Exception:
        return


def increment_cache_use(cache_id: str) -> None:
    touch_cache_best_effort(cache_id)


def upsert_ai_answer_to_cache_best_effort(
    normalized_question: str,
    answer: str,
    tags: Optional[str] = None,
    source: str = "ai",
    lang: str = "en",
    canonical_key: Optional[str] = None,
    enabled: bool = True,
    priority: int = 0,
) -> None:
    nq = _normalize_question(normalized_question) if normalized_question else ""
    ans = (answer or "").strip()
    if not nq or not ans:
        return

    lang = (lang or "en").strip() or "en"

    # Do not overwrite seeded/library answers
    try:
        existing = (
            supabase().table("qa_cache")
            .select("source")
            .eq("enabled", True)
            .eq("normalized_question", nq)
            .eq("lang", lang)
            .in_("source", ["seeded", "library"])
            .limit(1)
            .execute()
        )
        if getattr(existing, "data", None) and len(existing.data) > 0:
            return
    except Exception:
        pass

    payload: Dict[str, Any] = {
        "normalized_question": nq,
        "answer": ans,
        "tags": tags,
        "source": source,
        "enabled": bool(enabled),
        "priority": int(priority or 0),
        "lang": lang,
        "last_used_at": _now_iso(),
    }
    if canonical_key and canonical_key.strip():
        payload["canonical_key"] = canonical_key.strip()

    try:
        if payload.get("canonical_key"):
            supabase().table("qa_cache").upsert(payload, on_conflict="canonical_key,lang").execute()
        else:
            supabase().table("qa_cache").upsert(payload, on_conflict="normalized_question,lang").execute()
    except Exception:
        return
