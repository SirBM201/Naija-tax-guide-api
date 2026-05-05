from __future__ import annotations

import re
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from ..core.supabase_client import supabase

# Set up logging
logger = logging.getLogger(__name__)

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
    except Exception as e:
        logger.error(f"find_cached_answer error: {e}")
        return None

def find_answer_in_library(
    normalized_question: str,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """
    Search qa_library with exact match, then trigram similarity (via RPC).
    Returns a dict compatible with qa_cache rows.
    """
    nq = (normalized_question or "").strip()
    if not nq:
        return None

    lang = (lang or "en").strip() or "en"
    lang_column = f"answer_{lang}" if lang != "en" else "answer"
    logger.info(f"Searching library for '{nq}', lang={lang}, column={lang_column}")

    try:
        # ----- Stage 1: Exact match -----
        logger.info("Stage 1: Exact match")
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
            answer_text = row.get(lang_column) or row.get("answer")
            logger.info(f"Exact match found, answer length={len(answer_text) if answer_text else 0}")
            return {
                "id": row.get("id"),
                "answer": answer_text,
                "source": "library_exact",
                "priority": row.get("priority", 50),
                "canonical_key": row.get("canonical_key"),
                "tags": row.get("tags"),
                "normalized_question": nq,
                "lang": lang,
                "enabled": True,
            }

        # ----- Stage 2: Trigram similarity via RPC -----
        logger.info("Stage 2: Trigram similarity")
        res = _sb().rpc("search_library_trigram", {
            "query_text": nq,
            "min_similarity": 0.35
        }).execute()

        if getattr(res, "data", None) and len(res.data) > 0:
            best_row = res.data[0]
            answer_text = best_row.get(lang_column) or best_row.get("answer")
            logger.info(f"Trigram match found, similarity={best_row.get('similarity')}, answer length={len(answer_text) if answer_text else 0}")
            return {
                "id": best_row.get("id"),
                "answer": answer_text,
                "source": "library_trigram",
                "priority": best_row.get("priority", 50),
                "canonical_key": best_row.get("canonical_key"),
                "tags": best_row.get("tags"),
                "normalized_question": nq,
                "lang": lang,
                "enabled": True,
                "similarity": best_row.get("similarity"),
            }
        else:
            logger.info("No trigram match found")
    except Exception as e:
        logger.error(f"Error searching qa_library: {e}", exc_info=True)
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
    logger.info(f"find_best_cached_answer: nq='{nq}', lang='{lang}', canonical_key='{canonical_key}'")

    # ----- Stage 1: qa_cache -----
    try:
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            logger.info(f"Checking qa_cache with canonical_key={ck}")
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
                row = res.data[0]
                logger.info(f"Cache hit via canonical_key, answer length={len(row.get('answer', ''))}")
                return row

        if nq:
            logger.info(f"Checking qa_cache with normalized_question='{nq}'")
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
                    row = res.data[0]
                    logger.info(f"Cache hit via source={source}, answer length={len(row.get('answer', ''))}")
                    # If answer is empty, delete this cache entry and break to fall through to library
                    if not row.get('answer'):
                        logger.warning(f"Cache answer empty, deleting cache entry id={row['id']}")
                        _sb().table("qa_cache").delete().eq("id", row['id']).execute()
                        break  # exit loop and go to library
                    return row
    except Exception as e:
        logger.error(f"find_best_cached_answer cache error: {e}", exc_info=True)

    # ----- Stage 2: qa_library -----
    logger.info("No valid cache entry, searching qa_library")
    library_answer = find_answer_in_library(nq, lang)
    if library_answer and library_answer.get("answer"):
        logger.info(f"Found in library: source={library_answer.get('source')}, answer length={len(library_answer['answer'])}")
        # Copy to cache for future speed
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
            logger.info("Copied library answer to cache")
        except Exception as e:
            logger.error(f"Failed to cache library answer: {e}")
        # Return in expected format
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

    logger.info("No answer found in library either")
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
            logger.info(f"Not overwriting existing {existing.data[0]['source']} answer in cache")
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
            logger.info(f"Upserted to cache with canonical_key={payload['canonical_key']}")
        else:
            _sb().table("qa_cache").upsert(payload, on_conflict="normalized_question,lang").execute()
            logger.info(f"Upserted to cache with normalized_question={nq}")
    except Exception as e:
        logger.error(f"Failed to upsert to cache: {e}")
