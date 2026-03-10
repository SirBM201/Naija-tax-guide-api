from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase

HISTORY_TABLE = "qa_history"


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(v: Any, n: int = 280) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _has_table(table: str) -> bool:
    try:
        _sb().table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


def _safe_select_cols() -> str:
    preferred = [
        "id",
        "account_id",
        "question",
        "answer",
        "lang",
        "source",
        "from_cache",
        "canonical_key",
        "normalized_question",
        "plan_code",
        "credits_consumed",
        "usage_charged",
        "channel",
        "created_at",
        "updated_at",
    ]
    cols = [c for c in preferred if _has_column(HISTORY_TABLE, c)]
    return ",".join(cols) if cols else "*"


def history_table_ready() -> Dict[str, Any]:
    exists = _has_table(HISTORY_TABLE)
    return {
        "ok": True,
        "table": HISTORY_TABLE,
        "exists": exists,
    }


def log_history_item_best_effort(
    *,
    account_id: str,
    question: str,
    answer: str,
    lang: str = "en",
    source: str = "ai",
    from_cache: bool = False,
    canonical_key: Optional[str] = None,
    normalized_question: Optional[str] = None,
    plan_code: Optional[str] = None,
    credits_consumed: int = 0,
    usage_charged: bool = False,
    channel: str = "web",
) -> None:
    """
    Best-effort persistence for successful user-visible Q/A history.
    Never throws.
    """
    account_id = (account_id or "").strip()
    question = (question or "").strip()
    answer = (answer or "").strip()
    lang = (lang or "en").strip() or "en"
    source = (source or "ai").strip().lower() or "ai"
    channel = (channel or "web").strip().lower() or "web"
    canonical_key = (canonical_key or "").strip() or None
    normalized_question = (normalized_question or "").strip() or None
    plan_code = (plan_code or "").strip().lower() or None

    if not account_id or not question or not answer:
        return

    if not _has_table(HISTORY_TABLE):
        return

    payload: Dict[str, Any] = {
        "account_id": account_id,
        "question": question,
        "answer": answer,
        "lang": lang,
        "source": source,
        "from_cache": bool(from_cache),
        "canonical_key": canonical_key,
        "normalized_question": normalized_question,
        "plan_code": plan_code,
        "credits_consumed": int(credits_consumed or 0),
        "usage_charged": bool(usage_charged),
        "channel": channel,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    safe_payload = {k: v for k, v in payload.items() if _has_column(HISTORY_TABLE, k)}

    if not safe_payload:
        return

    try:
        _sb().table(HISTORY_TABLE).insert(safe_payload).execute()
    except Exception:
        return


def list_history_items(
    *,
    account_id: str,
    limit: int = 50,
    source: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    account_id = (account_id or "").strip()
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Authenticate first so canonical accounts.account_id can be resolved.",
        }

    if not _has_table(HISTORY_TABLE):
        return {
            "ok": False,
            "error": "history_table_missing",
            "root_cause": f"{HISTORY_TABLE} table is not available.",
            "fix": f"Create table {HISTORY_TABLE} before using persistent history.",
            "details": {"table": HISTORY_TABLE},
        }

    try:
        safe_limit = max(1, min(int(limit or 50), 200))
    except Exception:
        safe_limit = 50

    source = (source or "").strip().lower() or None
    query = (query or "").strip().lower() or None

    try:
        q = (
            _sb()
            .table(HISTORY_TABLE)
            .select(_safe_select_cols())
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(safe_limit)
        )

        if source and _has_column(HISTORY_TABLE, "source"):
            q = q.eq("source", source)

        res = q.execute()
        rows = getattr(res, "data", None) or []

        if query:
            filtered: List[Dict[str, Any]] = []
            for row in rows:
                haystack = " ".join(
                    [
                        str(row.get("question") or ""),
                        str(row.get("answer") or ""),
                        str(row.get("lang") or ""),
                        str(row.get("source") or ""),
                        str(row.get("canonical_key") or ""),
                    ]
                ).lower()
                if query in haystack:
                    filtered.append(row)
            rows = filtered

        return {
            "ok": True,
            "items": rows,
            "count": len(rows),
            "account_id": account_id,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": "history_lookup_failed",
            "root_cause": f"{type(e).__name__}: {_clip(e)}",
            "fix": f"Check {HISTORY_TABLE} table access, columns, and Supabase connectivity.",
            "details": {"table": HISTORY_TABLE, "account_id": account_id},
        }
