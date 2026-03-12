from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.scripts.seed_tax_sources import seed_sources

bp = Blueprint("dev_tools", __name__)


@bp.route("/dev/seed-tax", methods=["GET", "POST"])
def seed_tax():
    try:
        result = seed_sources()
        return jsonify({
            "ok": True,
            "message": "Tax knowledge seeded successfully",
            "result": result,
            "method": request.method,
        }), 200
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500
