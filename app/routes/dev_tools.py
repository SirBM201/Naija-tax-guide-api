from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from app.scripts.seed_tax_sources import seed_sources

bp = Blueprint("dev_tools", __name__)


@bp.route("/dev/seed-tax", methods=["GET", "POST"])
def seed_tax():
    expected = (os.getenv("SEED_TAX_TOKEN") or "").strip()

    if expected:
        provided = (request.headers.get("X-Seed-Token") or "").strip()
        if provided != expected:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        result = seed_sources()
        return jsonify(
            {
                "ok": True,
                "message": "Tax knowledge seeded successfully",
                "result": result,
                "method": request.method,
            }
        ), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
