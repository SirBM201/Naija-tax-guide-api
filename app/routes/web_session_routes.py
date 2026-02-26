# app/routes/web_session.py
from __future__ import annotations

"""Web session endpoints.

Uses canonical identity:
  - account_id == accounts.account_id

Assumptions:
- app.core.auth.require_auth_plus (or your middleware) sets g.account_id to canonical.
- web_tokens.account_id FK MUST reference accounts.account_id (fix your DB constraint).

"""

from flask import Blueprint, jsonify, request, g

from app.core.auth import require_auth_plus

bp = Blueprint("web_session", __name__)


def _clip(s: str, n: int = 240) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


@bp.get("/me")
@require_auth_plus
def me():
    """Return current authenticated session's account_id."""
    account_id = getattr(g, "account_id", None)
    if not account_id:
        return jsonify({
            "ok": False,
            "error": "unauthorized",
            "root_cause": "auth middleware did not set g.account_id",
            "fix": "Ensure web auth middleware validates cookie/bearer and sets canonical account_id.",
        }), 401

    return jsonify({"ok": True, "account_id": account_id}), 200


@bp.post("/logout")
@require_auth_plus
def logout():
    """Logout current session. Actual revoke may be handled by auth middleware/service."""
    # If you have a dedicated logout in web_auth_service, call it here.
    return jsonify({"ok": True}), 200
