# app/routes/debug_routes.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request

bp = Blueprint("debug_routes", __name__)

def _admin_ok(req) -> bool:
    expected = (os.getenv("ADMIN_KEY") or "").strip()
    got = (req.headers.get("X-Admin-Key") or "").strip()
    return bool(expected) and got == expected

@bp.get("/_debug/routes")
def list_routes():
    if not _admin_ok(request):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    # We don’t enumerate app.url_map here because we don't have access to app in blueprint.
    # This endpoint simply confirms that debug blueprint is active.
    return jsonify({"ok": True, "debug_routes_enabled": True}), 200
