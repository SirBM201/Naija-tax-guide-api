# app/services/tax_filing_service.py
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.core.supabase_client import supabase
from app.services.tax_calculator import calculate_tax

logger = logging.getLogger(__name__)


def _sb():
    return supabase() if callable(supabase) else supabase


def generate_reference(tax_type: str) -> str:
    """Generate a unique filing reference"""
    import random
    import string
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"NTG-{tax_type.upper()}-{suffix}"


def save_filing_draft(
    user_id: str,
    tax_type: str,
    inputs: Dict[str, Any],
    documents: List[str],
    current_step: int = 1
) -> Dict[str, Any]:
    """Save or update an in-progress filing draft"""
    try:
        # Check if draft exists
        existing = _sb().table("tax_filing_drafts") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("tax_type", tax_type) \
            .eq("status", "in_progress") \
            .execute()
        
        now = datetime.now(timezone.utc).isoformat()
        
        if existing.data:
            # Update existing draft
            result = _sb().table("tax_filing_drafts") \
                .update({
                    "inputs": inputs,
                    "documents": documents,
                    "current_step": current_step,
                    "updated_at": now
                }) \
                .eq("id", existing.data[0]["id"]) \
                .execute()
            return {"ok": True, "draft": result.data[0] if result.data else existing.data[0]}
        else:
            # Create new draft
            draft_id = str(uuid.uuid4())
            result = _sb().table("tax_filing_drafts").insert({
                "id": draft_id,
                "user_id": user_id,
                "tax_type": tax_type,
                "inputs": inputs,
                "documents": documents,
                "current_step": current_step,
                "status": "in_progress",
                "created_at": now,
                "updated_at": now
            }).execute()
            return {"ok": True, "draft": result.data[0] if result.data else {"id": draft_id}}
            
    except Exception as e:
        logger.error(f"Error saving draft: {e}")
        return {"ok": False, "error": str(e)}


def get_filing_draft(user_id: str, tax_type: str) -> Optional[Dict[str, Any]]:
    """Get an in-progress filing draft"""
    try:
        result = _sb().table("tax_filing_drafts") \
            .select("*") \
            .eq("user_id", user_id) \
            .eq("tax_type", tax_type) \
            .eq("status", "in_progress") \
            .order("updated_at", desc=True) \
            .limit(1) \
            .execute()
        
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting draft: {e}")
        return None


def delete_filing_draft(user_id: str, tax_type: str) -> bool:
    """Delete an in-progress filing draft"""
    try:
        _sb().table("tax_filing_drafts") \
            .delete() \
            .eq("user_id", user_id) \
            .eq("tax_type", tax_type) \
            .eq("status", "in_progress") \
            .execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting draft: {e}")
        return False


def submit_tax_filing(
    user_id: str,
    tax_type: str,
    inputs: Dict[str, Any],
    documents: List[str] = None
) -> Dict[str, Any]:
    """Submit a completed tax filing"""
    try:
        # Calculate tax
        calculation = calculate_tax(tax_type, inputs)
        
        # Generate reference
        reference = generate_reference(tax_type)
        now = datetime.now(timezone.utc).isoformat()
        
        # Store filing
        filing_id = str(uuid.uuid4())
        result = _sb().table("tax_filings").insert({
            "id": filing_id,
            "user_id": user_id,
            "tax_type": tax_type,
            "inputs": inputs,
            "documents": documents or [],
            "status": "submitted",
            "reference": reference,
            "submitted_at": now
        }).execute()
        
        # Store calculation details
        _sb().table("tax_calculations").insert({
            "filing_id": filing_id,
            "user_id": user_id,
            "tax_type": tax_type,
            "chargeable_income": calculation.get("chargeable_income", 0),
            "tax_payable": calculation.get("annual_tax_payable", calculation.get("vat_payable", calculation.get("cit_payable", 0))),
            "relief_amount": calculation.get("cra_deduction", 0),
            "pension_deductible": calculation.get("pension_deduction", 0),
            "nhf_deductible": calculation.get("nhf_deduction", 0),
            "cra_deductible": calculation.get("cra_deduction", 0),
            "calculation_details": calculation,
            "created_at": now
        }).execute()
        
        # Delete any draft
        delete_filing_draft(user_id, tax_type)
        
        return {
            "ok": True,
            "filing": result.data[0] if result.data else {"id": filing_id, "reference": reference},
            "calculation": calculation,
            "reference": reference,
            "submitted_at": now
        }
        
    except Exception as e:
        logger.error(f"Error submitting filing: {e}")
        return {"ok": False, "error": str(e)}


def get_user_filings(user_id: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """Get user's filing history"""
    try:
        result = _sb().table("tax_filings") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("submitted_at", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()
        
        filings = []
        for filing in (result.data or []):
            # Get calculation for this filing
            calc_result = _sb().table("tax_calculations") \
                .select("*") \
                .eq("filing_id", filing["id"]) \
                .limit(1) \
                .execute()
            
            filing["calculation"] = calc_result.data[0] if calc_result.data else None
            filings.append(filing)
        
        return filings
    except Exception as e:
        logger.error(f"Error getting filings: {e}")
        return []


def get_filing_by_reference(reference: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific filing by reference"""
    try:
        result = _sb().table("tax_filings") \
            .select("*") \
            .eq("reference", reference) \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        
        if result.data:
            filing = result.data[0]
            calc_result = _sb().table("tax_calculations") \
                .select("*") \
                .eq("filing_id", filing["id"]) \
                .limit(1) \
                .execute()
            filing["calculation"] = calc_result.data[0] if calc_result.data else None
            return filing
        return None
    except Exception as e:
        logger.error(f"Error getting filing: {e}")
        return None
