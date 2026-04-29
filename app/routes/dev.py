# app/routes/dev.py
from __future__ import annotations

import os
import sys
import logging
import traceback
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, current_app

from app.core.security import require_admin_key
from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)

bp = Blueprint("dev", __name__)


@bp.get("/dev/info")
def dev_info():
    """Get development info (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    return jsonify({
        "ok": True,
        "environment": os.getenv("ENV", "production"),
        "python_version": sys.version,
        "debug_enabled": current_app.debug,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200


@bp.get("/dev/health")
def dev_health():
    """Detailed health check (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    health = {
        "ok": True,
        "status": "healthy",
        "checks": {}
    }
    
    # Check Supabase
    try:
        supabase.table("accounts").select("id", count="exact").limit(1).execute()
        health["checks"]["supabase"] = {"ok": True}
    except Exception as e:
        health["checks"]["supabase"] = {"ok": False, "error": str(e)}
        health["ok"] = False
    
    # Check session
    from flask import session
    health["checks"]["session"] = {
        "ok": True,
        "has_session": bool(session)
    }
    
    # Check environment variables
    required_vars = ["SECRET_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
    health["checks"]["env"] = {}
    for var in required_vars:
        health["checks"]["env"][var] = bool(os.getenv(var))
    
    return jsonify(health), 200 if health["ok"] else 500


@bp.get("/dev/test-error")
def test_error():
    """Test error handling (admin only)"""
    guard = require_admin_key()
    if guard is not None:
        return guard
    
    # Force an error to test error handling
    raise ValueError("This is a test error from /dev/test-error")
