from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request, session

from app.services.history_service import (
    HistoryServiceError,
    clear_items,
    delete_item,
    get_item,
    get_summary,
    healthcheck,
    list_items,
    save_item,
)

bp = Blueprint("history", __name__)


def _request_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_account_id(body: dict[str, Any] | None = None) -> str | None:
    payload = body or {}

    candidates = [
        getattr(g, "account_id", None),
        getattr(g, "web_account_id", None),
        getattr(g, "current_account_id", None),
        getattr(g, "authenticated_account_id", None),
        session.get("account_id"),
        session.get("web_account_id"),
        request.headers.get("X-Account-Id"),
        request.args.get("account_id"),
        payload.get("account_id"),
    ]

    current_account = getattr(g, "current_account", None)
    if isinstance(current_account, dict):
        candidates.insert(0, current_account.get("id"))

    for candidate in candidates:
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value

    return None


def _ok(payload: dict[str, Any], status: int = 200):
    payload.setdefault("ok", True)
    return jsonify(payload), status


def _error(message: str, status: int = 400, **extra: Any):
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


@bp.get("/history")
@bp.get("/history/")
@bp.get("/history/items")
@bp.get("/history/list")
def history_list():
    account_id = _resolve_account_id()
    if not account_id:
        return _error("Authenticated account_id is required for history listing.", 401)

    query = (request.args.get("q") or request.args.get("query") or request.args.get("search") or "").strip()
    source = (request.args.get("source") or "all").strip().lower()
    lang = (request.args.get("lang") or "all").strip().lower()
    limit = _to_int(request.args.get("limit"), 50)
    offset = _to_int(request.args.get("offset"), 0)

    try:
        items_payload = list_items(
            account_id=account_id,
            source=source,
            lang=lang,
            query=query,
            limit=limit,
            offset=offset,
        )
        summary_payload = get_summary(
            account_id=account_id,
            source=source,
            lang=lang,
            query=query,
        )

        return _ok(
            {
                "account_id": account_id,
                "items": items_payload["items"],
                "results": items_payload["items"],
                "history": items_payload["items"],
                "total": items_payload["total"],
                "count": items_payload["total"],
                "limit": items_payload["limit"],
                "offset": items_payload["offset"],
                "summary": summary_payload,
                "stats": summary_payload,
                "backend_history_available": True,
                "storage_mode": "backend",
                "fallback_mode": False,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local")


@bp.get("/history/summary")
@bp.get("/history/status")
def history_summary():
    account_id = _resolve_account_id()
    if not account_id:
        return _error("Authenticated account_id is required for history summary.", 401)

    query = (request.args.get("q") or request.args.get("query") or request.args.get("search") or "").strip()
    source = (request.args.get("source") or "all").strip().lower()
    lang = (request.args.get("lang") or "all").strip().lower()

    try:
        summary = get_summary(account_id=account_id, source=source, lang=lang, query=query)
        return _ok(summary)
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local")


@bp.get("/history/health")
def history_health():
    try:
        result = healthcheck()
        return _ok(result)
    except HistoryServiceError as exc:
        return _error(str(exc), 503, backend_history_available=False, storage_mode="local")


@bp.get("/history/item/<item_id>")
@bp.get("/history/items/<item_id>")
def history_get_item(item_id: str):
    account_id = _resolve_account_id()
    if not account_id:
        return _error("Authenticated account_id is required for history item lookup.", 401)

    try:
        item = get_item(account_id=account_id, item_id=item_id)
        if not item:
            return _error("History item not found.", 404)

        return _ok(
            {
                "item": item,
                "history_item": item,
                "saved_item": item,
                "backend_history_available": True,
                "storage_mode": "backend",
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local")


@bp.post("/history/save")
@bp.post("/history/items")
def history_save():
    body = _request_body()
    account_id = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history save.", 401)

    question = str(body.get("question") or "").strip()
    answer = str(body.get("answer") or "").strip()
    lang = str(body.get("lang") or body.get("language") or "en").strip().lower()
    source = str(body.get("source") or "web").strip().lower()
    from_cache = bool(body.get("from_cache") or False)
    canonical_key = body.get("canonical_key")

    try:
        item = save_item(
            account_id=account_id,
            question=question,
            answer=answer,
            lang=lang,
            source=source,
            from_cache=from_cache,
            canonical_key=canonical_key,
        )
        summary = get_summary(account_id=account_id)

        return _ok(
            {
                "saved": True,
                "item": item,
                "history_item": item,
                "saved_item": item,
                "summary": summary,
                "stats": summary,
                "backend_history_available": True,
                "storage_mode": "backend",
            },
            201,
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local")


@bp.post("/history/delete")
def history_delete():
    body = _request_body()
    account_id = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history delete.", 401)

    item_id = str(body.get("item_id") or body.get("id") or "").strip()
    if not item_id:
        return _error("item_id is required.", 400)

    try:
        result = delete_item(account_id=account_id, item_id=item_id)
        summary = get_summary(account_id=account_id)

        return _ok(
            {
                **result,
                "summary": summary,
                "stats": summary,
                "backend_history_available": True,
                "storage_mode": "backend",
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local")


@bp.delete("/history/item/<item_id>")
@bp.delete("/history/items/<item_id>")
def history_delete_by_path(item_id: str):
    account_id = _resolve_account_id()
    if not account_id:
        return _error("Authenticated account_id is required for history delete.", 401)

    try:
        result = delete_item(account_id=account_id, item_id=item_id)
        summary = get_summary(account_id=account_id)

        return _ok(
            {
                **result,
                "summary": summary,
                "stats": summary,
                "backend_history_available": True,
                "storage_mode": "backend",
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local")


@bp.post("/history/clear")
@bp.delete("/history/clear")
def history_clear():
    body = _request_body()
    account_id = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history clear.", 401)

    source = str(body.get("source") or request.args.get("source") or "all").strip().lower()

    try:
        result = clear_items(account_id=account_id, source=source)
        summary = get_summary(account_id=account_id)

        return _ok(
            {
                **result,
                "summary": summary,
                "stats": summary,
                "backend_history_available": True,
                "storage_mode": "backend",
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local")
