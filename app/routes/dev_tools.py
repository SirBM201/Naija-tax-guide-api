from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from app.scripts.seed_tax_sources import seed_sources

bp = Blueprint("dev_tools", __name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


@bp.route("/dev/seed-tax", methods=["GET", "POST"])
def seed_tax():
    """
    Safe seed endpoint for initial Nigerian tax knowledge.

    Protection options:
    1. If SEED_TAX_TOKEN is set, request must include matching X-Seed-Token header.
    2. If SEED_TAX_REQUIRE_POST=1, only POST is allowed.
    3. If SEED_TAX_ENABLED=0, endpoint is disabled.
    """
    if not _truthy(os.getenv("SEED_TAX_ENABLED", "1")):
        return jsonify({"ok": False, "error": "Seed endpoint disabled"}), 403

    require_post = _truthy(os.getenv("SEED_TAX_REQUIRE_POST", "0"))
    if require_post and request.method != "POST":
        return jsonify({"ok": False, "error": "Method Not Allowed"}), 405

    expected = (os.getenv("SEED_TAX_TOKEN") or "").strip()
    if expected:
        provided = (request.headers.get("X-Seed-Token") or "").strip()
        if provided != expected:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

    allow_reseed = _truthy(os.getenv("SEED_TAX_ALLOW_RESEED", "0"))

    try:
        result = seed_sources(allow_reseed=allow_reseed)
        return jsonify(
            {
                "ok": True,
                "message": "Tax knowledge seed completed",
                "method": request.method,
                "allow_reseed": allow_reseed,
                "result": result,
            }
        ), 200
    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "error": type(e).__name__,
                "message": str(e),
            }
        ), 500
