from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.supabase_client import supabase


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(v: Any, n: int = 260) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "..."


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _openai_embeddings_enabled() -> bool:
    return bool(_env("OPENAI_API_KEY", ""))


def create_history_row(
    *,
    account_id: str,
    question: str,
    answer: str,
    source: str,
    provider: str,
    lang: str,
    normalized_question: Optional[str] = None,
    canonical_key: Optional[str] = None,
    cache_id: Optional[str] = None,
    embedding_id: Optional[str] = None,
) -> Dict[str, Any]:
    account_id = (account_id or "").strip()
    question = (question or "").strip()
    answer = (answer or "").strip()
    source = (source or "").strip() or "ai"
    provider = (provider or "").strip() or "web"
    lang = (lang or "en").strip() or "en"

    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
        }

    if not question:
        return {
            "ok": False,
            "error": "question_required",
            "root_cause": "missing_question",
        }

    if not answer:
        return {
            "ok": False,
            "error": "answer_required",
            "root_cause": "missing_answer",
        }

    payload = {
        "account_id": account_id,
        "question": question,
        "answer": answer,
        "source": source,
        "provider": provider,
        "lang": lang,
        "normalized_question": (normalized_question or "").strip() or None,
        "canonical_key": (canonical_key or "").strip() or None,
        "cache_id": (cache_id or "").strip() or None,
        "embedding_id": (embedding_id or "").strip() or None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    try:
        res = _sb().table("qa_history").insert(payload).execute()
        rows = getattr(res, "data", None) or []
        return {
            "ok": True,
            "history": rows[0] if rows else payload,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "history_insert_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_history table columns and DB access.",
        }


def _generate_openai_embedding(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {
            "ok": False,
            "error": "text_required",
            "root_cause": "empty_embedding_input",
        }

    api_key = _env("OPENAI_API_KEY", "")
    model = _env("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    if not api_key:
        return {
            "ok": False,
            "error": "embeddings_not_configured",
            "root_cause": "OPENAI_API_KEY missing",
            "fix": "Set OPENAI_API_KEY to enable semantic vector writes.",
        }

    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        return {
            "ok": False,
            "error": "openai_sdk_missing",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Install openai package in backend requirements.",
        }

    try:
        client = OpenAI(api_key=api_key)
        resp = client.embeddings.create(
            model=model,
            input=text,
        )

        vec = resp.data[0].embedding if resp.data else None
        if not vec:
            return {
                "ok": False,
                "error": "embedding_empty",
                "root_cause": "OpenAI returned no embedding vector",
                "fix": "Check provider response and embedding model.",
            }

        return {
            "ok": True,
            "embedding": vec,
            "model": model,
            "dimensions": len(vec),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_generation_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check OPENAI_API_KEY, network access, and embedding model.",
        }


def create_embedding_row(
    *,
    cache_id: str,
    question: str,
    normalized_question: Optional[str] = None,
    canonical_key: Optional[str] = None,
    lang: str = "en",
    jurisdiction: str = "nigeria",
    tax_type: Optional[str] = None,
    audience: Optional[str] = None,
    source_type: str = "cache",
    policy_version: Optional[str] = None,
) -> Dict[str, Any]:
    cache_id = (cache_id or "").strip()
    question = (question or "").strip()
    lang = (lang or "en").strip() or "en"
    jurisdiction = (jurisdiction or "nigeria").strip() or "nigeria"

    if not cache_id:
        return {
            "ok": False,
            "error": "cache_id_required",
            "root_cause": "missing_cache_id",
        }

    if not question:
        return {
            "ok": False,
            "error": "question_required",
            "root_cause": "missing_question",
        }

    try:
        existing = (
            _sb()
            .table("qa_embeddings")
            .select("id,cache_id,trust_score,review_status,hit_count")
            .eq("cache_id", cache_id)
            .limit(1)
            .execute()
        )
        rows = getattr(existing, "data", None) or []
        if rows:
            return {
                "ok": True,
                "embedding": rows[0],
                "reused": True,
            }
    except Exception:
        pass

    emb = _generate_openai_embedding(question)
    if not emb.get("ok"):
        return emb

    payload = {
        "cache_id": cache_id,
        "question": question,
        "normalized_question": (normalized_question or "").strip() or None,
        "canonical_key": (canonical_key or "").strip() or None,
        "lang": lang,
        "jurisdiction": jurisdiction,
        "tax_type": (tax_type or "").strip() or None,
        "audience": (audience or "").strip() or None,
        "trust_score": 0.85,
        "review_status": "approved",
        "hit_count": 0,
        "source_type": (source_type or "cache").strip() or "cache",
        "policy_version": (policy_version or "").strip() or "v1",
        "embedding_vector": emb["embedding"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    try:
        res = _sb().table("qa_embeddings").insert(payload).execute()
        rows = getattr(res, "data", None) or []

        return {
            "ok": True,
            "embedding": rows[0] if rows else payload,
            "created": True,
            "embedding_model": emb.get("model"),
            "dimensions": emb.get("dimensions"),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_insert_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings columns, pgvector setup, and embedding_vector type.",
        }


def increment_embedding_hit_count(embedding_id: Optional[str]) -> Dict[str, Any]:
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
            .select("id,hit_count")
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
            }

        row = rows[0]
        current_hits = _safe_int(row.get("hit_count"), 0)
        new_hits = current_hits + 1

        upd = (
            _sb()
            .table("qa_embeddings")
            .update(
                {
                    "hit_count": new_hits,
                    "updated_at": _now_iso(),
                }
            )
            .eq("id", embedding_id)
            .execute()
        )
        out = getattr(upd, "data", None) or []

        return {
            "ok": True,
            "embedding": out[0] if out else {
                "id": embedding_id,
                "hit_count": new_hits,
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "embedding_hit_update_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": "Check qa_embeddings access and hit_count column.",
        }


def semantic_write_runtime(
    *,
    account_id: str,
    question: str,
    answer: str,
    source: str,
    provider: str,
    lang: str,
    normalized_question: Optional[str],
    canonical_key: Optional[str],
    cache_id: Optional[str],
) -> Dict[str, Any]:
    """
    Runtime write orchestration:
    1. create/update embedding if cache_id exists and embeddings are enabled
    2. create history row
    3. increment embedding hit count if embedding exists
    """
    embedding_result = None
    embedding_id = None

    if cache_id and _openai_embeddings_enabled():
        embedding_result = create_embedding_row(
            cache_id=cache_id,
            question=question,
            normalized_question=normalized_question,
            canonical_key=canonical_key,
            lang=lang,
            jurisdiction="nigeria",
            source_type=source,
            policy_version="v1",
        )
        if embedding_result.get("ok"):
            emb_row = embedding_result.get("embedding") or {}
            embedding_id = str(emb_row.get("id") or "").strip() or None

    history_result = create_history_row(
        account_id=account_id,
        question=question,
        answer=answer,
        source=source,
        provider=provider,
        lang=lang,
        normalized_question=normalized_question,
        canonical_key=canonical_key,
        cache_id=cache_id,
        embedding_id=embedding_id,
    )

    hit_result = None
    if embedding_id:
        hit_result = increment_embedding_hit_count(embedding_id)

    return {
        "ok": bool(history_result.get("ok")),
        "history_result": history_result,
        "embedding_result": embedding_result,
        "embedding_hit_result": hit_result,
        "embedding_id": embedding_id,
    }
