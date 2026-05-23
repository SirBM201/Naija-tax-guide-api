# app/routes/workspace.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify as _flask_jsonify, request

from app.core.supabase_client import supabase
from app.services.account_entitlements_service import (
    count_workspace_members,
    enforce_workspace_member_limit,
    get_account_entitlements,
)
from app.services.auth_service import get_current_user

try:
    from app.services.web_auth_service import get_account_id_from_request
except Exception:  # pragma: no cover - keeps the route boot-safe if web auth service changes
    get_account_id_from_request = None  # type: ignore



try:
    from app.core.response_safety import sanitize_response_payload
except Exception:  # pragma: no cover
    def sanitize_response_payload(payload, request_obj=None):
        return payload


def jsonify(*args, **kwargs):
    """Local safe jsonify wrapper that strips debug/internal payload keys in production."""
    if len(args) == 1 and isinstance(args[0], (dict, list)) and not kwargs:
        return _flask_jsonify(sanitize_response_payload(args[0], request))
    return _flask_jsonify(*args, **kwargs)


logger = logging.getLogger(__name__)

# Do NOT add url_prefix here. app/__init__.py registers this blueprint with /api.
bp = Blueprint("workspace", __name__)


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def _sb():
    return supabase() if callable(supabase) else supabase


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_email(value: Any) -> str:
    return _clean(value).lower()


def _clip(value: Any, n: int = 500) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "...<truncated>"


def _json_error(
    message: str,
    status: int,
    *,
    reason: Optional[str] = None,
    fix: Optional[str] = None,
    root_cause: Optional[Any] = None,
    debug: Optional[Any] = None,
    details: Optional[Any] = None,
):
    payload: Dict[str, Any] = {
        "ok": False,
        "error": reason or message,
        "message": message,
    }
    if reason:
        payload["reason"] = reason
    if fix:
        payload["fix"] = fix
    if root_cause is not None:
        payload["root_cause"] = root_cause
    if debug is not None:
        payload["debug"] = debug
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _account_select() -> str:
    # Keep this list conservative. Selecting a non-existing column can break Supabase queries.
    return "id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at"


def _workspace_select() -> str:
    return "id,owner_account_id,member_account_id,role,status,created_at,updated_at"


def _normalize_account(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out = dict(row)
    if not _clean(out.get("account_id")) and _clean(out.get("id")):
        out["account_id"] = _clean(out.get("id"))
    return out


def _query_one(table: str, select_cols: str, column: str, value: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    value = _clean(value)
    if not value:
        return None, None
    try:
        res = (
            _sb()
            .table(table)
            .select(select_cols)
            .eq(column, value)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return (rows[0] if rows else None), None
    except Exception as exc:
        return None, f"{table}.{column}: {type(exc).__name__}: {_clip(exc)}"


def _get_account_by_any(value: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Resolve an account from any identifier used by the current codebase.

    Current web login sometimes stores accounts.account_id in Flask session.user_id.
    Older route code treated session.user_id as auth_user_id. This function supports both
    so workspace does not fail after a valid login.
    """
    value = _clean(value)
    errors: List[str] = []
    if not value:
        return None, errors

    for column in ("account_id", "id", "auth_user_id", "supabase_user_id", "provider_user_id", "email"):
        row, err = _query_one("accounts", _account_select(), column, value)
        if row:
            return _normalize_account(row), errors
        if err:
            # Missing legacy columns such as auth_user_id/supabase_user_id should not fail the route.
            errors.append(err)

    return None, errors


def _get_account_by_email(email: str) -> Optional[Dict[str, Any]]:
    email = _clean_email(email)
    if not email:
        return None

    for column in ("email", "provider_user_id"):
        row, _err = _query_one("accounts", _account_select(), column, email)
        if row:
            return _normalize_account(row)

    return None


def _resolve_current_account() -> Tuple[Optional[str], Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Resolve the current logged-in account.

    Primary path: Flask session via app.services.auth_service.get_current_user().
    This is the path already proven by /api/me and /api/link/status in the current logs.

    Secondary path: plain-token web session via get_account_id_from_request(), kept for
    compatibility with older/newer login flows.
    """
    debug: Dict[str, Any] = {
        "resolver": "workspace_v2",
        "flask_session_user_found": False,
        "web_token_checked": False,
    }

    # 1) Current working website session path.
    try:
        user = get_current_user()
    except Exception as exc:
        user = None
        debug["flask_session_error"] = f"{type(exc).__name__}: {_clip(exc)}"

    if user:
        debug["flask_session_user_found"] = True
        debug["flask_session_user_keys"] = sorted(list(user.keys()))

        candidate_values = [
            _clean(user.get("account_id")),
            _clean(user.get("id")),
            _clean(user.get("email")),
        ]

        lookup_errors: List[str] = []
        for candidate in candidate_values:
            if not candidate:
                continue
            account, errors = _get_account_by_any(candidate)
            lookup_errors.extend(errors)
            if account:
                account_id = _clean(account.get("account_id")) or _clean(account.get("id")) or candidate
                debug["account_source"] = "flask_session"
                debug["account_lookup_candidate"] = candidate
                if lookup_errors:
                    debug["non_fatal_lookup_errors"] = lookup_errors[:6]
                return account_id, account, debug

        # If the session itself already carries a UUID-like account id, keep the page usable.
        # The entitlement service can still return free fallback, and member queries will expose
        # any table issue clearly instead of returning unauthorized.
        fallback_id = _clean(user.get("account_id")) or _clean(user.get("id"))
        if fallback_id:
            debug["account_source"] = "flask_session_fallback_id"
            if lookup_errors:
                debug["non_fatal_lookup_errors"] = lookup_errors[:8]
            return fallback_id, None, debug

    # 2) Compatibility path for token-in-cookie/bearer flows.
    if get_account_id_from_request is not None:
        try:
            debug["web_token_checked"] = True
            token_account_id, token_debug = get_account_id_from_request(request)  # type: ignore[misc]
            debug["web_token_debug"] = token_debug
            token_account_id = _clean(token_account_id)
            if token_account_id:
                account, errors = _get_account_by_any(token_account_id)
                if errors:
                    debug["web_token_lookup_errors"] = errors[:6]
                debug["account_source"] = "web_token"
                return token_account_id, account, debug
        except Exception as exc:
            debug["web_token_error"] = f"{type(exc).__name__}: {_clip(exc)}"

    debug["root_cause"] = "No valid Flask session account or web-session token was resolved."
    return None, None, debug


def _normalize_role(role: Any) -> str:
    role = _clean(role).lower()
    return role if role in {"admin", "member", "viewer"} else "member"


def _active_member_rows(owner_account_id: str) -> List[Dict[str, Any]]:
    res = (
        _sb()
        .table("workspace_members")
        .select(_workspace_select())
        .eq("owner_account_id", owner_account_id)
        .order("created_at", desc=False)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    active: List[Dict[str, Any]] = []
    for row in rows:
        status = _clean((row or {}).get("status")).lower() or "active"
        if status in {"active", "invited"}:
            active.append(row)
    return active


def _enrich_member(row: Dict[str, Any]) -> Dict[str, Any]:
    member_account_id = _clean(row.get("member_account_id"))
    member, _errors = _get_account_by_any(member_account_id)

    out = dict(row)
    out["role"] = _normalize_role(out.get("role"))
    out["status"] = _clean(out.get("status")) or "active"

    if member:
        out["member_account_id"] = _clean(member.get("account_id")) or member_account_id
        out["member_email"] = member.get("email")
        out["member_display_name"] = member.get("display_name")
        out["member_provider"] = member.get("provider")
        out["member_provider_user_id"] = member.get("provider_user_id")
    else:
        out.setdefault("member_email", None)
        out.setdefault("member_display_name", None)
        out.setdefault("member_provider", None)
        out.setdefault("member_provider_user_id", None)

    return out


def _workspace_payload(account_id: str, owner: Optional[Dict[str, Any]], debug: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entitlements = get_account_entitlements(account_id)
    service_counts = count_workspace_members(account_id) or {}
    rows = _active_member_rows(account_id)
    members = [_enrich_member(row) for row in rows]

    counts = {
        "active_members_only": len(members),
        "owner_included_total": 1 + len(members),
        **service_counts,
    }
    counts["active_members_only"] = len(members)
    counts["owner_included_total"] = 1 + len(members)

    return {
        "ok": True,
        "account_id": account_id,
        "owner": owner,
        "members": members,
        "count": len(members),
        "counts": counts,
        "entitlements": entitlements,
        "debug": debug or {},
    }


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@bp.get("/workspace/health")
def workspace_health():
    account_id, owner, debug = _resolve_current_account()
    return jsonify(
        {
            "ok": True,
            "status": "healthy",
            "route": "/api/workspace/health",
            "authenticated": bool(account_id),
            "account_id": account_id,
            "owner_found": bool(owner),
            "debug": debug,
        }
    ), 200


@bp.get("/workspace/limits")
def workspace_limits():
    account_id, owner, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before opening the workspace.",
            401,
            reason="unauthorized",
            fix="Login again from the website so the ntg_session Flask session cookie can be refreshed.",
            debug=debug,
        )

    try:
        entitlements = get_account_entitlements(account_id)
        counts = count_workspace_members(account_id) or {}
        if int(counts.get("owner_included_total") or 0) < 1:
            counts = {"active_members_only": 0, "owner_included_total": 1}

        return jsonify(
            {
                "ok": True,
                "account_id": account_id,
                "owner": owner,
                "counts": counts,
                "entitlements": entitlements,
                "debug": debug,
            }
        ), 200
    except Exception as exc:
        logger.exception("Workspace limits failed")
        return _json_error(
            "Workspace limits could not be loaded.",
            500,
            reason="workspace_limits_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Confirm workspace_members, accounts, user_subscriptions, and plans are available in Supabase.",
            debug=debug,
        )


@bp.get("/workspace/members")
def workspace_members_list():
    account_id, owner, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before opening workspace members.",
            401,
            reason="unauthorized",
            fix="Login again from the website so the ntg_session Flask session cookie can be refreshed.",
            debug=debug,
        )

    try:
        return jsonify(_workspace_payload(account_id, owner, debug)), 200
    except Exception as exc:
        logger.exception("Workspace members list failed")
        return _json_error(
            "Workspace members could not be loaded.",
            500,
            reason="workspace_members_list_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Confirm workspace_members exists and stores owner_account_id/member_account_id using canonical accounts.account_id values.",
            debug=debug,
        )


@bp.post("/workspace/members/add")
def workspace_members_add():
    account_id, owner, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before adding a workspace member.",
            401,
            reason="unauthorized",
            fix="Login again from the website so the ntg_session Flask session cookie can be refreshed.",
            debug=debug,
        )

    body = request.get_json(silent=True) or {}
    member_email = _clean_email(body.get("member_email"))
    member_account_id = _clean(body.get("member_account_id"))
    role = _normalize_role(body.get("role") or "member")

    if not member_email and not member_account_id:
        return _json_error(
            "Enter the member email address or member account ID.",
            400,
            reason="member_identifier_required",
            fix="Send member_email for an existing web account, or member_account_id for an existing account row.",
            debug=debug,
        )

    try:
        member: Optional[Dict[str, Any]] = None
        if member_account_id:
            member, _errors = _get_account_by_any(member_account_id)
        if not member and member_email:
            member = _get_account_by_email(member_email)

        if not member:
            return _json_error(
                "No existing web account was found for this member.",
                404,
                reason="member_account_not_found",
                fix="Ask the member to sign in to Naija Tax Guide once, then add the same email again.",
                debug=debug,
                details={"member_email": member_email or None, "member_account_id": member_account_id or None},
            )

        target_account_id = _clean(member.get("account_id")) or _clean(member.get("id"))
        if not target_account_id:
            return _json_error(
                "The member account exists but has no canonical account_id.",
                409,
                reason="member_account_id_missing",
                fix="Repair the accounts row so account_id is populated. For this system, account_id should normally equal id for web users.",
                debug=debug,
                details={"member": member},
            )

        if target_account_id == account_id:
            return _json_error(
                "The owner is already part of this workspace.",
                409,
                reason="cannot_add_owner_as_member",
                fix="Add another user email, not the owner email.",
                debug=debug,
            )

        existing = (
            _sb()
            .table("workspace_members")
            .select(_workspace_select())
            .eq("owner_account_id", account_id)
            .eq("member_account_id", target_account_id)
            .limit(1)
            .execute()
        )
        existing_rows = getattr(existing, "data", None) or []
        if existing_rows:
            existing_status = _clean(existing_rows[0].get("status")).lower() or "active"
            if existing_status in {"active", "invited"}:
                return _json_error(
                    "This user is already a member of the workspace.",
                    409,
                    reason="member_already_linked",
                    fix="Choose another user or remove this member first.",
                    debug=debug,
                    details={"member": _enrich_member(existing_rows[0])},
                )

            updated = (
                _sb()
                .table("workspace_members")
                .update({"status": "active", "role": role, "updated_at": _now_iso()})
                .eq("id", existing_rows[0].get("id"))
                .execute()
            )
            row = (getattr(updated, "data", None) or existing_rows)[0]
            return jsonify(
                {
                    "ok": True,
                    "account_id": account_id,
                    "message": f"Successfully reactivated {member.get('email') or target_account_id} in this workspace.",
                    "member": _enrich_member(row),
                    "workspace": _workspace_payload(account_id, owner, debug),
                    "debug": debug,
                }
            ), 200

        limit_check = enforce_workspace_member_limit(account_id)
        if not limit_check.get("ok"):
            return jsonify({"account_id": account_id, "debug": debug, **limit_check}), 403

        insert_data = {
            "owner_account_id": account_id,
            "member_account_id": target_account_id,
            "role": role,
            "status": "active",
        }

        created = _sb().table("workspace_members").insert(insert_data).execute()
        created_rows = getattr(created, "data", None) or []
        created_row = created_rows[0] if created_rows else insert_data

        return jsonify(
            {
                "ok": True,
                "account_id": account_id,
                "message": f"Successfully added {member.get('email') or target_account_id} to this workspace.",
                "member": _enrich_member(created_row),
                "workspace": _workspace_payload(account_id, owner, debug),
                "debug": debug,
            }
        ), 200

    except Exception as exc:
        logger.exception("Workspace member add failed")
        return _json_error(
            "Workspace member could not be added.",
            500,
            reason="workspace_member_add_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Check accounts lookup, workspace_members insert policy, and unique constraints.",
            debug=debug,
        )


@bp.post("/workspace/members/remove")
def workspace_members_remove():
    account_id, owner, debug = _resolve_current_account()
    if not account_id:
        return _json_error(
            "Please sign in again before removing a workspace member.",
            401,
            reason="unauthorized",
            fix="Login again from the website so the ntg_session Flask session cookie can be refreshed.",
            debug=debug,
        )

    body = request.get_json(silent=True) or {}
    member_account_id = _clean(body.get("member_account_id") or body.get("member_id"))

    if not member_account_id:
        return _json_error(
            "member_account_id is required.",
            400,
            reason="member_account_id_required",
            fix="Send the member_account_id shown on the workspace members list.",
            debug=debug,
        )

    if member_account_id == account_id:
        return _json_error(
            "The workspace owner cannot be removed.",
            403,
            reason="cannot_remove_owner",
            fix="Only additional members can be removed from this page.",
            debug=debug,
        )

    try:
        existing = (
            _sb()
            .table("workspace_members")
            .select(_workspace_select())
            .eq("owner_account_id", account_id)
            .eq("member_account_id", member_account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(existing, "data", None) or []
        if not rows:
            return jsonify(
                {
                    "ok": True,
                    "account_id": account_id,
                    "removed": False,
                    "message": "This member was not found or has already been removed.",
                    "workspace": _workspace_payload(account_id, owner, debug),
                    "debug": debug,
                }
            ), 200

        row_id = rows[0].get("id")
        _sb().table("workspace_members").delete().eq("id", row_id).execute()

        return jsonify(
            {
                "ok": True,
                "account_id": account_id,
                "removed": True,
                "member_account_id": member_account_id,
                "message": "Member removed successfully.",
                "workspace": _workspace_payload(account_id, owner, debug),
                "debug": debug,
            }
        ), 200

    except Exception as exc:
        logger.exception("Workspace member remove failed")
        return _json_error(
            "Workspace member could not be removed.",
            500,
            reason="workspace_member_remove_failed",
            root_cause=f"{type(exc).__name__}: {_clip(exc)}",
            fix="Check workspace_members delete access and confirm the member_account_id belongs to this owner.",
            debug=debug,
        )
