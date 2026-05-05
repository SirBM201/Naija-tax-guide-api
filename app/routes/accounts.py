from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase
from app.services.accounts_service import upsert_account
from app.services.web_auth_service import get_account_id_from_request

bp = Blueprint("accounts", __name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def _safe_json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _clip(v: Any, n: int = 320) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _fail(
    *,
    error: str,
    message: Optional[str] = None,
    root_cause: Any = None,
    extra: Optional[Dict[str, Any]] = None,
    status: int = 400,
):
    out: Dict[str, Any] = {"ok": False, "error": error}
    if message:
        out["message"] = message
    if root_cause is not None:
        out["root_cause"] = root_cause
    if extra:
        out.update(extra)
    return jsonify(out), status


def _has_column(table: str, col: str) -> bool:
    try:
        _sb().table(table).select(col).limit(1).execute()
        return True
    except Exception:
        return False


def _get_account_row(account_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    account_id here is canonical accounts.account_id from web auth.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        return None, {
            "error": "account_id_required",
            "root_cause": "missing_account_id",
        }

    select_cols = [
        "id",
        "account_id",
        "provider",
        "provider_user_id",
        "phone_e164",
        "created_at",
        "updated_at",
        "display_name",
        "phone",
        "auth_user_id",
        "email",
        "has_used_trial",
    ]
    safe_select = ",".join([c for c in select_cols if _has_column("accounts", c)]) or "*"

    try:
        q = (
            _sb()
            .table("accounts")
            .select(safe_select)
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by account_id failed: {type(e).__name__}: {_clip(e)}",
        }

    try:
        q = (
            _sb()
            .table("accounts")
            .select(safe_select)
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(q, "data", None) or []
        if rows:
            return rows[0], None
    except Exception as e:
        return None, {
            "error": "account_lookup_failed",
            "root_cause": f"lookup by id failed: {type(e).__name__}: {_clip(e)}",
        }

    return None, {
        "error": "account_not_found",
        "root_cause": "no accounts row matched provided account_id",
    }


def _normalize_email(v: Any) -> str:
    return str(v or "").strip().lower()


def _build_public_account(row: Dict[str, Any]) -> Dict[str, Any]:
    row = row or {}
    return {
        "account_id": row.get("account_id") or row.get("id"),
        "display_name": row.get("display_name"),
        "email": row.get("email"),
        "phone": row.get("phone"),
        "phone_e164": row.get("phone_e164"),
        "provider": row.get("provider"),
        "provider_user_id": row.get("provider_user_id"),
        "has_used_trial": bool(row.get("has_used_trial", False)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@bp.post("/accounts")
def create_or_get_account():
    """
    Create or find an account by provider identity.

    Body:
      {
        "provider": "whatsapp" | "wa" | "telegram" | "tg" | "web" | ...,
        "provider_user_id": "<string>",
        "display_name": "<optional>",
        "phone": "<optional>"
      }

    Returns canonical account_id = accounts.account_id
    """
    body = request.get_json(silent=True) or {}

    provider = (body.get("provider") or "").strip().lower()
    provider_user_id = (body.get("provider_user_id") or "").strip()

    if not provider or not provider_user_id:
        return jsonify({
            "ok": False,
            "error": "invalid_request",
            "root_cause": "provider or provider_user_id missing",
            "fix": "Send JSON with provider and provider_user_id.",
        }), 400

    res = upsert_account(
        provider=provider,
        provider_user_id=provider_user_id,
        display_name=(body.get("display_name") or None),
        phone=(body.get("phone") or None),
    )

    if not res.get("ok"):
        return jsonify(res), 400

    return jsonify({
        "ok": True,
        "account_id": res.get("account_id"),
        "account": res.get("account"),
    }), 200


@bp.get("/accounts/me")
def account_me():
    """
    Authenticated account profile endpoint for frontend settings/workspace use.
    """
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    row, err = _get_account_row(account_id)
    if err:
        return _fail(
            error=err.get("error") or "account_lookup_failed",
            root_cause=err.get("root_cause"),
            status=404 if err.get("error") == "account_not_found" else 400,
        )

    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "account": _build_public_account(row or {}),
            "debug": {"auth": auth_debug},
        }
    ), 200


@bp.patch("/accounts/me")
def update_account_me():
    """
    Authenticated account settings/profile update endpoint.

    Accepted fields:
      - display_name
      - email
      - phone

    Notes:
    - Only updates columns that already exist in the accounts table.
    - Uses canonical accounts.account_id for row resolution.
    """
    account_id, auth_debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": auth_debug}), 401

    row, err = _get_account_row(account_id)
    if err:
        return _fail(
            error=err.get("error") or "account_lookup_failed",
            root_cause=err.get("root_cause"),
            status=404 if err.get("error") == "account_not_found" else 400,
        )

    body = _safe_json()

    display_name = str(body.get("display_name") or "").strip()
    email = _normalize_email(body.get("email"))
    phone = str(body.get("phone") or "").strip()

    patch: Dict[str, Any] = {}

    if "display_name" in body and _has_column("accounts", "display_name"):
        patch["display_name"] = display_name or None

    if "email" in body and _has_column("accounts", "email"):
        if email and "@" not in email:
            return _fail(
                error="invalid_email",
                message="Email address is invalid.",
                status=400,
            )
        patch["email"] = email or None

    if "phone" in body and _has_column("accounts", "phone"):
        patch["phone"] = phone or None

    if "phone" in body and _has_column("accounts", "phone_e164"):
        patch["phone_e164"] = phone or None

    if _has_column("accounts", "updated_at"):
        from datetime import datetime, timezone
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()

    if not patch:
        return _fail(
            error="nothing_to_update",
            message="No valid updatable fields were provided.",
            extra={
                "allowed_fields": ["display_name", "email", "phone"],
            },
            status=400,
        )

    row_id = str((row or {}).get("id") or "").strip()
    if not row_id:
        return _fail(
            error="account_row_id_missing",
            root_cause="accounts row exists but id is missing",
            status=500,
        )

    try:
        upd = (
            _sb()
            .table("accounts")
            .update(patch)
            .eq("id", row_id)
            .execute()
        )
        out = getattr(upd, "data", None) or []
        updated_row = out[0] if out else None
    except Exception as e:
        return _fail(
            error="account_update_failed",
            root_cause=f"{type(e).__name__}: {_clip(e)}",
            extra={"account_id": account_id},
            status=500,
        )

    if not updated_row:
        refreshed, refresh_err = _get_account_row(account_id)
        if refresh_err:
            return _fail(
                error="account_refresh_failed",
                root_cause=refresh_err.get("root_cause"),
                status=500,
            )
        updated_row = refreshed

    return jsonify(
        {
            "ok": True,
            "message": "Account settings updated successfully.",
            "account_id": account_id,
            "account": _build_public_account(updated_row or {}),
            "debug": {"auth": auth_debug},
        }
    ), 200
