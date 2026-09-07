from __future__ import annotations

"""Backfill semantic vectors only for already-approved reusable qa_cache rows.

Safety defaults:
- dry-run unless --execute is supplied
- small bounded batches
- skips AI/unreviewed/disabled/non-reusable rows
- skips rows already present in qa_embeddings
- never changes qa_cache review state
"""

import argparse
from typing import Any

from app.core.supabase_client import supabase
from app.services.semantic_runtime_service import create_embedding_row


def _sb():
    return supabase() if callable(supabase) else supabase


def _truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _approved(row: dict[str, Any]) -> bool:
    status = str(row.get("review_status") or "").strip().lower()
    source = str(row.get("source") or row.get("source_type") or "").strip().lower()
    enabled = row.get("enabled")
    reusable = row.get("reusable_without_credit")
    try:
        trust = float(row.get("trust_score") or 0)
    except Exception:
        trust = 0.0
    return (
        status in {"approved", "active", "published", "ai_reviewed_safe"}
        and (enabled is None or _truthy(enabled))
        and _truthy(reusable)
        and trust >= 0.75
        and not source.startswith("ai")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    limit = max(1, min(int(args.limit), 100))

    res = _sb().table("qa_cache").select("*").limit(1000).execute()
    rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
    candidates = [r for r in rows if _approved(r)]

    existing_res = _sb().table("qa_embeddings").select("cache_id").limit(5000).execute()
    existing = {str(r.get("cache_id")) for r in (getattr(existing_res, "data", None) or []) if isinstance(r, dict) and r.get("cache_id")}
    pending = [r for r in candidates if str(r.get("id") or "") not in existing][:limit]

    print({"mode": "execute" if args.execute else "dry_run", "qa_cache_rows": len(rows), "approved_reusable_candidates": len(candidates), "already_embedded": len(existing), "selected": len(pending)})
    if not args.execute:
        for row in pending[:10]:
            print({"cache_id": row.get("id"), "question": str(row.get("question") or row.get("normalized_question") or "")[:120]})
        return 0

    ok = 0
    failed = 0
    for row in pending:
        cache_id = str(row.get("id") or "").strip()
        question = str(row.get("question") or row.get("normalized_question") or "").strip()
        if not cache_id or not question:
            failed += 1
            continue
        result = create_embedding_row(
            cache_id=cache_id,
            question=question,
            normalized_question=str(row.get("normalized_question") or "") or None,
            canonical_key=str(row.get("canonical_key") or "") or None,
            lang=str(row.get("lang") or "en"),
            jurisdiction=str(row.get("jurisdiction") or "nigeria"),
            tax_type=str(row.get("tax_type") or row.get("topic") or "") or None,
            audience=str(row.get("audience") or "") or None,
            source_type="approved_cache",
            policy_version="v1-approved-backfill",
        )
        if result.get("ok"):
            ok += 1
        else:
            failed += 1
            print({"cache_id": cache_id, "error": result.get("error"), "root_cause": result.get("root_cause")})

    print({"created_or_reused": ok, "failed": failed})
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
