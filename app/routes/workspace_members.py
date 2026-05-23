from __future__ import annotations

from flask import Blueprint, jsonify as _flask_jsonify, request

from app.services.web_auth_service import get_account_id_from_request
from app.services.account_entitlements_service import (
    count_workspace_members,
    get_account_entitlements,
)
from app.services.workspace_members_service import (
    add_workspace_member,
    list_workspace_members,
    remove_workspace_member,
)

bp = Blueprint("workspace_members", __name__, url_prefix="/workspace")


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


def _safe_json():
    return request.get_json(silent=True) or {}


@bp.get("/members")
def workspace_members_list():
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    listing = list_workspace_members(account_id)
    ent = get_account_entitlements(account_id)
    counts = count_workspace_members(account_id)

    return jsonify(
        {
            "ok": bool(listing.get("ok")),
            "account_id": account_id,
            "owner": listing.get("owner"),
            "members": listing.get("members") or [],
            "count": listing.get("count") or 0,
            "counts": counts,
            "entitlements": ent,
            "debug": debug,
            **({} if listing.get("ok") else {"error": listing.get("error"), "details": listing}),
        }
    ), (200 if listing.get("ok") else 400)


@bp.get("/limits")
def workspace_limits():
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    ent = get_account_entitlements(account_id)
    counts = count_workspace_members(account_id)

    # Always return 200 here for authenticated users, even when they are on free/no-plan fallback.
    return jsonify(
        {
            "ok": True,
            "account_id": account_id,
            "counts": counts,
            "entitlements": ent,
            "debug": debug,
        }
    ), 200


@bp.post("/members/add")
def workspace_members_add():
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _safe_json()
    result = add_workspace_member(
        owner_account_id=account_id,
        member_account_id=(body.get("member_account_id") or "").strip() or None,
        member_email=(body.get("member_email") or "").strip() or None,
        role=(body.get("role") or "member").strip(),
    )
    status = 200 if result.get("ok") else 400
    return jsonify({"account_id": account_id, "debug": debug, **result}), status


@bp.post("/members/remove")
def workspace_members_remove():
    account_id, debug = get_account_id_from_request(request)
    if not account_id:
        return jsonify({"ok": False, "error": "unauthorized", "debug": debug}), 401

    body = _safe_json()
    result = remove_workspace_member(
        owner_account_id=account_id,
        member_account_id=(body.get("member_account_id") or "").strip(),
    )
    status = 200 if result.get("ok") else 400
    return jsonify({"account_id": account_id, "debug": debug, **result}), status
