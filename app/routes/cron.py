# app/routes/cron.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    return jsonify({
        "ok": True,
        "method": request.method,
        "message": "Cron blueprint is working!"
    })


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    # NO AUTHENTICATION CHECK - completely open for testing
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method,
        "timestamp": datetime.now(timezone.utc).isoformat()
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
    return jsonify({
        "ok": True,
        "route_version": "cron_v1",
        "result": {"matured_count": 0}
    })


@bp.route("/cron/referrals/payout-batch", methods=["POST"])
def cron_referrals_payout_batch():
    return jsonify({
        "ok": True,
        "route_version": "cron_v1",
        "prepared_count": 0,
        "skipped_count": 0
    })
