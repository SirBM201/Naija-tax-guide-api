from app.core.supabase_client import supabase as _sb
from typing import Optional, Dict, Any
import hashlib

def _normalize_question(q: str) -> str:
    """Basic normalization: lowercase, strip, remove extra spaces."""
    if not q:
        return ""
    return " ".join(q.strip().lower().split())

def find_best_cached_answer(
    normalized_question: str,
    lang: str = "en",
    canonical_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Returns the best answer from cache searching in this order:
    1. Exact canonical_key match (if provided)
    2. Seeded answers (source='seeded')
    3. Library answers (source='library')
    4. AI cached answers (source='ai')
    
    Within each source, highest priority wins.
    """
    nq = _normalize_question(normalized_question) if normalized_question else ""
    if not nq and not canonical_key:
        return None

    lang = (lang or "en").strip() or "en"
    
    try:
        # Priority 1: exact canonical_key match (most specific)
        if canonical_key and canonical_key.strip():
            ck = canonical_key.strip()
            res = (_sb().table("qa_cache")
                   .select("*")
                   .eq("enabled", True)
                   .eq("canonical_key", ck)
                   .eq("lang", lang)
                   .order("priority", desc=True)
                   .limit(1)
                   .execute())
            if getattr(res, "data", None) and len(res.data) > 0:
                return res.data[0]
        
        # Priority 2: search by normalized_question with source priority
        if nq:
            # Define source priority order
            source_priority = ['seeded', 'library', 'ai']
            for source in source_priority:
                res = (_sb().table("qa_cache")
                       .select("*")
                       .eq("enabled", True)
                       .eq("normalized_question", nq)
                       .eq("lang", lang)
                       .eq("source", source)  # Use existing 'source' column
                       .order("priority", desc=True)
                       .limit(1)
                       .execute())
                if getattr(res, "data", None) and len(res.data) > 0:
                    return res.data[0]
        
        return None
        
    except Exception as e:
        print(f"Cache lookup error: {e}")
        return None

def increment_cache_use(cache_id: str):
    """Increment use_count and update last_used_at."""
    try:
        (_sb().table("qa_cache")
         .update({"use_count": _sb().raw("use_count + 1"), "last_used_at": "now()"})
         .eq("id", cache_id)
         .execute())
    except Exception as e:
        print(f"Failed to increment cache use: {e}")

def upsert_ai_answer_to_cache_best_effort(
    normalized_question: str,
    answer: str,
    source: str = "ai",
    lang: str = "en",
    enabled: bool = True,
    priority: int = 0,
    canonical_key: Optional[str] = None,
    tags: Optional[list] = None
):
    """Upsert an AI-generated answer into cache. Does not overwrite higher-priority seeded/library answers."""
    nq = _normalize_question(normalized_question)
    if not nq or not answer:
        return
    
    lang = (lang or "en").strip() or "en"
    
    try:
        # Check if a seeded/library answer already exists for this question
        existing = (_sb().table("qa_cache")
                    .select("source, priority")
                    .eq("normalized_question", nq)
                    .eq("lang", lang)
                    .in_("source", ["seeded", "library"])
                    .execute())
        
        if existing.data and len(existing.data) > 0:
            # Don't overwrite curated content with AI
            print(f"Skip AI cache upsert: seeded/library answer exists for '{nq}'")
            return
        
        # Otherwise, upsert the AI answer
        data = {
            "normalized_question": nq,
            "answer": answer,
            "source": source,
            "lang": lang,
            "enabled": enabled,
            "priority": priority,
            "use_count": 0,
        }
        if canonical_key:
            data["canonical_key"] = canonical_key
        if tags:
            data["tags"] = tags
            
        (_sb().table("qa_cache")
         .upsert(data, on_conflict="normalized_question,lang")
         .execute())
    except Exception as e:
        print(f"AI cache upsert error: {e}")
