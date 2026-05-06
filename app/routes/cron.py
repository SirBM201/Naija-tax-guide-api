# app/routes/cron.py
from __future__ import annotations

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
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method
    })
