# app/routes/cron.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)
ROUTE_VERSION = "cron_route_v3_fixed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET") or "").strip()


def _cron_authorized() -> bool:
    secret = _cron_secret()
    if not secret:
        return True
    incoming = (request.headers.get("X-Cron-Secret") or request.args.get("cron_secret") or "").strip()
    return bool(incoming) and incoming == secret


# ============================================================
# SIMPLE DEADLINE DATA (no external imports)
# ============================================================

TAX_DEADLINES = {
    "paye": {"name": "PAYE", "day": 10, "description": "Monthly salary tax"},
    "vat": {"name": "VAT", "day": 21, "description": "Monthly sales tax"},
    "cit_annual": {"name": "CIT Annual", "month": 6, "day": 30, "description": "Annual company tax"},
}


def get_simple_deadlines():
    today = _now().date()
    deadlines = []
    for tax_type, config in TAX_DEADLINES.items():
        if "day" in config and "month" not in config:
            # Monthly deadline
            deadline_date = today.replace(day=config["day"])
            if deadline_date < today:
                if today.month == 12:
                    deadline_date = today.replace(year=today.year + 1, month=1, day=config["day"])
                else:
                    deadline_date = today.replace(month=today.month + 1, day=config["day"])
            deadlines.append({
                "tax_name": config["name"],
                "deadline_date": deadline_date.isoformat(),
                "description": config["description"]
            })
    return deadlines


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    return jsonify({"ok": True, "method": request.method, "message": "Cron blueprint is working!"})


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    deadlines = get_simple_deadlines()
    
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method,
        "deadlines_count": len(deadlines),
        "deadlines": deadlines,
        "timestamp": _now().isoformat()
    }), 200


@bp.route("/cron/deadlines/upcoming", methods=["GET"])
def cron_get_upcoming_deadlines():
    days_ahead = int(request.args.get("days", 30))
    deadlines = get_simple_deadlines()
    return jsonify({
        "ok": True,
        "count": len(deadlines),
        "deadlines": deadlines
    }), 200


# ============================================================
# REFERRAL CRON JOBS (Simplified - returns placeholder)
# ============================================================

@bp.route("/cron/referrals/mature", methods=["POST"])
def cron_referrals_mature():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "route_version": ROUTE_VERSION,
        "message": "Referral mature endpoint - full implementation coming soon",
        "result": {"matured_count": 0}
    }), 200


@bp.route("/cron/referrals/payout-batch", methods=["POST"])
def cron_referrals_payout_batch():
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    return jsonify({
        "ok": True,
        "route_version": ROUTE_VERSION,
        "message": "Referral payout endpoint - full implementation coming soon",
        "prepared_count": 0,
        "skipped_count": 0
    }), 200
