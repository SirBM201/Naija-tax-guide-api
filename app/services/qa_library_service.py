from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.supabase_client import supabase

HISTORY_TABLE = "qa_history"


SAFE_HISTORY_COLUMNS = [
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


DEFAULT_ITEM = {
    "id": None,
    "account_id": "",
    "question": "",
    "answer": "",
    "lang": "en",
    "source": "web",
    "from_cache": False,
    "canonical_key": None,
    "normalized_question": None,
    "plan_code": None,
    "credits_consumed": 0,
    "usage_charged": False,
    "channel": "web",
    "created_at": None,
    "updated_at": None,
}


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(value: Any, limit: int = 280) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}...<truncated>"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _normalize_lang(value: Any) -> str:
    text = str(value or "en").strip().lower()
    return text or "en"


def _normalize_channel(value: Any) -> str:
    text = str(value or "web").strip().lower()
    return text or "web"


def _normalize_source(value: Any, channel: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "ai", "direct_cache", "cache", "rules_engine", "tax_process_composer", "ai_grounded"}:
        return channel or "web"
    return text


def _has_table(table: str) -> bool:
    try:
        _sb().table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


def _has_column(table: str, column: str) -> bool:
    try:
        _sb().table(table).select(column).limit(1).execute()
        return True
    except Exception:
        return False


def _available_columns() -> List[str]:
    return [column for column in SAFE_HISTORY_COLUMNS if _has_column(HISTORY_TABLE, column)]


def _safe_select_cols() -> str:
    cols = _available_columns()
    return ",".join(cols) if cols else "*"


def _row_to_item(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(DEFAULT_ITEM)
    raw = row or {}
    item.update(
        {
            "id": raw.get("id"),
            "account_id": str(raw.get("account_id") or "").strip(),
            "question": str(raw.get("question") or "").strip(),
            "answer": str(raw.get("answer") or "").strip(),
            "lang": _normalize_lang(raw.get("lang")),
            "source": _normalize_source(raw.get("source"), _normalize_channel(raw.get("channel"))),
            "from_cache": _as_bool(raw.get("from_cache")),
            "canonical_key": _normalize_text(raw.get("canonical_key")),
            "normalized_question": _normalize_text(raw.get("normalized_question")),
            "plan_code": _normalize_text(raw.get("plan_code")),
            "credits_consumed": _as_int(raw.get("credits_consumed"), 0),
            "usage_charged": _as_bool(raw.get("usage_charged")),
            "channel": _normalize_channel(raw.get("channel")),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
        }
    )
    return item


def _filter_rows(
    rows: List[Dict[str, Any]],
    *,
    source: Optional[str] = None,
    channel: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    source_norm = (source or "").strip().lower() or None
    channel_norm = (channel or "").strip().lower() or None
    query_norm = (query or "").strip().lower() or None

    filtered = rows

    if source_norm:
        filtered = [
            row
            for row in filtered
            if str(row.get("source") or "").strip().lower() == source_norm
        ]

    if channel_norm:
        filtered = [
            row
            for row in filtered
            if str(row.get("channel") or "").strip().lower() == channel_norm
        ]

    if query_norm:
        matched: List[Dict[str, Any]] = []
        for row in filtered:
            haystack = " ".join(
                [
                    str(row.get("question") or ""),
                    str(row.get("answer") or ""),
                    str(row.get("lang") or ""),
                    str(row.get("source") or ""),
                    str(row.get("channel") or ""),
                    str(row.get("canonical_key") or ""),
                    str(row.get("normalized_question") or ""),
                ]
            ).lower()
            if query_norm in haystack:
                matched.append(row)
        filtered = matched

    return filtered


def history_table_ready() -> Dict[str, Any]:
    exists = _has_table(HISTORY_TABLE)
    return {
        "ok": True,
        "table": HISTORY_TABLE,
        "exists": exists,
        "available_columns": _available_columns() if exists else [],
    }


def log_history_item_best_effort(
    *,
    account_id: str,
    question: str,
    answer: str,
    lang: str = "en",
    source: str = "web",
    from_cache: bool = False,
    canonical_key: Optional[str] = None,
    normalized_question: Optional[str] = None,
    plan_code: Optional[str] = None,
    credits_consumed: int = 0,
    usage_charged: bool = False,
    channel: str = "web",
) -> None:
    account_id = str(account_id or "").strip()
    question = str(question or "").strip()
    answer = str(answer or "").strip()
    lang = _normalize_lang(lang)
    channel = _normalize_channel(channel)
    source = _normalize_source(source, channel)
    canonical_key = _normalize_text(canonical_key)
    normalized_question = _normalize_text(normalized_question)
    plan_code = _normalize_text(plan_code)

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

    safe_payload = {key: value for key, value in payload.items() if _has_column(HISTORY_TABLE, key)}
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
    channel: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Authenticate first so a canonical account_id can be resolved.",
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
        safe_limit = max(1, min(int(limit or 50), 500))
    except Exception:
        safe_limit = 50

    try:
        res = (
            _sb()
            .table(HISTORY_TABLE)
            .select(_safe_select_cols())
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        raw_rows = getattr(res, "data", None) or []
        items = [_row_to_item(row) for row in raw_rows]
        items = _filter_rows(items, source=source, channel=channel, query=query)
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "account_id": account_id,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "history_lookup_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            "fix": f"Check {HISTORY_TABLE} table access, columns, and Supabase connectivity.",
            "details": {"table": HISTORY_TABLE, "account_id": account_id},
        }


def get_history_item(*, account_id: str, item_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    item_id = str(item_id or "").strip()

    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Authenticate first so a canonical account_id can be resolved.",
        }

    if not item_id:
        return {
            "ok": False,
            "error": "item_id_required",
            "root_cause": "missing_item_id",
            "fix": "Provide the history item id.",
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
        res = (
            _sb()
            .table(HISTORY_TABLE)
            .select(_safe_select_cols())
            .eq("account_id", account_id)
            .eq("id", item_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else None
        if not row:
            return {
                "ok": False,
                "error": "history_item_not_found",
                "root_cause": "No matching history item was found for this account.",
                "fix": "Use an existing history item id for the authenticated account.",
                "details": {"item_id": item_id},
            }
        return {"ok": True, "item": _row_to_item(row), "account_id": account_id}
    except Exception as exc:
        return {
            "ok": False,
            "error": "history_item_lookup_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            "fix": f"Check {HISTORY_TABLE} table access, columns, and Supabase connectivity.",
            "details": {"table": HISTORY_TABLE, "item_id": item_id, "account_id": account_id},
        }


def delete_history_item(*, account_id: str, item_id: str) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    item_id = str(item_id or "").strip()

    lookup = get_history_item(account_id=account_id, item_id=item_id)
    if not lookup.get("ok"):
        return lookup

    try:
        _sb().table(HISTORY_TABLE).delete().eq("account_id", account_id).eq("id", item_id).execute()
        return {
            "ok": True,
            "deleted": True,
            "item_id": item_id,
            "account_id": account_id,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "history_item_delete_failed",
            "root_cause": f"{type(exc).__name__}: {_clip(exc)}",
            "fix": f"Check delete access to {HISTORY_TABLE} and verify the item still exists.",
            "details": {"table": HISTORY_TABLE, "item_id": item_id, "account_id": account_id},
        }


def clear_history_items(
    *,
    account_id: str,
    source: Optional[str] = None,
    channel: Optional[str] = None,
) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
        return {
            "ok": False,
            "error": "account_id_required",
            "root_cause": "missing_account_id",
            "fix": "Authenticate first so a canonical account_id can be resolved.",
        }

    listed = list_history_items(
        account_id=account_id,
        limit=5000,
        source=source,
        channel=channel,
        query=None,
    )
    if not listed.get("ok"):
        return listed

    items = listed.get("items", [])
    if not items:
        return {
            "ok": True,
            "deleted": 0,
            "account_id": account_id,
            "source": (source or "").strip().lower() or None,
            "channel": (channel or "").strip().lower() or None,
        }

    item_ids = [str(item.get("id") or "").strip() for item in items if str(item.get("id") or "").strip()]
    deleted = 0
    errors: List[Dict[str, Any]] = []

    for item_id in item_ids:
        try:
            _sb().table(HISTORY_TABLE).delete().eq("account_id", account_id).eq("id", item_id).execute()
            deleted += 1
        except Exception as exc:
            errors.append({"item_id": item_id, "root_cause": f"{type(exc).__name__}: {_clip(exc)}"})

    if errors:
        return {
            "ok": False,
            "error": "history_clear_partial_failure",
            "root_cause": "Some history items could not be deleted.",
            "fix": "Retry the clear action or inspect delete permissions for qa_history.",
            "deleted": deleted,
            "errors": errors,
            "account_id": account_id,
        }

    return {
        "ok": True,
        "deleted": deleted,
        "account_id": account_id,
        "source": (source or "").strip().lower() or None,
        "channel": (channel or "").strip().lower() or None,
    }


def history_summary(
    *,
    account_id: str,
    source: Optional[str] = None,
    channel: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    listed = list_history_items(
        account_id=account_id,
        limit=5000,
        source=source,
        channel=channel,
        query=query,
    )
    if not listed.get("ok"):
        return listed

    items = listed.get("items", [])
    by_source: Dict[str, int] = {}
    by_channel: Dict[str, int] = {}
    by_lang: Dict[str, int] = {}
    dates = [str(item.get("created_at") or "").strip() for item in items if str(item.get("created_at") or "").strip()]

    for item in items:
        item_source = str(item.get("source") or "unknown").strip().lower() or "unknown"
        item_channel = str(item.get("channel") or item_source or "unknown").strip().lower() or "unknown"
        item_lang = str(item.get("lang") or "en").strip().lower() or "en"
        by_source[item_source] = by_source.get(item_source, 0) + 1
        by_channel[item_channel] = by_channel.get(item_channel, 0) + 1
        by_lang[item_lang] = by_lang.get(item_lang, 0) + 1

    sorted_dates = sorted(dates)
    return {
        "ok": True,
        "account_id": account_id,
        "count": len(items),
        "by_source": by_source,
        "by_channel": by_channel,
        "by_lang": by_lang,
        "oldest_created_at": sorted_dates[0] if sorted_dates else None,
        "newest_created_at": sorted_dates[-1] if sorted_dates else None,
        "storage_mode": "server",
    }
