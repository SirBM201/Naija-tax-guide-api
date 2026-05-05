# app/routes/cron.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    """Test endpoint to verify cron blueprint is working"""
    return jsonify({
        "ok": True,
        "method": request.method,
        "message": "Cron blueprint is working!"
    }), 200


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    """Cron job endpoint for deadline reminders"""
    # Simple response for now - will add full logic later
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


@bp.route("/cron/deadlines/upcoming", methods=["GET"])
def cron_get_upcoming_deadlines():
    """Get upcoming deadlines"""
    return jsonify({
        "ok": True,
        "message": "Deadlines endpoint working",
        "deadlines": []
    }), 200
