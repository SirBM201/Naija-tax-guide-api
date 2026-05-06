# app/routes/cron.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET") or "").strip()


def _cron_authorized() -> bool:
    secret = _cron_secret()
    if not secret:
        return True
    incoming = (request.headers.get("X-Cron-Secret") or request.args.get("cron_secret") or "").strip()
    return bool(incoming) and incoming == secret

@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    return jsonify({
        "ok": True,
        "method": request.method,
        "message": "Cron blueprint is working!"
    })


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": "Full reminder logic will be added after workspace module is fixed"
    })


@bp.route("/cron/deadlines/upcoming", methods=["GET"])
def cron_get_upcoming_deadlines():
    days_ahead = int(request.args.get("days", 30))
    return jsonify({
        "ok": True,
        "days_ahead": days_ahead,
        "count": 2,
        "deadlines": [
            {
                "tax_name": "PAYE",
                "deadline_date": datetime.now(timezone.utc).date().isoformat(),
                "description": "Monthly salary tax"
            },
            {
                "tax_name": "VAT",
                "deadline_date": datetime.now(timezone.utc).date().isoformat(),
                "description": "Monthly sales tax"
            }
        ]
    })


@bp.route("/cron/referrals/mature", methods=["POST"])
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "route_version": "cron_v1",
        "result": {"matured_count": 0, "message": "Referral system pending"}
    })


@bp.route("/cron/referrals/payout-batch", methods=["POST"])
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "route_version": "cron_v1",
        "prepared_count": 0,
        "skipped_count": 0,
        "message": "Referral payout system pending"
    })
