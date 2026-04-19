from __future__ import annotations

import re
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from ..core.supabase_client import supabase


def _sb():
    """Return the Supabase client, handling both callable and instance."""
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_question(q: str) -> str:
    """
    Normalize a question: lowercase, strip, remove extra spaces,
    and remove trailing punctuation (?, !, .).
    """
    if not q:
        return ""
    text = q.strip().lower()
    text = re.sub(r'[?!.;]+$', '', text)
    text = " ".join(text.split())
    return text


def find_cached_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Exact match in qa_cache (original behaviour)."""
    nq = (normalized_question or "").strip()
    if not nq:
        return None
    lang = (lang or "en").strip() or "en"

    try:
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            res = (
                _sb().table("qa_cache")
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
            _sb().table("qa_cache")
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


def find_answer_in_library(
    normalized_question: str,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """
    Search qa_library with exact match, then trigram similarity.
    Returns a dict compatible with qa_cache rows (including 'answer', 'source', etc.)
    """
    nq = (normalized_question or "").strip()
    if not nq:
        return None

    lang = (lang or "en").strip() or "en"
    # Determine which answer column to use
    lang_column = f"answer_{lang}" if lang != "en" else "answer"
    # Verify the column exists; fallback to 'answer'
    try:
        _sb().table("qa_library").select(lang_column).limit(1).execute()
    except Exception:
        lang_column = "answer"

    try:
        # ----- Stage 1: Exact match -----
        res = (
            _sb().table("qa_library")
            .select("id", "answer", lang_column, "priority", "canonical_key", "tags")
            .eq("normalized_question", nq)
            .eq("enabled", True)
            .order("priority", desc=True)
            .limit(1)
            .execute()
        )
        if getattr(res, "data", None) and res.data:
            row = res.data[0]
            return {
                "id": row.get("id"),
                "answer": row.get(lang_column) or row.get("answer"),
                "source": "library_exact",
                "priority": row.get("priority", 50),
                "canonical_key": row.get("canonical_key"),
                "tags": row.get("tags"),
                "normalized_question": nq,
                "lang": lang,
                "enabled": True,
            }

        # ----- Stage 2: Trigram similarity (requires pg_trgm extension) -----
        # Escape single quotes for safety
        safe_nq = nq.replace("'", "''")
        res = (
            _sb().table("qa_library")
            .select("id", "answer", lang_column, "priority", "canonical_key", "tags",
                    f"similarity(normalized_question, '{safe_nq}') as sim")
            .eq("enabled", True)
            .filter("normalized_question", "op", "%", value=nq)
            .limit(5)
            .execute()
        )
        if getattr(res, "data", None):
            best_row = None
            best_score = -1
            for row in res.data:
                sim = row.get("sim", 0.0)
                if sim < 0.35:  # similarity threshold
                    continue
                # Score combines priority (0-100) and similarity (0-1)
                score = (row.get("priority", 0) * 100) + sim
                if score > best_score:
                    best_score = score
                    best_row = row
            if best_row:
                return {
                    "id": best_row.get("id"),
                    "answer": best_row.get(lang_column) or best_row.get("answer"),
                    "source": "library_trigram",
                    "priority": best_row.get("priority", 50),
                    "canonical_key": best_row.get("canonical_key"),
                    "tags": best_row.get("tags"),
                    "normalized_question": nq,
                    "lang": lang,
                    "enabled": True,
                    "similarity": best_row.get("sim"),
                }
    except Exception as e:
        # Log error but don't crash
        print(f"Error searching qa_library: {e}")
    return None


def find_best_cached_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Multi-stage search:
    1. qa_cache (exact, canonical, source priority)
    2. qa_library (exact then trigram)
    3. Return None if not found.
    """
    nq = _normalize_question(normalized_question) if normalized_question else ""
    if not nq and not canonical_key:
        return None

    lang = (lang or "en").strip() or "en"

    # ----- Stage 1: qa_cache (same as original) -----
    try:
        # 1a. canonical_key match
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            res = (
                _sb().table("qa_cache")
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

        # 1b. source priority order
        if nq:
            for source in ["seeded", "library", "ai"]:
                res = (
                    _sb().table("qa_cache")
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
    except Exception as e:
        print(f"find_best_cached_answer cache error: {e}")

    # ----- Stage 2: qa_library -----
    library_answer = find_answer_in_library(nq, lang)
    if library_answer:
        # Optionally copy to qa_cache for future speed
        try:
            upsert_ai_answer_to_cache_best_effort(
                normalized_question=nq,
                answer=library_answer["answer"],
                tags=library_answer.get("tags"),
                source="library",
                lang=lang,
                canonical_key=library_answer.get("canonical_key"),
                enabled=True,
                priority=library_answer.get("priority", 50),
            )
        except Exception as e:
            print(f"Failed to cache library answer: {e}")
        # Return in the same format as qa_cache rows
        return {
            "id": library_answer.get("id"),
            "normalized_question": nq,
            "answer": library_answer["answer"],
            "source": library_answer["source"],
            "priority": library_answer.get("priority", 50),
            "canonical_key": library_answer.get("canonical_key"),
            "lang": lang,
            "enabled": True,
            "tags": library_answer.get("tags"),
        }

    return None


def touch_cache_best_effort(cache_id: str) -> None:
    cid = (cache_id or "").strip()
    if not cid:
        return
    try:
        res = _sb().table("qa_cache").select("use_count").eq("id", cid).limit(1).execute()
        current = 0
        if getattr(res, "data", None):
            current = int(res.data[0].get("use_count") or 0)

        _sb().table("qa_cache").update(
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
            _sb().table("qa_cache")
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
            _sb().table("qa_cache").upsert(payload, on_conflict="canonical_key,lang").execute()
        else:
            _sb().table("qa_cache").upsert(payload, on_conflict="normalized_question,lang").execute()
    except Exception:
        return
