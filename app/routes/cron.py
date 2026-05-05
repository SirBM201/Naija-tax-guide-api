from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

bp = Blueprint("cron", __name__)


@bp.route("/cron/test", methods=["GET", "POST"])
def cron_test():
    return jsonify({
        "ok": True,
        "method": request.method,
        "message": "Cron blueprint is working!"
    }), 200


@bp.route("/cron/send-deadline-reminders", methods=["GET", "POST"])
def cron_send_deadline_reminders():
    return jsonify({
        "ok": True,
        "message": "Reminder endpoint reached",
        "method": request.method
    }), 200


# Rest of your existing code below...
