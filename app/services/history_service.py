from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests


class HistoryServiceError(Exception):
    pass


def _supabase_url() -> str:
    value = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    if not value:
        raise HistoryServiceError("SUPABASE_URL is not configured.")
    return value


def _supabase_key() -> str:
    value = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    if not value:
        raise HistoryServiceError("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY is not configured.")
    return value


def _rest_url(table_name: str) -> str:
    return f"{_supabase_url()}/rest/v1/{table_name}"


def _timeout() -> int:
    raw = os.getenv("SUPABASE_HTTP_TIMEOUT", "30").strip()
    try:
        return max(5, int(raw))
    except ValueError:
        return 30


def _headers(*, count_exact: bool = False, return_representation: bool = False) -> dict[str, str]:
    key = _supabase_key()
    prefer_parts: list[str] = []
    if count_exact:
        prefer_parts.append("count=exact")
    if return_representation:
        prefer_parts.append("return=representation")

    headers: dict[str, str] = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer_parts:
        headers["Prefer"] = ",".join(prefer_parts)
    return headers


def _extract_count(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range", "")
    if "/" not in content_range:
        return None
    try:
        return int(content_range.split("/")[-1])
    except (TypeError, ValueError):
        return None


def _safe_json(response: requests.Response) -> Any:
    if not response.text.strip():
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _raise_for_bad_response(response: requests.Response, fallback_message: str) -> None:
    if response.ok:
        return

    payload = _safe_json(response)
    if isinstance(payload, dict):
        message = (
            payload.get("message")
            or payload.get("error_description")
            or payload.get("hint")
            or payload.get("details")
            or payload.get("error")
            or fallback_message
        )
    else:
        message = fallback_message

    raise HistoryServiceError(f"{message} (status={response.status_code})")


def _truncate_answer(text: str, length: int = 240) -> str:
    value = (text or "").strip()
    if len(value) <= length:
        return value
    return value[: length - 3].rstrip() + "..."


def _build_or_search(query: str) -> str | None:
    text = (query or "").strip()
    if not text:
        return None

    escaped = text.replace("%", "").replace(",", " ")
    pattern = f"*{escaped}*"
    return f"(question.ilike.{pattern},answer.ilike.{pattern})"


def healthcheck() -> dict[str, Any]:
    url = _rest_url("qa_history")
    params = {
        "select": "id",
        "limit": "1",
    }
    response = requests.get(url, headers=_headers(), params=params, timeout=_timeout())
    _raise_for_bad_response(response, "History healthcheck failed.")

    return {
        "ok": True,
        "table": "qa_history",
        "backend_history_available": True,
        "storage_mode": "backend",
    }


def count_items(
    *,
    account_id: str,
    source: str | None = None,
    lang: str | None = None,
    query: str | None = None,
) -> int:
    url = _rest_url("qa_history")
    params: dict[str, str] = {
        "select": "id",
        "account_id": f"eq.{account_id}",
        "limit": "1",
    }

    normalized_source = (source or "").strip().lower()
    if normalized_source and normalized_source != "all":
        params["source"] = f"eq.{normalized_source}"

    normalized_lang = (lang or "").strip().lower()
    if normalized_lang and normalized_lang != "all":
        params["lang"] = f"eq.{normalized_lang}"

    or_search = _build_or_search(query or "")
    if or_search:
        params["or"] = or_search

    response = requests.get(
        url,
        headers=_headers(count_exact=True),
        params=params,
        timeout=_timeout(),
    )
    _raise_for_bad_response(response, "Failed to count history items.")
    return _extract_count(response) or 0


def list_items(
    *,
    account_id: str,
    source: str | None = None,
    lang: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    url = _rest_url("qa_history")
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    params: dict[str, str] = {
        "select": "id,account_id,question,answer,lang,source,from_cache,canonical_key,created_at,updated_at",
        "account_id": f"eq.{account_id}",
        "order": "created_at.desc.nullslast,id.desc",
        "limit": str(safe_limit),
        "offset": str(safe_offset),
    }

    normalized_source = (source or "").strip().lower()
    if normalized_source and normalized_source != "all":
        params["source"] = f"eq.{normalized_source}"

    normalized_lang = (lang or "").strip().lower()
    if normalized_lang and normalized_lang != "all":
        params["lang"] = f"eq.{normalized_lang}"

    or_search = _build_or_search(query or "")
    if or_search:
        params["or"] = or_search

    response = requests.get(
        url,
        headers=_headers(count_exact=True),
        params=params,
        timeout=_timeout(),
    )
    _raise_for_bad_response(response, "Failed to load history items.")

    rows = _safe_json(response) or []
    if not isinstance(rows, list):
        rows = []

    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        answer = str(row.get("answer") or "")
        item = {
            "id": row.get("id"),
            "account_id": row.get("account_id"),
            "question": str(row.get("question") or ""),
            "answer": answer,
            "answer_preview": _truncate_answer(answer),
            "lang": str(row.get("lang") or "en"),
            "source": str(row.get("source") or "web"),
            "from_cache": bool(row.get("from_cache") or False),
            "canonical_key": row.get("canonical_key"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        items.append(item)

    total = _extract_count(response)
    if total is None:
        total = len(items)

    return {
        "ok": True,
        "items": items,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "storage_mode": "backend",
        "backend_history_available": True,
    }


def get_item(*, account_id: str, item_id: str) -> dict[str, Any] | None:
    url = _rest_url("qa_history")
    params = {
        "select": "id,account_id,question,answer,lang,source,from_cache,canonical_key,created_at,updated_at",
        "account_id": f"eq.{account_id}",
        "id": f"eq.{item_id}",
        "limit": "1",
    }

    response = requests.get(url, headers=_headers(), params=params, timeout=_timeout())
    _raise_for_bad_response(response, "Failed to load history item.")

    rows = _safe_json(response) or []
    if not isinstance(rows, list) or not rows:
        return None

    row = rows[0]
    answer = str(row.get("answer") or "")
    return {
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "question": str(row.get("question") or ""),
        "answer": answer,
        "answer_preview": _truncate_answer(answer),
        "lang": str(row.get("lang") or "en"),
        "source": str(row.get("source") or "web"),
        "from_cache": bool(row.get("from_cache") or False),
        "canonical_key": row.get("canonical_key"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def save_item(
    *,
    account_id: str,
    question: str,
    answer: str,
    lang: str = "en",
    source: str = "web",
    from_cache: bool = False,
    canonical_key: str | None = None,
) -> dict[str, Any]:
    question_clean = (question or "").strip()
    answer_clean = (answer or "").strip()
    lang_clean = (lang or "en").strip().lower() or "en"
    source_clean = (source or "web").strip().lower() or "web"

    if not question_clean:
        raise HistoryServiceError("Question is required.")
    if not answer_clean:
        raise HistoryServiceError("Answer is required.")
    if not account_id:
        raise HistoryServiceError("Account ID is required.")

    url = _rest_url("qa_history")
    payload = [
        {
            "account_id": account_id,
            "question": question_clean,
            "answer": answer_clean,
            "lang": lang_clean,
            "source": source_clean,
            "from_cache": bool(from_cache),
            "canonical_key": canonical_key,
        }
    ]

    response = requests.post(
        url,
        headers=_headers(return_representation=True),
        json=payload,
        timeout=_timeout(),
    )
    _raise_for_bad_response(response, "Failed to save history item.")

    rows = _safe_json(response) or []
    if not isinstance(rows, list) or not rows:
        raise HistoryServiceError("History save returned no row.")

    row = rows[0]
    saved_answer = str(row.get("answer") or answer_clean)
    return {
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "question": str(row.get("question") or question_clean),
        "answer": saved_answer,
        "answer_preview": _truncate_answer(saved_answer),
        "lang": str(row.get("lang") or lang_clean),
        "source": str(row.get("source") or source_clean),
        "from_cache": bool(row.get("from_cache") or False),
        "canonical_key": row.get("canonical_key"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def delete_item(*, account_id: str, item_id: str) -> dict[str, Any]:
    if not account_id:
        raise HistoryServiceError("Account ID is required.")
    if not item_id:
        raise HistoryServiceError("Item ID is required.")

    existing = get_item(account_id=account_id, item_id=item_id)
    if not existing:
        return {
            "ok": True,
            "deleted": False,
            "deleted_count": 0,
            "item_id": item_id,
        }

    url = _rest_url("qa_history")
    params = {
        "account_id": f"eq.{account_id}",
        "id": f"eq.{item_id}",
    }

    response = requests.delete(
        url,
        headers=_headers(return_representation=True),
        params=params,
        timeout=_timeout(),
    )
    _raise_for_bad_response(response, "Failed to delete history item.")

    rows = _safe_json(response) or []
    deleted_count = len(rows) if isinstance(rows, list) else 0

    return {
        "ok": True,
        "deleted": deleted_count > 0,
        "deleted_count": deleted_count,
        "item_id": item_id,
    }


def clear_items(*, account_id: str, source: str | None = None) -> dict[str, Any]:
    if not account_id:
        raise HistoryServiceError("Account ID is required.")

    url = _rest_url("qa_history")
    params = {
        "account_id": f"eq.{account_id}",
    }

    normalized_source = (source or "").strip().lower()
    if normalized_source and normalized_source != "all":
        params["source"] = f"eq.{normalized_source}"

    before_count = count_items(account_id=account_id, source=normalized_source or None)

    response = requests.delete(
        url,
        headers=_headers(return_representation=True),
        params=params,
        timeout=_timeout(),
    )
    _raise_for_bad_response(response, "Failed to clear history.")

    rows = _safe_json(response) or []
    deleted_count = len(rows) if isinstance(rows, list) else before_count

    return {
        "ok": True,
        "cleared": True,
        "deleted_count": deleted_count,
        "source": normalized_source or "all",
    }


def get_summary(
    *,
    account_id: str,
    query: str | None = None,
    source: str | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    total_saved = count_items(account_id=account_id)
    filtered_results = count_items(
        account_id=account_id,
        source=source,
        lang=lang,
        query=query,
    )
    web_count = count_items(account_id=account_id, source="web")
    telegram_count = count_items(account_id=account_id, source="telegram")
    whatsapp_count = count_items(account_id=account_id, source="whatsapp")

    newest_items = list_items(account_id=account_id, limit=1, offset=0)
    newest = newest_items["items"][0]["created_at"] if newest_items["items"] else None

    oldest_url = _rest_url("qa_history")
    oldest_params = {
        "select": "id,created_at",
        "account_id": f"eq.{account_id}",
        "order": "created_at.asc.nullslast,id.asc",
        "limit": "1",
    }
    oldest_response = requests.get(
        oldest_url,
        headers=_headers(),
        params=oldest_params,
        timeout=_timeout(),
    )
    _raise_for_bad_response(oldest_response, "Failed to load oldest history item.")
    oldest_rows = _safe_json(oldest_response) or []
    oldest = oldest_rows[0].get("created_at") if isinstance(oldest_rows, list) and oldest_rows else None

    return {
        "ok": True,
        "backend_history_available": True,
        "storage_mode": "backend",
        "continuity_state": "Available" if total_saved > 0 else "Empty",
        "saved_items": total_saved,
        "filtered_results": filtered_results,
        "daily_usage": 0,
        "expires_at": None,
        "newest_item": newest,
        "oldest_item": oldest,
        "source_counts": {
            "web": web_count,
            "telegram": telegram_count,
            "whatsapp": whatsapp_count,
        },
    }
