# app/routes/history.py
from __future__ import annotations

from typing import Any, Optional, Tuple

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

try:
    from app.services.auth_service import get_current_user
except Exception:  # pragma: no cover - keep route boot-safe if auth service changes
    get_current_user = None  # type: ignore

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover - keep route boot-safe if web auth service changes
    get_account_id_from_request = None  # type: ignore

bp = Blueprint("history", __name__)

HISTORY_ROUTE_VERSION = "2026-05-23-v2-web-token-auth-safe"


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def _request_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clip(value: Any, n: int = 1200) -> str:
    text = str(value or "")
    return text if len(text) <= n else text[:n] + "...<truncated>"


def _ok(payload: dict[str, Any], status: int = 200):
    payload.setdefault("ok", True)
    payload.setdefault("history_route_version", HISTORY_ROUTE_VERSION)
    return jsonify(payload), status


def _error(message: str, status: int = 400, **extra: Any):
    payload: dict[str, Any] = {
        "ok": False,
        "error": message,
        "history_route_version": HISTORY_ROUTE_VERSION,
    }
    payload.update(extra)
    return jsonify(payload), status


# -----------------------------------------------------------------------------
# Authentication resolver
# -----------------------------------------------------------------------------


def _resolve_from_flask_g_or_session(payload: dict[str, Any]) -> Tuple[Optional[str], dict[str, Any]]:
    debug: dict[str, Any] = {"checked": True, "source": None}

    current_account = getattr(g, "current_account", None)
    if isinstance(current_account, dict):
        for key in ("account_id", "id"):
            value = _clean(current_account.get(key))
            if value:
                debug["source"] = f"g.current_account.{key}"
                return value, debug

    candidates = [
        ("g.account_id", getattr(g, "account_id", None)),
        ("g.web_account_id", getattr(g, "web_account_id", None)),
        ("g.current_account_id", getattr(g, "current_account_id", None)),
        ("g.authenticated_account_id", getattr(g, "authenticated_account_id", None)),
        ("session.account_id", session.get("account_id")),
        ("session.web_account_id", session.get("web_account_id")),
        ("session.user_id", session.get("user_id")),
        ("header.X-Account-Id", request.headers.get("X-Account-Id")),
        ("header.X-Web-Account-Id", request.headers.get("X-Web-Account-Id")),
        ("query.account_id", request.args.get("account_id")),
        ("body.account_id", payload.get("account_id")),
    ]

    for source, candidate in candidates:
        value = _clean(candidate)
        if value:
            debug["source"] = source
            return value, debug

    return None, debug


def _resolve_from_current_user() -> Tuple[Optional[str], dict[str, Any]]:
    debug: dict[str, Any] = {"checked": bool(get_current_user), "source": None}
    if get_current_user is None:
        return None, debug

    try:
        user = get_current_user()  # type: ignore[misc]
    except Exception as exc:
        debug["error"] = f"{type(exc).__name__}: {_clip(exc)}"
        return None, debug

    if not isinstance(user, dict) or not user:
        debug["user_found"] = False
        return None, debug

    debug["user_found"] = True
    debug["user_keys"] = sorted(list(user.keys()))

    for key in ("account_id", "id", "web_account_id", "current_account_id"):
        value = _clean(user.get(key))
        if value:
            debug["source"] = f"auth_service.current_user.{key}"
            return value, debug

    return None, debug


def _resolve_from_web_token() -> Tuple[Optional[str], dict[str, Any]]:
    debug: dict[str, Any] = {"checked": bool(get_account_id_from_request), "source": None}
    if get_account_id_from_request is None:
        return None, debug

    try:
        result = get_account_id_from_request(request)  # type: ignore[misc]
    except Exception as exc:
        debug["error"] = f"{type(exc).__name__}: {_clip(exc)}"
        return None, debug

    token_debug: Any = None
    account_id: Optional[str] = None

    if isinstance(result, tuple):
        account_id = result[0]
        token_debug = result[1] if len(result) > 1 else None
    else:
        account_id = result

    value = _clean(account_id)
    if token_debug is not None:
        debug["web_token_debug"] = token_debug

    if value:
        debug["source"] = "web_auth_service.get_account_id_from_request"
        return value, debug

    return None, debug


def _resolve_account_id(body: dict[str, Any] | None = None) -> Tuple[Optional[str], dict[str, Any]]:
    """
    Resolve the authenticated account for history routes.

    Why this exists:
    - The web app uses token/cookie based auth.
    - /api/workspace/limits, /api/billing/me, and /api/web/auth/me can resolve that token.
    - The old history route only checked Flask g/session/header/body values, so /api/history/items
      could return 401 even when the user was already logged in.

    Order is deliberately broad but safe:
    1. Flask g/session/header/body compatibility.
    2. Current auth_service user, if available.
    3. web_auth_service token/cookie resolver, matching the working web routes.
    """
    payload = body or {}
    debug: dict[str, Any] = {
        "resolver": "history_v2_web_token_auth_safe",
        "route_version": HISTORY_ROUTE_VERSION,
    }

    account_id, g_debug = _resolve_from_flask_g_or_session(payload)
    debug["g_or_session"] = g_debug
    if account_id:
        debug["account_source"] = g_debug.get("source") or "g_or_session"
        return account_id, debug

    account_id, user_debug = _resolve_from_current_user()
    debug["current_user"] = user_debug
    if account_id:
        debug["account_source"] = user_debug.get("source") or "current_user"
        return account_id, debug

    account_id, token_debug = _resolve_from_web_token()
    debug["web_token"] = token_debug
    if account_id:
        debug["account_source"] = token_debug.get("source") or "web_token"
        return account_id, debug

    debug["root_cause"] = "No authenticated account_id found in Flask session, current user, header/body, or web token cookie."
    return None, debug


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@bp.get("/history/health")
def history_health():
    try:
        result = healthcheck()
        return _ok(
            {
                **result,
                "service": "history",
                "version": HISTORY_ROUTE_VERSION,
                "auth_resolver": "g/session + auth_service + web_auth_service token cookie",
                "endpoints": [
                    "GET /history/items",
                    "GET /history/summary",
                    "GET /history/item/<item_id>",
                    "POST /history/save",
                    "POST /history/items",
                    "POST /history/delete",
                    "DELETE /history/items/<item_id>",
                    "POST /history/clear",
                    "DELETE /history/clear",
                ],
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 503, backend_history_available=False, storage_mode="local")


@bp.get("/history")
@bp.get("/history/")
@bp.get("/history/items")
@bp.get("/history/list")
def history_list():
    account_id, auth_debug = _resolve_account_id()
    if not account_id:
        return _error(
            "Authenticated account_id is required for history listing.",
            401,
            debug=auth_debug,
            backend_history_available=True,
            storage_mode="backend",
        )

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
                "debug": auth_debug,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.get("/history/summary")
@bp.get("/history/status")
def history_summary():
    account_id, auth_debug = _resolve_account_id()
    if not account_id:
        return _error(
            "Authenticated account_id is required for history summary.",
            401,
            debug=auth_debug,
            backend_history_available=True,
            storage_mode="backend",
        )

    query = (request.args.get("q") or request.args.get("query") or request.args.get("search") or "").strip()
    source = (request.args.get("source") or "all").strip().lower()
    lang = (request.args.get("lang") or "all").strip().lower()

    try:
        summary = get_summary(account_id=account_id, source=source, lang=lang, query=query)
        return _ok({**summary, "account_id": account_id, "debug": auth_debug})
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.get("/history/item/<item_id>")
@bp.get("/history/items/<item_id>")
def history_get_item(item_id: str):
    account_id, auth_debug = _resolve_account_id()
    if not account_id:
        return _error(
            "Authenticated account_id is required for history item lookup.",
            401,
            debug=auth_debug,
        )

    try:
        item = get_item(account_id=account_id, item_id=item_id)
        if not item:
            return _error("History item not found.", 404, debug=auth_debug)

        return _ok(
            {
                "item": item,
                "history_item": item,
                "saved_item": item,
                "backend_history_available": True,
                "storage_mode": "backend",
                "debug": auth_debug,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 500, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.post("/history/save")
@bp.post("/history/items")
def history_save():
    body = _request_body()
    account_id, auth_debug = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history save.", 401, debug=auth_debug)

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
                "debug": auth_debug,
            },
            201,
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.post("/history/delete")
def history_delete():
    body = _request_body()
    account_id, auth_debug = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history delete.", 401, debug=auth_debug)

    item_id = str(body.get("item_id") or body.get("id") or "").strip()
    if not item_id:
        return _error("item_id is required.", 400, debug=auth_debug)

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
                "debug": auth_debug,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.delete("/history/item/<item_id>")
@bp.delete("/history/items/<item_id>")
def history_delete_by_path(item_id: str):
    account_id, auth_debug = _resolve_account_id()
    if not account_id:
        return _error("Authenticated account_id is required for history delete.", 401, debug=auth_debug)

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
                "debug": auth_debug,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local", debug=auth_debug)


@bp.post("/history/clear")
@bp.delete("/history/clear")
def history_clear():
    body = _request_body()
    account_id, auth_debug = _resolve_account_id(body)
    if not account_id:
        return _error("Authenticated account_id is required for history clear.", 401, debug=auth_debug)

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
                "debug": auth_debug,
            }
        )
    except HistoryServiceError as exc:
        return _error(str(exc), 400, backend_history_available=False, storage_mode="local", debug=auth_debug)
