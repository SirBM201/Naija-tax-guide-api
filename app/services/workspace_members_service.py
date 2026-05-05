from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.supabase_client import supabase
from app.services.account_entitlements_service import enforce_workspace_member_limit


def _sb():
    return supabase() if callable(supabase) else supabase


def _clip(value: Any, n: int = 240) -> str:
    s = str(value or "")
    return s if len(s) <= n else s[:n] + "…"


def _reason_payload(reason: str, *, details: Any = None, fix: Optional[str] = None, root_cause: Optional[str] = None) -> Dict[str, Any]:
    payload = {"ok": False, "reason": reason, "error": reason}
    if details is not None:
        payload["details"] = details
    if fix:
        payload["fix"] = fix
    if root_cause:
        payload["root_cause"] = root_cause
    return payload


def _normalize_role(role: str) -> str:
    raw = str(role or "").strip().lower()
    return raw if raw in {"admin", "member", "viewer"} else "member"


def _get_account_by_email(email: str) -> Optional[Dict[str, Any]]:
    clean = str(email or "").strip().lower()
    if not clean:
        return None
    res = (
        _sb()
        .table("accounts")
        .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
        .eq("provider", "web")
        .eq("email", clean)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _get_account_by_account_id(account_id: str) -> Optional[Dict[str, Any]]:
    clean = str(account_id or "").strip()
    if not clean:
        return None
    res = (
        _sb()
        .table("accounts")
        .select("id,account_id,email,provider,provider_user_id,display_name,created_at,updated_at")
        .eq("account_id", clean)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def list_workspace_members(owner_account_id: str) -> Dict[str, Any]:
    try:
        owner = _get_account_by_account_id(owner_account_id)
        res = (
            _sb()
            .table("workspace_members")
            .select("*")
            .eq("owner_account_id", owner_account_id)
            .order("created_at")
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return {
            "ok": True,
            "owner": owner,
            "members": rows,
            "count": len(rows),
        }
    except Exception as e:
        return _reason_payload(
            "workspace_members_list_failed",
            root_cause=f"{type(e).__name__}: {_clip(e)}",
            fix="Check workspace_members table and Supabase access.",
        )


def add_workspace_member(
    *,
    owner_account_id: str,
    member_account_id: Optional[str] = None,
    member_email: Optional[str] = None,
    role: str = "member",
) -> Dict[str, Any]:
    owner_account_id = str(owner_account_id or "").strip()
    if not owner_account_id:
        return _reason_payload("owner_account_id_required", fix="Pass the owner account_id.")

    owner = _get_account_by_account_id(owner_account_id)
    if not owner:
        return _reason_payload("owner_account_not_found", fix="Ensure the owner account exists before adding members.")

    member = None
    if member_account_id:
        member = _get_account_by_account_id(member_account_id)
    elif member_email:
        member = _get_account_by_email(member_email)

    if not member:
        return _reason_payload(
            "member_account_not_found",
            fix="Pass a valid member account_id or a valid web-account email address.",
        )

    target_account_id = str(member.get("account_id") or "").strip()
    if not target_account_id:
        return _reason_payload("member_account_id_missing", fix="Ensure the member account has a canonical account_id.")

    if target_account_id == owner_account_id:
        return _reason_payload("cannot_add_owner_as_member", fix="Owner account is already the workspace owner.")

    limit_check = enforce_workspace_member_limit(owner_account_id)
    if not limit_check.get("ok"):
        return limit_check

    existing = (
        _sb()
        .table("workspace_members")
        .select("*")
        .eq("owner_account_id", owner_account_id)
        .eq("member_account_id", target_account_id)
        .limit(1)
        .execute()
    )
    rows = getattr(existing, "data", None) or []
    if rows:
        row = rows[0]
        status = str(row.get("status") or "").strip().lower()
        if status in {"active", "invited"}:
            return _reason_payload(
                "member_already_linked",
                details=row,
                fix="Choose a different account or remove the existing member first.",
            )
        try:
            upd = (
                _sb()
                .table("workspace_members")
                .update({"status": "active", "role": _normalize_role(role)})
                .eq("id", row["id"])
                .execute()
            )
            out = getattr(upd, "data", None) or []
            return {"ok": True, "member": out[0] if out else row}
        except Exception as e:
            return _reason_payload(
                "workspace_member_reactivate_failed",
                root_cause=f"{type(e).__name__}: {_clip(e)}",
                fix="Check workspace_members update path.",
            )

    payload = {
        "owner_account_id": owner_account_id,
        "member_account_id": target_account_id,
        "role": _normalize_role(role),
        "status": "active",
    }

    try:
        created = _sb().table("workspace_members").insert(payload).execute()
        out = getattr(created, "data", None) or []
        return {"ok": True, "member": out[0] if out else payload}
    except Exception as e:
        return _reason_payload(
            "workspace_member_add_failed",
            root_cause=f"{type(e).__name__}: {_clip(e)}",
            fix="Check workspace_members insert path and uniqueness constraints.",
        )


def remove_workspace_member(*, owner_account_id: str, member_account_id: str) -> Dict[str, Any]:
    owner_account_id = str(owner_account_id or "").strip()
    member_account_id = str(member_account_id or "").strip()
    if not owner_account_id or not member_account_id:
        return _reason_payload("owner_and_member_required", fix="Pass both owner_account_id and member_account_id.")

    try:
        existing = (
            _sb()
            .table("workspace_members")
            .select("*")
            .eq("owner_account_id", owner_account_id)
            .eq("member_account_id", member_account_id)
            .limit(1)
            .execute()
        )
        rows = getattr(existing, "data", None) or []
        if not rows:
            return {"ok": True, "removed": False, "reason": "member_not_found"}

        row = rows[0]
        _sb().table("workspace_members").delete().eq("id", row["id"]).execute()
        return {"ok": True, "removed": True, "member_account_id": member_account_id}
    except Exception as e:
        return _reason_payload(
            "workspace_member_remove_failed",
            root_cause=f"{type(e).__name__}: {_clip(e)}",
            fix="Check workspace_members delete path.",
        )
