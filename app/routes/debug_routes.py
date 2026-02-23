from __future__ import annotations

import os
from flask import Blueprint, jsonify, current_app

bp = Blueprint("debug_routes", __name__)

@bp.get("/__routes")
def list_routes():
    # Protect it (set DEBUG_ROUTES_KEY in env)
    key = os.getenv("DEBUG_ROUTES_KEY", "").strip()
    if key:
        # simple header guard
        # curl: -H "x-debug-key: YOURKEY"
        from flask import request
        if request.headers.get("x-debug-key", "") != key:
            return jsonify({"ok": False, "error": "forbidden"}), 403

    rules = []
    for r in sorted(current_app.url_map.iter_rules(), key=lambda x: str(x)):
        if r.endpoint == "static":
            continue
        rules.append({
            "rule": str(r),
            "methods": sorted([m for m in r.methods if m not in ("HEAD","OPTIONS")]),
            "endpoint": r.endpoint,
        })
    return jsonify({"ok": True, "count": len(rules), "routes": rules})
