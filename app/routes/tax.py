from __future__ import annotations
import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user

bp = Blueprint("tax", __name__)

@bp.post("/tax/file")
def file_tax_return():
    current_user = get_current_user()
    if not current_user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    tax_type = data.get("taxType", "").strip().lower()
    inputs = data.get("inputs", {})
    documents = data.get("documents", [])
    user_id = data.get("userId", "")

    if tax_type not in ("paye", "vat", "cit"):
        return jsonify({"ok": False, "error": "Invalid tax type"}), 400

    submission_id = str(uuid.uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()
    reference = f"TAX-{tax_type.upper()}-{submission_id[:8].upper()}"

    sb = supabase()
    filing_record = {
        "id": submission_id,
        "user_id": current_user.get("id"),
        "account_id": user_id,
        "tax_type": tax_type,
        "inputs": inputs,
        "documents": documents,
        "reference": reference,
        "status": "submitted",
        "submitted_at": submitted_at,
    }

    try:
        result = sb.table("tax_filings").insert(filing_record).execute()
        if not result.data:
            raise Exception("Insert failed")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Database error: {str(e)}"}), 500

    return jsonify({
        "ok": True,
        "message": f"{tax_type.upper()} filing submitted successfully.",
        "reference": reference,
        "submittedAt": submitted_at,
    }), 200
