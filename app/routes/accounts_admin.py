from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.payout_service import (
    PayoutNotFoundError,
    PayoutService,
    PayoutValidationError,
)


admin_referral_payouts_bp = Blueprint(
    "admin_referral_payouts",
    __name__,
    url_prefix="/api/admin/referral-payouts",
)


def _get_admin_key() -> str:
    return (request.headers.get("X-Admin-Key") or "").strip()


def _require_admin() -> None:
    from app.config import settings  # adjust to your project config
    supplied = _get_admin_key()
    expected = (getattr(settings, "ADMIN_API_KEY", "") or "").strip()
    if not supplied or supplied != expected:
        raise PermissionError("Invalid or missing admin API key.")


def _get_service() -> PayoutService:
    from app.supabase_client import get_supabase_client  # adjust import to your app
    return PayoutService(get_supabase_client())


@admin_referral_payouts_bp.errorhandler(PermissionError)
def _handle_permission_error(exc: PermissionError):
    return jsonify({"ok": False, "error": str(exc)}), 403


@admin_referral_payouts_bp.errorhandler(PayoutValidationError)
def _handle_validation_error(exc: PayoutValidationError):
    return jsonify({"ok": False, "error": str(exc)}), 400


@admin_referral_payouts_bp.errorhandler(PayoutNotFoundError)
def _handle_not_found_error(exc: PayoutNotFoundError):
    return jsonify({"ok": False, "error": str(exc)}), 404


@admin_referral_payouts_bp.route("", methods=["GET"])
def get_referral_payout_queue():
    _require_admin()
    statuses_raw = (request.args.get("status") or "pending,processing,failed").strip()
    statuses = [item.strip() for item in statuses_raw.split(",") if item.strip()]
    limit = max(1, min(int(request.args.get("limit", 200)), 500))
    rows = _get_service().get_queue(statuses=statuses, limit=limit)
    return jsonify({"ok": True, "count": len(rows), "rows": rows})


@admin_referral_payouts_bp.route("/<payout_id>", methods=["GET"])
def get_referral_payout(payout_id: str):
    _require_admin()
    payout = _get_service().get_payout(payout_id)
    return jsonify({"ok": True, "payout": payout})


@admin_referral_payouts_bp.route("/<payout_id>/audit", methods=["GET"])
def get_referral_payout_audit(payout_id: str):
    _require_admin()
    limit = max(1, min(int(request.args.get("limit", 100)), 300))
    rows = _get_service().get_audit_history(payout_id=payout_id, limit=limit)
    return jsonify({"ok": True, "rows": rows})


@admin_referral_payouts_bp.route("/<payout_id>/mark-processing", methods=["POST"])
def mark_referral_payout_processing(payout_id: str):
    _require_admin()
    body = request.get_json(silent=True) or {}
    result = _get_service().mark_processing(
        payout_id=payout_id,
        provider_reference=body.get("provider_reference"),
        provider_transfer_code=body.get("provider_transfer_code"),
        metadata={"source": "admin_single", "request_body": body},
    )
    return jsonify({"ok": True, "payout": result.payout, "updated_reward_ids": result.updated_reward_ids})


@admin_referral_payouts_bp.route("/<payout_id>/mark-paid", methods=["POST"])
def mark_referral_payout_paid(payout_id: str):
    _require_admin()
    body = request.get_json(silent=True) or {}
    result = _get_service().mark_paid(
        payout_id=payout_id,
        provider_reference=body.get("provider_reference"),
        provider_transfer_code=body.get("provider_transfer_code"),
        metadata={"source": "admin_single", "request_body": body},
    )
    return jsonify({"ok": True, "payout": result.payout, "updated_reward_ids": result.updated_reward_ids})


@admin_referral_payouts_bp.route("/<payout_id>/mark-failed", methods=["POST"])
def mark_referral_payout_failed(payout_id: str):
    _require_admin()
    body = request.get_json(silent=True) or {}
    result = _get_service().mark_failed(
        payout_id=payout_id,
        failure_reason=body.get("failure_reason") or "",
        provider_reference=body.get("provider_reference"),
        provider_transfer_code=body.get("provider_transfer_code"),
        metadata={"source": "admin_single", "request_body": body},
    )
    return jsonify({"ok": True, "payout": result.payout, "updated_reward_ids": result.updated_reward_ids})


@admin_referral_payouts_bp.route("/bulk", methods=["POST"])
def bulk_update_referral_payouts():
    _require_admin()
    body = request.get_json(silent=True) or {}

    action = (body.get("action") or "").strip()
    payout_ids = body.get("payout_ids") or []
    provider_reference = body.get("provider_reference")
    provider_transfer_code = body.get("provider_transfer_code")
    failure_reason = body.get("failure_reason")

    result = _get_service().bulk_update(
        action=action,
        payout_ids=payout_ids,
        provider_reference=provider_reference,
        provider_transfer_code=provider_transfer_code,
        failure_reason=failure_reason,
        metadata={"source": "admin_bulk", "request_body": body},
    )
    return jsonify({"ok": True, **result})
