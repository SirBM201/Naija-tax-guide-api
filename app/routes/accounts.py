# app/routes/accounts.py
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.accounts_service import upsert_account

bp = Blueprint("accounts", __name__)


@bp.post("/accounts")
def create_or_get_account():
    """Create or find an account by provider identity.

    Body:
      {
        "provider": "whatsapp" | "wa" | "telegram" | "tg" | "web" | ...,
        "provider_user_id": "<string>",
        "display_name": "<optional>",
        "phone": "<optional>"
      }

    ✅ Returns canonical account_id = accounts.account_id
    """
    body = request.get_json(silent=True) or {}

    provider = (body.get("provider") or "").strip().lower()
    provider_user_id = (body.get("provider_user_id") or "").strip()

    if not provider or not provider_user_id:
        return jsonify({
            "ok": False,
            "error": "invalid_request",
            "root_cause": "provider or provider_user_id missing",
            "fix": "Send JSON with provider and provider_user_id.",
        }), 400

    res = upsert_account(
        provider=provider,
        provider_user_id=provider_user_id,
        display_name=(body.get("display_name") or None),
        phone=(body.get("phone") or None),
    )

    if not res.get("ok"):
        return jsonify(res), 400

    return jsonify({
        "ok": True,
        "account_id": res.get("account_id"),
        "account": res.get("account"),
    }), 200
