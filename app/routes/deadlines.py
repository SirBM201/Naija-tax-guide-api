# app/routes/deadlines.py
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover
    get_account_id_from_request = None  # type: ignore

logger = logging.getLogger(__name__)

# Do NOT add url_prefix here. app/__init__.py registers this blueprint with /api.
bp = Blueprint("deadlines", __name__)

ALLOWED_TAX_TYPES = {"paye", "vat", "cit", "wht", "pension", "nsitf", "itf", "custom"}


def _sb():
    return supabase() if callable(supabase) else supabase


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_account_id() -> Tuple[Optional[str], Dict[str, Any]]:
    debug: Dict[str, Any] = {
        "resolver": "deadlines_v1",
        "flask_session_checked": True,
        "flask_session_user_found": False,
        "web_token_checked": False,
    }

    try:
        user = get_current_user()
    except Exception as exc:
        user = None
        debug["flask_session_error"] = f"{type(exc).__name__}: {exc}"

    if user:
        debug["flask_session_user_found"] = True
        debug["flask_session_user_keys"] = sorted(list(user.keys()))
        account_id = _clean(user.get("account_id")) or _clean(user.get("id"))
        if account_id:
            debug["account_source"] = "flask_session"
            return account_id, debug

    if get_account_id_from_request is not None:
        try:
            debug["web_token_checked"] = True
            account_id, token_debug = get_account_id_from_request(request)  # type: ignore[misc]
            account_id = _clean(account_id)
            debug["web_token_debug"] = token_debug
            if account_id:
                debug["account_source"] = "web_token"
                return account_id, debug
        except Exception as exc:
            debug["web_token_error"] = f"{type(exc).__name__}: {exc}"

    debug["root_cause"] = "No logged-in account was resolved from ntg_session or web token."
    return None, debug


def _json_error(message: str, status: int, **extra: Any):
    payload: Dict[str, Any] = {"ok": False, "error": message, "message": message}
    payload.update(extra)
    return jsonify(payload), status


def _parse_due_date(value: Any) -> Optional[str]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        # Accept YYYY-MM-DD or ISO datetime and store date portion.
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        return date.fromisoformat(raw[:10]).isoformat()
    except Exception:
        return None


def _deadline_payload(body: Dict[str, Any], account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Any, int]]]:
    tax_type = _clean(body.get("tax_type") or body.get("taxType")).lower()
    if not tax_type:
        return None, _json_error("tax_type is required", 400)
    if tax_type not in ALLOWED_TAX_TYPES:
        # Keep custom values safe but not blocking; store as custom if unknown.
        tax_type = "custom"

    due_date = _parse_due_date(body.get("due_date") or body.get("dueDate"))
    if not due_date:
        return None, _json_error("due_date is required and must be YYYY-MM-DD", 400)

    try:
        reminder_days_before = int(body.get("reminder_days_before", body.get("reminderDaysBefore", 7)) or 7)
    except Exception:
        reminder_days_before = 7
    reminder_days_before = max(1, min(reminder_days_before, 365))

    enabled = body.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}
    else:
        enabled = bool(enabled)

    payload = {
        "user_id": account_id,
        "account_id": account_id,
        "tax_type": tax_type,
        "due_date": due_date,
        "reminder_days_before": reminder_days_before,
        "enabled": enabled,
        "updated_at": _now_iso(),
    }
    return payload, None


@bp.get("/deadlines/health")
def deadlines_health():
    return jsonify({"ok": True, "service": "deadlines", "version": "v1"}), 200


@bp.get("/deadlines")
def list_deadlines():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("unauthorized", 401, debug=debug)

    requested_user_id = _clean(request.args.get("userId") or request.args.get("user_id"))
    if requested_user_id and requested_user_id != account_id:
        return _json_error("forbidden_account_mismatch", 403, requested_user_id=requested_user_id, account_id=account_id)

    try:
        res = (
            _sb()
            .table("tax_deadlines")
            .select("*")
            .eq("user_id", account_id)
            .order("due_date", desc=False)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return jsonify({"ok": True, "account_id": account_id, "deadlines": rows, "debug": debug}), 200
    except Exception as exc:
        logger.exception("List deadlines error")
        return _json_error("deadline_list_failed", 500, root_cause=f"{type(exc).__name__}: {exc}")


@bp.post("/deadlines")
def create_deadline():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("unauthorized", 401, debug=debug)

    body = request.get_json(silent=True) or {}
    payload, error = _deadline_payload(body, account_id)
    if error:
        return error

    try:
        payload = dict(payload or {})
        payload["created_at"] = _now_iso()
        res = _sb().table("tax_deadlines").insert(payload).execute()
        rows = getattr(res, "data", None) or []
        return jsonify({"ok": True, "account_id": account_id, "deadline": rows[0] if rows else payload}), 201
    except Exception as exc:
        logger.exception("Create deadline error")
        return _json_error("deadline_create_failed", 500, root_cause=f"{type(exc).__name__}: {exc}")


@bp.put("/deadlines")
@bp.patch("/deadlines")
def update_deadline():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("unauthorized", 401, debug=debug)

    body = request.get_json(silent=True) or {}
    deadline_id = _clean(body.get("id"))
    if not deadline_id:
        return _json_error("id is required", 400)

    requested_user_id = _clean(body.get("userId") or body.get("user_id"))
    if requested_user_id and requested_user_id != account_id:
        return _json_error("forbidden_account_mismatch", 403, requested_user_id=requested_user_id, account_id=account_id)

    payload, error = _deadline_payload(body, account_id)
    if error:
        return error

    try:
        res = (
            _sb()
            .table("tax_deadlines")
            .update(payload)
            .eq("id", deadline_id)
            .eq("user_id", account_id)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return jsonify({"ok": True, "account_id": account_id, "deadline": rows[0] if rows else {"id": deadline_id, **(payload or {})}}), 200
    except Exception as exc:
        logger.exception("Update deadline error")
        return _json_error("deadline_update_failed", 500, root_cause=f"{type(exc).__name__}: {exc}")


@bp.delete("/deadlines")
def delete_deadline():
    account_id, debug = _resolve_account_id()
    if not account_id:
        return _json_error("unauthorized", 401, debug=debug)

    deadline_id = _clean(request.args.get("id"))
    if not deadline_id:
        return _json_error("id is required", 400)

    requested_user_id = _clean(request.args.get("userId") or request.args.get("user_id"))
    if requested_user_id and requested_user_id != account_id:
        return _json_error("forbidden_account_mismatch", 403, requested_user_id=requested_user_id, account_id=account_id)

    try:
        _sb().table("tax_deadlines").delete().eq("id", deadline_id).eq("user_id", account_id).execute()
        return jsonify({"ok": True, "deleted": True, "id": deadline_id, "account_id": account_id}), 200
    except Exception as exc:
        logger.exception("Delete deadline error")
        return _json_error("deadline_delete_failed", 500, root_cause=f"{type(exc).__name__}: {exc}")
