# app/routes/cron.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)

ROUTE_VERSION = "cron_v2_with_auth"


def _cron_secret() -> str:
    """Get the cron secret from environment variables"""
    return (os.getenv("CRON_SECRET") or os.getenv("ADMIN_CRON_SECRET") or "").strip()


def _cron_authorized() -> bool:
    """Check if the request has the correct X-Cron-Secret header"""
    secret = _cron_secret()
    
    # If no secret is configured, reject all requests (safe default)
    if not secret:
        return False
    
    # Check for X-Cron-Secret header
    header_secret = request.headers.get("X-Cron-Secret", "").strip()
    if header_secret and header_secret == secret:
        return True
    
    # Also check for X-Webhook-Secret (alternative)
    alt_secret = request.headers.get("X-Webhook-Secret", "").strip()
    if alt_secret and alt_secret == secret:
        return True
    
    return False


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    """Test endpoint to verify cron blueprint is working"""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "message": "Missing or invalid X-Cron-Secret header"}), 401
    
    return jsonify({
        "ok": True,
        "method": request.method,
        "message": "Cron blueprint is working!",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    """Cron job endpoint for deadline reminders"""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "message": "Missing or invalid X-Cron-Secret header"}), 401
    
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": "Full reminder logic will be added after workspace module is fixed"
    })


@bp.route("/cron/deadlines/upcoming", methods=["GET"])
def cron_get_upcoming_deadlines():
    """Get upcoming deadlines (public endpoint - no auth required)"""
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
    """Cron job to mature pending referral rewards"""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "route_version": ROUTE_VERSION}), 401
    
    return jsonify({
        "ok": True,
        "route_version": ROUTE_VERSION,
        "result": {"matured_count": 0, "message": "Referral system pending implementation"}
    })


@bp.route("/cron/referrals/payout-batch", methods=["POST"])
def cron_referrals_payout_batch():
    """Cron job to process referral payouts"""
    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized", "route_version": ROUTE_VERSION}), 401
    
    return jsonify({
        "ok": True,
        "route_version": ROUTE_VERSION,
        "prepared_count": 0,
        "skipped_count": 0,
        "message": "Referral payout system pending implementation"
    })
