from __future__ import annotations
import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
from app.core.supabase_client import supabase
from app.services.auth_service import get_current_user
import logging

logger = logging.getLogger(__name__)

bp = Blueprint("tax", __name__)

@bp.before_request
def before_request():
    """Log all requests to this blueprint for debugging"""
    logger.info(f"Tax blueprint request: {request.method} {request.path} - Session: {bool(request.cookies)}")

@bp.post("/tax/file")
def file_tax_return():
    """
    Endpoint to file a tax return (PAYE, VAT, CIT).
    Expects a JSON body with:
    - taxType: "paye" | "vat" | "cit"
    - inputs: dict containing the relevant financial details
    - documents: list of file metadata (optional)
    - userId: account ID (optional, extracted from auth if not provided)
    """
    # Log request details for debugging
    logger.info(f"Tax filing request received - Method: {request.method}, Headers: {dict(request.headers)}")
    logger.info(f"Cookies present: {list(request.cookies.keys()) if request.cookies else 'None'}")
    
    # Authenticate user - this is critical
    current_user = get_current_user()
    
    if not current_user:
        logger.warning("No authenticated user found for tax filing request")
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    
    logger.info(f"Authenticated user: {current_user.get('id')}, Email: {current_user.get('email')}")
    
    # Parse request body
    try:
        data = request.get_json(silent=True) or {}
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400
    
    tax_type = data.get("taxType", "").strip().lower()
    inputs = data.get("inputs", {})
    documents = data.get("documents", [])
    user_id = data.get("userId", "")
    
    # Validate required fields
    if tax_type not in ("paye", "vat", "cit"):
        return jsonify({"ok": False, "error": "Invalid tax type. Must be 'paye', 'vat', or 'cit'."}), 400
    
    # Generate a submission reference and timestamp
    submission_id = str(uuid.uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()
    reference = f"TAX-{tax_type.upper()}-{submission_id[:8].upper()}"
    
    # Insert filing record into Supabase
    sb = supabase()
    filing_record = {
        "id": submission_id,
        "user_id": current_user.get("id"),
        "account_id": user_id or current_user.get("account_id") or current_user.get("id"),
        "tax_type": tax_type,
        "inputs": inputs,
        "documents": documents,
        "reference": reference,
        "status": "submitted",
        "submitted_at": submitted_at,
    }
    
    try:
        logger.info(f"Inserting tax filing record for user {current_user.get('id')}")
        result = sb.table("tax_filings").insert(filing_record).execute()
        if not result.data:
            raise Exception("Failed to insert filing record")
        logger.info(f"Successfully inserted tax filing with reference: {reference}")
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        return jsonify({"ok": False, "error": f"Database error: {str(e)}"}), 500
    
    # Return success response including the generated reference
    return jsonify({
        "ok": True,
        "message": f"{tax_type.upper()} filing submitted successfully.",
        "reference": reference,
        "submittedAt": submitted_at,
    }), 200
