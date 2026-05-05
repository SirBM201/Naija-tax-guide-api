# app/routes/dev.py
from __future__ import annotations

import os
import sys
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from app.core.security import require_admin_key

logger = logging.getLogger(__name__)

bp = Blueprint("dev", __name__)


@bp.get("/dev/info")
def dev_info():
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    return jsonify({
        "ok": True,
        "environment": os.getenv("ENV", "production"),
        "python_version": sys.version,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


@bp.get("/dev/health")
def dev_health():
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    return jsonify({
        "ok": True,
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200
