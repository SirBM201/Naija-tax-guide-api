from __future__ import annotations
from flask import Blueprint, current_app, jsonify

bp = Blueprint("debug_routes", __name__)

@bp.get("/debug/routes")
def debug_routes():
    rules = []
    for r in current_app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        rules.append({
            "rule": str(r),
            "methods": sorted([m for m in r.methods if m not in ("HEAD","OPTIONS")]),
            "endpoint": r.endpoint,
        })
    rules = sorted(rules, key=lambda x: x["rule"])
    return jsonify({"ok": True, "count": len(rules), "routes": rules})
