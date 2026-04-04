from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.supabase_service import sb_request


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_json(response) -> Optional[Dict[str, Any]]:
    rows = response.json() or []
    return rows[0] if rows else None


def _normalize_phone_e164(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = "".join(ch for ch in str(value) if ch.isdigit())
    return s or None


def find_account_by_provider_user_id(*, provider: str, provider_user_id: str) -> Optional[Dict[str, Any]]:
    r = sb_request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
            "provider": f"eq.{provider}",
            "provider_user_id": f"eq.{provider_user_id}",
            "limit": "1",
        },
    )
    return _first_json(r)


def find_account_by_auth_user_and_provider(*, auth_user_id: str, provider: str) -> Optional[Dict[str, Any]]:
    r = sb_request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
            "auth_user_id": f"eq.{auth_user_id}",
            "provider": f"eq.{provider}",
            "limit": "1",
        },
    )
    return _first_json(r)


def lookup_account(*, provider: str, provider_user_id: str) -> Dict[str, Any]:
    row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if not row:
        return {"ok": True, "linked": False}
    return {
        "ok": True,
        "linked": bool(row.get("auth_user_id")),
        "id": row.get("id"),
        "account_id": row.get("account_id"),
        "auth_user_id": row.get("auth_user_id"),
        "row": row,
    }


def upsert_account(*, provider: str, provider_user_id: str, display_name: Optional[str], phone: Optional[str]) -> Dict[str, Any]:
    payload = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "display_name": display_name,
        "phone": phone,
        "phone_e164": _normalize_phone_e164(phone or provider_user_id),
        "updated_at": _now_iso(),
    }
    r = sb_request(
        "POST",
        "/rest/v1/accounts",
        params={
            "on_conflict": "provider,provider_user_id",
            "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
        },
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json=payload,
    )
    row = _first_json(r)
    return {"ok": True, "row": row}


def mark_channel_claimed_for_auth_user(
    *,
    provider: str,
    provider_user_id: str,
    auth_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    existing = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if not existing:
        return {"ok": False, "reason": "channel_not_found"}

    existing_auth = str(existing.get("auth_user_id") or "").strip()
    if existing_auth and existing_auth != auth_user_id:
        return {"ok": False, "reason": "channel_belongs_to_another_user"}

    payload = {
        "auth_user_id": auth_user_id,
        "display_name": display_name if display_name is not None else existing.get("display_name"),
        "phone": phone if phone is not None else existing.get("phone"),
        "phone_e164": _normalize_phone_e164(phone or existing.get("phone") or provider_user_id),
        "updated_at": _now_iso(),
    }
    r = sb_request(
        "PATCH",
        "/rest/v1/accounts",
        params={
            "id": f"eq.{existing['id']}",
            "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
        },
        headers={"Prefer": "return=representation"},
        json=payload,
    )
    row = _first_json(r)
    if not row:
        return {"ok": False, "reason": "claim_failed"}
    return {"ok": True, "account_id": row.get("account_id"), "row": row}


def upsert_account_link(
    *,
    provider: str,
    provider_user_id: str,
    auth_user_id: str,
    display_name: Optional[str],
    phone: Optional[str],
) -> Dict[str, Any]:
    by_channel = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if by_channel:
        channel_auth = str(by_channel.get("auth_user_id") or "").strip()
        if channel_auth and channel_auth != auth_user_id:
            return {"ok": False, "reason": "channel_belongs_to_another_user"}

    by_user = find_account_by_auth_user_and_provider(auth_user_id=auth_user_id, provider=provider)
    if by_user:
        payload = {
            "provider_user_id": provider_user_id,
            "display_name": display_name if display_name is not None else by_user.get("display_name"),
            "phone": phone if phone is not None else by_user.get("phone"),
            "phone_e164": _normalize_phone_e164(phone or by_user.get("phone") or provider_user_id),
            "updated_at": _now_iso(),
        }
        r = sb_request(
            "PATCH",
            "/rest/v1/accounts",
            params={
                "id": f"eq.{by_user['id']}",
                "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
            },
            headers={"Prefer": "return=representation"},
            json=payload,
        )
        row = _first_json(r)
        if not row:
            return {"ok": False, "reason": "update_failed"}
        return {"ok": True, "account_id": row.get("account_id"), "row": row}

    payload = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "auth_user_id": auth_user_id,
        "display_name": display_name,
        "phone": phone,
        "phone_e164": _normalize_phone_e164(phone or provider_user_id),
        "updated_at": _now_iso(),
    }
    r = sb_request(
        "POST",
        "/rest/v1/accounts",
        params={
            "on_conflict": "provider,provider_user_id",
            "select": "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,updated_at,created_at",
        },
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json=payload,
    )
    row = _first_json(r)
    if not row:
        row = find_account_by_provider_user_id(provider=provider, provider_user_id=provider_user_id)
    if not row:
        return {"ok": False, "reason": "link_failed"}
    return {"ok": True, "account_id": row.get("account_id"), "row": row}
