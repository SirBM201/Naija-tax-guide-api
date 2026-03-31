from flask import Blueprint, jsonify, request
import os

from app.core.supabase_client import supabase
from app.services.payout_service import (
    admin_mark_payout_failed,
    admin_mark_payout_paid,
    admin_mark_payout_processing,
    get_payout_row,
    list_payout_queue,
)

bp = Blueprint("accounts_admin", __name__)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()


def _bad(msg: str, status: int = 400, **extra):
    payload = {"ok": False, "error": msg}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def _authorized() -> bool:
    admin_key = (request.headers.get("X-Admin-Key") or "").strip()
    return bool(ADMIN_API_KEY) and admin_key == ADMIN_API_KEY


@bp.post("/admin/accounts/unlink")
def admin_unlink_account():
    if not _authorized():
        return _bad("Unauthorized", 401)

    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()
    provider_user_id = (body.get("provider_user_id") or "").strip()

    if provider not in ("wa", "tg"):
        return _bad("provider must be wa or tg")
    if not provider_user_id:
        return _bad("provider_user_id required")

    try:
        res = (
            supabase()
            .table("accounts")
            .update({"auth_user_id": None})
            .eq("provider", provider)
            .eq("provider_user_id", provider_user_id)
            .execute()
        )
    except Exception as e:
        return _bad(f"DB error: {str(e)}", 500)

    return jsonify({"ok": True, "unlinked": True, "rows": len(res.data or [])})


@bp.get("/admin/referral-payouts")
def admin_list_referral_payouts():
    if not _authorized():
        return _bad("Unauthorized", 401)

    raw_status = (request.args.get("status") or "pending,processing,failed").strip()
    statuses = [s.strip().lower() for s in raw_status.split(",") if s.strip()]
    try:
        limit = int((request.args.get("limit") or "200").strip())
    except Exception:
        limit = 200

    rows = list_payout_queue(statuses=statuses, limit=limit)
    return jsonify({"ok": True, "count": len(rows), "rows": rows})


@bp.get("/admin/referral-payouts/<payout_id>")
def admin_get_referral_payout(payout_id: str):
    if not _authorized():
        return _bad("Unauthorized", 401)

    payout = get_payout_row(payout_id)
    if not payout:
        return _bad("payout not found", 404)
    return jsonify({"ok": True, "payout": payout})


@bp.post("/admin/referral-payouts/<payout_id>/mark-processing")
def admin_referral_payout_mark_processing(payout_id: str):
    if not _authorized():
        return _bad("Unauthorized", 401)

    body = request.get_json(silent=True) or {}
    try:
        result = admin_mark_payout_processing(
            payout_id,
            provider_reference=(body.get("provider_reference") or "").strip() or None,
            provider_transfer_code=(body.get("provider_transfer_code") or "").strip() or None,
        )
        return jsonify(result)
    except Exception as e:
        return _bad(str(e), 400)


@bp.post("/admin/referral-payouts/<payout_id>/mark-paid")
def admin_referral_payout_mark_paid(payout_id: str):
    if not _authorized():
        return _bad("Unauthorized", 401)

    body = request.get_json(silent=True) or {}
    try:
        result = admin_mark_payout_paid(
            payout_id,
            provider_reference=(body.get("provider_reference") or "").strip() or None,
            provider_transfer_code=(body.get("provider_transfer_code") or "").strip() or None,
        )
        return jsonify(result)
    except Exception as e:
        return _bad(str(e), 400)


@bp.post("/admin/referral-payouts/<payout_id>/mark-failed")
def admin_referral_payout_mark_failed(payout_id: str):
    if not _authorized():
        return _bad("Unauthorized", 401)

    body = request.get_json(silent=True) or {}
    try:
        result = admin_mark_payout_failed(
            payout_id,
            failure_reason=(body.get("failure_reason") or "").strip() or "admin_marked_failed",
            provider_reference=(body.get("provider_reference") or "").strip() or None,
            provider_transfer_code=(body.get("provider_transfer_code") or "").strip() or None,
        )
        return jsonify(result)
    except Exception as e:
        return _bad(str(e), 400)
