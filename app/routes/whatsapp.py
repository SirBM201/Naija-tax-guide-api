
# app/routes/whatsapp.py
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, request

from app.core.supabase_client import supabase

try:
    from app.core.supabase_client import get_supabase_client
except Exception:  # pragma: no cover
    get_supabase_client = None  # type: ignore

try:
    from supabase import create_client as _create_supabase_client
except Exception:  # pragma: no cover
    _create_supabase_client = None  # type: ignore
from app.services.ask_service import ask_guarded

try:
    from app.services.channel_identity_service import create_or_update_channel_identity
except Exception:  # pragma: no cover
    create_or_update_channel_identity = None  # type: ignore

try:
    from app.services.paystack_service import create_reference, initialize_transaction
except Exception:  # pragma: no cover
    create_reference = None  # type: ignore
    initialize_transaction = None  # type: ignore


bp = Blueprint("whatsapp", __name__)

WHATSAPP_FLOW_VERSION = "2026-05-25-v27-support-reply-close-credit-history"


# =============================================================================
# Data configuration
# =============================================================================

PLAN_OPTIONS: Dict[str, Dict[str, Any]] = {
    "S1": {
        "code": "S1",
        "plan_code": "starter_monthly",
        "name": "Starter Monthly",
        "price": 5000,
        "credits": 100,
        "cycle": "monthly",
        "family": "starter",
        "aliases": [
            "s1",
            "starter",
            "starter monthly",
            "starter month",
            "monthly starter",
            "starter plan",
            "5000",
            "5,000",
            "₦5000",
            "₦5,000",
            "100 credits",
            "starter 100",
        ],
    },
    "S2": {
        "code": "S2",
        "plan_code": "starter_quarterly",
        "name": "Starter Quarterly",
        "price": 14000,
        "credits": 300,
        "cycle": "quarterly",
        "family": "starter",
        "aliases": [
            "s2",
            "starter quarterly",
            "starter quarter",
            "quarterly starter",
            "14000",
            "14,000",
            "₦14000",
            "₦14,000",
            "300 credits starter",
            "starter 300",
        ],
    },
    "S3": {
        "code": "S3",
        "plan_code": "starter_yearly",
        "name": "Starter Yearly",
        "price": 51000,
        "credits": 1200,
        "cycle": "yearly",
        "family": "starter",
        "aliases": [
            "s3",
            "starter yearly",
            "starter annual",
            "yearly starter",
            "51000",
            "51,000",
            "₦51000",
            "₦51,000",
            "1200 credits",
            "starter 1200",
        ],
    },
    "P1": {
        "code": "P1",
        "plan_code": "professional_monthly",
        "name": "Professional Monthly",
        "price": 12000,
        "credits": 300,
        "cycle": "monthly",
        "family": "professional",
        "aliases": [
            "p1",
            "professional",
            "pro",
            "professional monthly",
            "pro monthly",
            "monthly professional",
            "12000",
            "12,000",
            "₦12000",
            "₦12,000",
            "300 credits professional",
            "pro 300",
        ],
    },
    "P2": {
        "code": "P2",
        "plan_code": "professional_quarterly",
        "name": "Professional Quarterly",
        "price": 33600,
        "credits": 900,
        "cycle": "quarterly",
        "family": "professional",
        "aliases": [
            "p2",
            "professional quarterly",
            "pro quarterly",
            "quarterly professional",
            "33600",
            "33,600",
            "₦33600",
            "₦33,600",
            "900 credits",
            "pro 900",
        ],
    },
    "P3": {
        "code": "P3",
        "plan_code": "professional_yearly",
        "name": "Professional Yearly",
        "price": 122400,
        "credits": 3600,
        "cycle": "yearly",
        "family": "professional",
        "aliases": [
            "p3",
            "professional yearly",
            "pro yearly",
            "yearly professional",
            "122400",
            "122,400",
            "₦122400",
            "₦122,400",
            "3600 credits",
            "pro 3600",
        ],
    },
    "B1": {
        "code": "B1",
        "plan_code": "business_monthly",
        "name": "Business Monthly",
        "price": 25000,
        "credits": 800,
        "cycle": "monthly",
        "family": "business",
        "aliases": [
            "b1",
            "business",
            "business monthly",
            "monthly business",
            "25000",
            "25,000",
            "₦25000",
            "₦25,000",
            "800 credits",
            "business 800",
        ],
    },
    "B2": {
        "code": "B2",
        "plan_code": "business_quarterly",
        "name": "Business Quarterly",
        "price": 70000,
        "credits": 2400,
        "cycle": "quarterly",
        "family": "business",
        "aliases": [
            "b2",
            "business quarterly",
            "quarterly business",
            "70000",
            "70,000",
            "₦70000",
            "₦70,000",
            "2400 credits",
            "business 2400",
        ],
    },
    "B3": {
        "code": "B3",
        "plan_code": "business_yearly",
        "name": "Business Yearly",
        "price": 255000,
        "credits": 9600,
        "cycle": "yearly",
        "family": "business",
        "aliases": [
            "b3",
            "business yearly",
            "business annual",
            "yearly business",
            "255000",
            "255,000",
            "₦255000",
            "₦255,000",
            "9600 credits",
            "business 9600",
        ],
    },
}

TOPUP_OPTIONS: Dict[str, Dict[str, Any]] = {
    "T10": {
        "code": "T10",
        "name": "Starter Add-on",
        "credits": 10,
        "price": 500,
        "aliases": ["t10", "10 credits", "10 extra credits", "500", "₦500", "buy 10", "topup 10"],
    },
    "T50": {
        "code": "T50",
        "name": "Smart Add-on",
        "credits": 50,
        "price": 2000,
        "aliases": ["t50", "50 credits", "50 extra credits", "2000", "2,000", "₦2000", "₦2,000", "topup 50"],
    },
    "T100": {
        "code": "T100",
        "name": "Growth Add-on",
        "credits": 100,
        "price": 3500,
        "aliases": ["t100", "100 extra credits", "100 topup", "3500", "3,500", "₦3500", "₦3,500", "topup 100"],
    },
    "T500": {
        "code": "T500",
        "name": "Business Add-on",
        "credits": 500,
        "price": 15000,
        "aliases": ["t500", "500 credits", "500 extra credits", "15000", "15,000", "₦15000", "₦15,000", "topup 500"],
    },
}

TOOL_OPTIONS: Dict[str, Dict[str, Any]] = {
    "F1": {"code": "F1", "action": "calculator_menu", "name": "Tax calculators", "aliases": ["f1", "calculator", "calculators", "tax calculator", "tax calculators", "calc"]},
    "F2": {"code": "F2", "action": "paye_guide", "name": "PAYE filing guide", "aliases": ["f2", "paye guide", "paye filing", "paye filing guide", "employee tax guide"]},
    "F3": {"code": "F3", "action": "vat_guide", "name": "VAT filing guide", "aliases": ["f3", "vat guide", "vat filing", "vat filing guide"]},
    "F4": {"code": "F4", "action": "cit_guide", "name": "CIT filing guide", "aliases": ["f4", "cit guide", "company tax guide", "company income tax guide", "cit filing"]},
    "F5": {"code": "F5", "action": "wht_guide", "name": "WHT guide", "aliases": ["f5", "wht guide", "withholding tax guide", "withholding guide"]},
    "F6": {"code": "F6", "action": "deadlines", "name": "Tax deadlines/calendar", "aliases": ["f6", "deadline", "deadlines", "calendar", "tax calendar", "due dates"]},
    "F7": {"code": "F7", "action": "filing_checklist", "name": "Filing checklist", "aliases": ["f7", "checklist", "filing checklist", "tax checklist"]},
    "F8": {"code": "F8", "action": "main_menu", "name": "Back to main menu", "aliases": ["f8", "main", "main menu", "menu"]},
}

CALC_OPTIONS: Dict[str, Dict[str, Any]] = {
    "C1": {"code": "C1", "action": "paye_calc", "name": "PAYE calculator", "aliases": ["c1", "paye calculator", "paye calc", "salary tax", "employee tax"]},
    "C2": {"code": "C2", "action": "cit_calc", "name": "Company Income Tax calculator", "aliases": ["c2", "cit calculator", "cit calc", "company income tax calculator", "company tax calculator"]},
    "C3": {"code": "C3", "action": "vat_calc", "name": "VAT calculator", "aliases": ["c3", "vat calculator", "vat calc"]},
    "C4": {"code": "C4", "action": "wht_calc", "name": "Withholding Tax calculator", "aliases": ["c4", "wht calculator", "wht calc", "withholding tax calculator"]},
    "C5": {"code": "C5", "action": "salary_compare", "name": "Salary/net pay comparison", "aliases": ["c5", "salary compare", "compare salary", "net pay", "salary comparison"]},
    "C6": {"code": "C6", "action": "tax_quiz", "name": "Tax quiz", "aliases": ["c6", "quiz", "tax quiz", "question quiz"]},
    "C7": {"code": "C7", "action": "deadlines", "name": "Tax calendar/deadlines", "aliases": ["c7", "tax calendar", "deadlines", "deadline"]},
    "C8": {"code": "C8", "action": "tools_menu", "name": "Back to Tax Tools", "aliases": ["c8", "back to tools", "tools"]},
}


QUIZ_BANK: List[Dict[str, Any]] = [
    {
        "id": "q_paye_1",
        "category": "PAYE",
        "question": "Which tax is usually deducted from an employee's salary by the employer in Nigeria?",
        "options": {"A": "VAT", "B": "PAYE", "C": "Company Income Tax", "D": "Import Duty"},
        "answer": "B",
        "explain": "PAYE means Pay-As-You-Earn. Employers deduct it from employee salaries and remit it to the relevant State Internal Revenue Service.",
    },
    {
        "id": "q_paye_2",
        "category": "PAYE",
        "question": "Who usually remits PAYE after deducting it from employees' salaries?",
        "options": {"A": "The employee's landlord", "B": "The employer", "C": "The bank cashier", "D": "The customer"},
        "answer": "B",
        "explain": "The employer deducts PAYE from salary and remits it to the relevant State Internal Revenue Service.",
    },
    {
        "id": "q_paye_3",
        "category": "PAYE",
        "question": "PAYE is mainly connected to which type of income?",
        "options": {"A": "Salary and employment income", "B": "Imported goods only", "C": "Company share capital", "D": "Bank transfer charges"},
        "answer": "A",
        "explain": "PAYE applies to salary or employment income earned by individuals.",
    },
    {
        "id": "q_paye_4",
        "category": "PAYE",
        "question": "If a company owner earns salary from the company, what tax may apply to that salary?",
        "options": {"A": "PAYE / Personal Income Tax", "B": "Only Company Income Tax", "C": "Import Duty", "D": "No tax ever"},
        "answer": "A",
        "explain": "Company tax and personal income tax are separate. Salary paid to the owner can still be subject to PAYE/PIT.",
    },
    {
        "id": "q_vat_1",
        "category": "VAT",
        "question": "What is the common standard VAT rate used in Nigeria for many VATable supplies?",
        "options": {"A": "2.5%", "B": "5%", "C": "7.5%", "D": "30%"},
        "answer": "C",
        "explain": "The commonly applied standard VAT rate is 7.5%, subject to current law and transaction-specific exemptions.",
    },
    {
        "id": "q_vat_2",
        "category": "VAT",
        "question": "VAT is generally charged on what?",
        "options": {"A": "Taxable supply of goods and services", "B": "Employee age", "C": "Company logo", "D": "Only rent receipts"},
        "answer": "A",
        "explain": "VAT is charged on taxable supplies of goods and services unless the item is exempt or zero-rated.",
    },
    {
        "id": "q_vat_3",
        "category": "VAT",
        "question": "Which agency is mainly responsible for VAT administration in Nigeria?",
        "options": {"A": "FIRS", "B": "FRSC", "C": "NIMC", "D": "INEC"},
        "answer": "A",
        "explain": "VAT is generally administered by the Federal Inland Revenue Service (FIRS).",
    },
    {
        "id": "q_vat_4",
        "category": "VAT",
        "question": "Input VAT usually refers to VAT on what?",
        "options": {"A": "Eligible purchases", "B": "Employee birthdays", "C": "Company name reservation", "D": "Social media followers"},
        "answer": "A",
        "explain": "Input VAT is VAT paid on eligible business purchases, which may be offset against output VAT where allowed.",
    },
    {
        "id": "q_cit_1",
        "category": "Company Tax",
        "question": "Company Income Tax is mainly charged on what?",
        "options": {"A": "Company taxable profit", "B": "Employee salary", "C": "Customer phone number", "D": "Bank name only"},
        "answer": "A",
        "explain": "Company Income Tax is charged on taxable profit after allowable deductions and adjustments, not directly on employee salary.",
    },
    {
        "id": "q_cit_2",
        "category": "Company Tax",
        "question": "Which body mainly administers Company Income Tax in Nigeria?",
        "options": {"A": "FIRS", "B": "State traffic agency", "C": "Local electricity vendor", "D": "Company receptionist"},
        "answer": "A",
        "explain": "Company Income Tax is generally administered by the Federal Inland Revenue Service (FIRS).",
    },
    {
        "id": "q_cit_3",
        "category": "Company Tax",
        "question": "For company tax purposes, salary paid to staff is generally treated as what?",
        "options": {"A": "An expense, if properly documented and allowable", "B": "A shareholder gift only", "C": "VAT output only", "D": "Import duty"},
        "answer": "A",
        "explain": "Employee salaries are normally business expenses if properly incurred, documented, and allowable under tax rules.",
    },
    {
        "id": "q_cit_4",
        "category": "Company Tax",
        "question": "A company should keep which document for tax filing support?",
        "options": {"A": "Financial statements and records", "B": "Only staff nicknames", "C": "Only social media posts", "D": "Only office paint color"},
        "answer": "A",
        "explain": "Financial statements, accounting records, receipts, invoices, and supporting schedules help support company tax filings.",
    },
    {
        "id": "q_wht_1",
        "category": "WHT",
        "question": "Withholding Tax is usually deducted at what point?",
        "options": {"A": "At payment point", "B": "Only after 10 years", "C": "When opening email", "D": "Never"},
        "answer": "A",
        "explain": "WHT is commonly deducted at payment point where the transaction is subject to withholding tax.",
    },
    {
        "id": "q_wht_2",
        "category": "WHT",
        "question": "WHT deducted from a supplier is generally treated as what for that supplier?",
        "options": {"A": "A tax credit where applicable", "B": "A birthday gift", "C": "A bank loan", "D": "A visa fee"},
        "answer": "A",
        "explain": "Withholding Tax may serve as a credit against the supplier's final tax liability where applicable.",
    },
    {
        "id": "q_wht_3",
        "category": "WHT",
        "question": "Before deducting WHT, what should a business confirm?",
        "options": {"A": "Whether the transaction is subject to WHT and the correct rate", "B": "The supplier's favorite food", "C": "The color of the invoice", "D": "The customer's shoe size"},
        "answer": "A",
        "explain": "WHT depends on the transaction type, parties involved, and applicable rate, so these should be confirmed.",
    },
    {
        "id": "q_deadline_1",
        "category": "Deadlines",
        "question": "Why should a business track tax filing deadlines?",
        "options": {"A": "To avoid late filing penalties and interest", "B": "To increase office rent", "C": "To change phone wallpaper", "D": "To avoid keeping records"},
        "answer": "A",
        "explain": "Tracking deadlines helps avoid penalties, interest, and compliance problems.",
    },
    {
        "id": "q_deadline_2",
        "category": "Deadlines",
        "question": "If a reminder date has already passed, what should the system do?",
        "options": {"A": "Reject it or suggest a valid reminder period", "B": "Accept it silently", "C": "Delete all reminders", "D": "Charge VAT automatically"},
        "answer": "A",
        "explain": "A reminder should only be accepted if the reminder date can still occur in the future or today.",
    },
    {
        "id": "q_records_1",
        "category": "Records",
        "question": "Which records are useful for tax compliance?",
        "options": {"A": "Invoices, receipts, payroll, bank statements, and filings", "B": "Only WhatsApp stickers", "C": "Only office chairs", "D": "Only passwords"},
        "answer": "A",
        "explain": "Proper records help support tax calculations, filings, audits, and dispute resolution.",
    },
    {
        "id": "q_records_2",
        "category": "Records",
        "question": "Why should a business keep PAYE records?",
        "options": {"A": "To prove salary payments and tax deductions", "B": "To decorate the office", "C": "To replace CAC documents", "D": "To avoid all tax duties"},
        "answer": "A",
        "explain": "PAYE records help show salaries paid, deductions made, and remittances to the relevant tax authority.",
    },
    {
        "id": "q_general_1",
        "category": "General",
        "question": "Which statement is most correct?",
        "options": {"A": "Different taxes may apply to different income or transaction types", "B": "One tax covers everything forever", "C": "VAT is always personal income tax", "D": "PAYE is import duty"},
        "answer": "A",
        "explain": "Different taxes apply to different bases, such as salaries, company profits, taxable supplies, or specific transactions.",
    },
]

QUIZ_FREE_DAILY_LIMIT = 12



# =============================================================================
# Generic helpers
# =============================================================================

def _sb():
    return supabase() if callable(supabase) else supabase


_ADMIN_SUPABASE_CLIENT = None


def _admin_sb():
    """
    Return a Supabase service-role client for backend webhook writes.

    Why this exists:
    normal route reads can work with the shared client, but the WhatsApp webhook
    must write channel_identities after RLS tightening. If the shared client is
    accidentally anon/public, channel_identities INSERT returns 401.
    """
    global _ADMIN_SUPABASE_CLIENT

    if _ADMIN_SUPABASE_CLIENT is not None:
        return _ADMIN_SUPABASE_CLIENT

    try:
        if get_supabase_client is not None:  # type: ignore[truthy-function]
            _ADMIN_SUPABASE_CLIENT = get_supabase_client(admin=True)  # type: ignore[misc]
            return _ADMIN_SUPABASE_CLIENT
    except Exception:
        pass

    url = _clean(os.getenv("SUPABASE_URL"))
    key = _clean(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE"))
    if url and key and _create_supabase_client is not None:
        try:
            _ADMIN_SUPABASE_CLIENT = _create_supabase_client(url, key)
            return _ADMIN_SUPABASE_CLIENT
        except Exception:
            pass

    # Last resort. This may be anon if env is misconfigured; callers will expose
    # the write error in logs rather than crashing the webhook.
    return _sb()


def _query_one_admin(table: str, select_cols: str = "*", **eq_filters: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        q = _admin_sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        res = q.limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return rows[0], None
        return None, None
    except Exception as exc:
        return None, f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_update_admin(table: str, payload: Dict[str, Any], **eq_filters: Any) -> Dict[str, Any]:
    try:
        q = _admin_sb().table(table).update(payload)
        for col, val in eq_filters.items():
            q = q.eq(col, val)
        res = q.execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _safe_insert_admin(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = _admin_sb().table(table).insert(payload).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _safe_delete_admin(table: str, **eq_filters: Any) -> Dict[str, Any]:
    try:
        q = _admin_sb().table(table).delete()
        for col, val in eq_filters.items():
            q = q.eq(col, val)
        res = q.execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(value: Any, limit: int = 700) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _normalize_phone(value: Any) -> str:
    text = re.sub(r"\D+", "", _clean(value))
    if text.startswith("00"):
        text = text[2:]
    return text


def _display_phone(value: Any) -> str:
    phone = _normalize_phone(value)
    return f"+{phone}" if phone else ""


def _safe_json() -> Dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _debug_enabled() -> bool:
    return _truthy(os.getenv("DEBUG_WHATSAPP")) or _truthy(os.getenv("DEBUG_AI"))


def _base_url() -> str:
    return _clean(os.getenv("APP_BASE_URL") or os.getenv("FRONTEND_BASE_URL") or "https://www.naijataxguides.com").rstrip("/")


def _whatsapp_bot_phone() -> str:
    """
    Real WhatsApp bot/business chat number in international format.

    Required Koyeb env:
    WHATSAPP_BOT_PHONE_NUMBER=234XXXXXXXXXX

    Do not use WHATSAPP_PHONE_NUMBER_ID here; that is Meta's internal ID.
    """
    return _normalize_phone(
        os.getenv("WHATSAPP_BOT_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_BOT_PHONE")
        or os.getenv("WHATSAPP_DISPLAY_PHONE_NUMBER")
        or os.getenv("META_WHATSAPP_DISPLAY_PHONE_NUMBER")
    )


def _whatsapp_return_url(customer_wa_id: str, message: str) -> str:
    """
    Paystack callback for WhatsApp-originated payments.

    It opens the BOT chat, while the customer phone stays in metadata for
    activation and notification.
    """
    bot_phone = _whatsapp_bot_phone()
    safe_message = quote(_clean(message)[:900])

    if not bot_phone:
        return f"{_base_url()}/billing/success?source=whatsapp&missing_bot_phone=1"

    return f"https://wa.me/{bot_phone}?text={safe_message}"


def _normalize_text(value: Any) -> str:
    text = _lower(value)
    text = text.replace("₦", " ")
    text = re.sub(r"[,]+", "", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_amounts(value: Any) -> List[int]:
    text = _clean(value).replace(",", "")
    amounts: List[int] = []
    for match in re.finditer(r"(?:₦\s*)?(\d{2,9})", text):
        try:
            amounts.append(int(match.group(1)))
        except Exception:
            pass
    return amounts


def _contains_credit_phrase(value: Any) -> bool:
    text = _normalize_text(value)
    return "credit" in text or "credits" in text


def _extract_payment_reference(value: Any) -> str:
    text = _clean(value)
    match = re.search(r"\b(NTG(?:-WA|-WA-TOPUP|-TOPUP)?-[A-Za-z0-9]+)\b", text, flags=re.I)
    return match.group(1) if match else ""



def _is_valid_email(value: Any) -> bool:
    text = _lower(value)
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text))


def _is_placeholder_email(value: Any) -> bool:
    email = _lower(value)
    return not email or email.startswith("user_") or email.endswith("@naijataxguides.com")


def _account_email(account: Optional[Dict[str, Any]]) -> str:
    return _clean((account or {}).get("email"))


def _ensure_email_or_prompt(
    *,
    wa_id: str,
    account: Optional[Dict[str, Any]],
    pending_action: str,
    data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    email = _account_email(account)
    if email and not _is_placeholder_email(email):
        return None

    _set_session_state(wa_id, context="collect_email", pending_action=pending_action, data=data)
    body = (
        "📧 Please enter your email address to continue with payment.\n\n"
        "Example: name@email.com\n\n"
        "This email will be used for your Paystack receipt and account record."
    )
    return {"ok": True, "handled": "collect_email", "send_result": _send_whatsapp_text(wa_id, body)}



# _V10_PATCH_MARKER
def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session_data(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = (state or {}).get("data")
    return data if isinstance(data, dict) else {}


def _patch_session_data(wa_id: str, **updates: Any) -> None:
    state = _get_session_state(wa_id)
    data = _session_data(state)
    data.update({k: v for k, v in updates.items() if v is not None})
    _set_session_state(
        wa_id,
        context=_clean((state or {}).get("context")) or "main",
        pending_action=_clean((state or {}).get("pending_action")) or "",
        data=data,
    )


def _deadline_allowed_for_account(account_id: str) -> bool:
    return _is_active_paid_subscription(account_id)


def _deadline_usage_text() -> str:
    return (
        "📅 *Deadline commands*\n\n"
        "D1 PAYE 2026-05-29 7 - create reminder\n"
        "D2 - view my reminders\n"
        "D3 1 - delete reminder number 1 from D2 list\n"
        "D4 1 14 - set reminder number 1 to 14 days before\n\n"
        "Paid users can create and manage custom deadline reminders. Free users can still view general tax guidance."
    )


def _quiz_usage_text() -> str:
    return (
        "🧠 *Tax Quiz Centre*\n\n"
        "Q1 - start a quiz\n"
        "Q2 - choose category\n"
        "Q3 - view score\n"
        "Q4 - review last answer\n"
        "Q5 - AI explanation for last quiz answer\n\n"
        "Reply A, B, C, or D after a question. Free users get 12 non-AI quiz attempts daily. Paid users get unlimited non-AI quiz attempts."
    )


def _extract_choice_letter(text: Any) -> str:
    value = _lower(text)
    match = re.search(r"\b([abcd])\b", value)
    return match.group(1).upper() if match else ""


def _quiz_attempts_for_today(data: Dict[str, Any]) -> int:
    usage = data.get("quiz_usage")
    if not isinstance(usage, dict):
        return 0
    if usage.get("date") != _today_key():
        return 0
    return int(usage.get("attempts") or 0)


def _increment_quiz_attempts(wa_id: str) -> int:
    state = _get_session_state(wa_id)
    data = _session_data(state)
    usage = data.get("quiz_usage") if isinstance(data.get("quiz_usage"), dict) else {}
    today = _today_key()
    if usage.get("date") != today:
        usage = {"date": today, "attempts": 0}
    usage["attempts"] = int(usage.get("attempts") or 0) + 1
    data["quiz_usage"] = usage
    _set_session_state(
        wa_id,
        context=_clean((state or {}).get("context")) or "main",
        pending_action=_clean((state or {}).get("pending_action")) or "",
        data=data,
    )
    return int(usage["attempts"])



def _date_today_v13() -> date:
    return datetime.now(timezone.utc).date()


def _parse_date_v13(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(_clean(value), "%Y-%m-%d").date()
    except Exception:
        return None


def _deadline_validation_v13(due_date_text: Any, reminder_days: Any) -> Dict[str, Any]:
    today = _date_today_v13()
    due_date = _parse_date_v13(due_date_text)
    try:
        days = int(reminder_days)
    except Exception:
        days = 7
    days = max(0, min(365, days))
    if not due_date:
        return {"ok": False, "reason": "invalid_due_date", "max_days": 0, "message": "The due date is invalid. Use YYYY-MM-DD, for example D1 PAYE 2026-05-29 7."}
    days_until_due = (due_date - today).days
    reminder_date = due_date - timedelta(days=days)
    if days_until_due < 0:
        return {"ok": False, "reason": "due_date_passed", "today": today.isoformat(), "due_date": due_date.isoformat(), "reminder_date": reminder_date.isoformat(), "max_days": 0, "message": f"The due date {due_date.isoformat()} has already passed. Please choose a future due date."}
    if reminder_date < today:
        max_days = max(0, days_until_due)
        return {
            "ok": False,
            "reason": "reminder_date_passed",
            "today": today.isoformat(),
            "due_date": due_date.isoformat(),
            "reminder_date": reminder_date.isoformat(),
            "max_days": max_days,
            "message": (
                f"That reminder is no longer possible.\n\n"
                f"Today: {today.isoformat()}\n"
                f"Due date: {due_date.isoformat()}\n"
                f"Requested reminder: {days} day(s) before\n"
                f"Reminder date would be: {reminder_date.isoformat()}\n\n"
                f"Use {max_days} day(s) before or choose a later due date."
            ),
        }
    return {"ok": True, "reason": "valid", "today": today.isoformat(), "due_date": due_date.isoformat(), "reminder_date": reminder_date.isoformat(), "max_days": max(0, days_until_due), "days": days}




def _valid_reminder_time_v14(value: Any) -> str:
    raw = _clean(value or "09:00")
    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if not m:
        return "09:00"
    hour = int(m.group(1)); minute = int(m.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return "09:00"
    return f"{hour:02d}:{minute:02d}"


def _valid_reminder_mode_v14(value: Any) -> str:
    raw = _lower(value or "whatsapp")
    raw = raw.replace(" ", "")
    allowed = {"whatsapp", "email", "sms", "whatsapp,email", "whatsapp,sms", "email,sms", "whatsapp,email,sms"}
    return raw if raw in allowed else "whatsapp"


def _deadline_optional_payload_v14(parsed: Dict[str, Any], wa_id: str, account_row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    mode = _valid_reminder_mode_v14(parsed.get("reminder_mode"))
    account_row = account_row or {}
    return {
        "reminder_time": _valid_reminder_time_v14(parsed.get("reminder_time")),
        "timezone": _clean(parsed.get("timezone") or "Africa/Lagos"),
        "reminder_mode": mode,
        "reminder_email": _clean(account_row.get("email") or "") or None,
        "reminder_phone": _clean(account_row.get("phone_e164") or account_row.get("phone") or wa_id) or None,
    }


def _delete_deadline_by_id_v14(deadline_id: str) -> Dict[str, Any]:
    try:
        res = _sb().table("tax_deadlines").delete().eq("id", deadline_id).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"tax_deadlines: {type(exc).__name__}: {_clip(exc)}"}


def _deadline_computed_status_v13(item: Dict[str, Any]) -> str:
    enabled = bool(item.get("enabled", True))
    validation = _deadline_validation_v13(item.get("due_date"), item.get("reminder_days_before", 7))
    if not validation.get("ok"):
        return "inactive"
    return "active" if enabled else "inactive"

def _deadline_display_line(item: Dict[str, Any], index: int) -> str:
    tax_type = _clean(item.get("tax_type") or item.get("title") or "Tax").upper()
    due = _clean(item.get("due_date") or "No date")
    try:
        days_text = f"{int(item.get('reminder_days_before', 7))} days before"
    except Exception:
        days_text = "7 days before"
    status = _deadline_computed_status_v13(item)
    time_text = _clean(item.get("reminder_time") or "09:00")
    mode_text = _clean(item.get("reminder_mode") or "whatsapp")
    # Keep D2 organized in the current compact style, with time/mode only at the end.
    return f"{index}. {tax_type} - due {due} - reminder {days_text} - {status} - {time_text} via {mode_text}"


def _safe_get(table: str, params: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Safe Supabase SELECT helper used by v10/v11 deadline and quiz utilities.
    Returns [] on failure so WhatsApp webhook does not crash.
    """
    try:
        if "_supabase_get" in globals():
            data = _supabase_get(table, params=params or {})
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return data.get("data") or []
            return []

        url = f"{_supabase_url().rstrip('/')}/rest/v1/{table}"
        response = requests.get(url, headers=_supabase_headers(), params=params or {}, timeout=25)
        if response.status_code >= 400:
            return []
        data = response.json() if response.text else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _get_deadline_list(account_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    # v14: use the same query path/order as D2 so D3/D4 numbers match the visible list.
    rows, err = _query_many("tax_deadlines", "*", limit=limit, account_id=account_id)
    if err:
        return []
    return rows


def _deadline_by_index(account_id: str, index: int) -> Optional[Dict[str, Any]]:
    rows = _get_deadline_list(account_id, limit=10)
    if index < 1 or index > len(rows):
        return None
    return rows[index - 1]




def _supabase_delete(table: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    url = f"{_supabase_url().rstrip('/')}/rest/v1/{table}"
    response = requests.delete(url, headers=_supabase_headers(prefer="return=representation"), params=params or {}, timeout=25)
    try:
        data = response.json() if response.text else []
    except Exception:
        data = response.text
    return {"ok": response.status_code < 400, "status_code": response.status_code, "data": data}


def _query_one(table: str, select_cols: str = "*", **eq_filters: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        res = q.limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], dict):
            return rows[0], None
        return None, None
    except Exception as exc:
        return None, f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _query_many(table: str, select_cols: str = "*", limit: int = 20, order_col: str = "created_at", desc: bool = True, **eq_filters: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        q = _sb().table(table).select(select_cols)
        for col, val in eq_filters.items():
            if val is not None and _clean(val):
                q = q.eq(col, val)
        try:
            q = q.order(order_col, desc=desc)
        except Exception:
            pass
        res = q.limit(limit).execute()
        rows = getattr(res, "data", None) or []
        return [r for r in rows if isinstance(r, dict)], None
    except Exception as exc:
        return [], f"{table}: {type(exc).__name__}: {_clip(exc)}"


def _safe_insert(table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        res = _sb().table(table).insert(payload).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}



def _safe_delete(table: str, **filters: Any) -> Dict[str, Any]:
    try:
        params = {}
        for key, value in filters.items():
            if value is not None and _clean(value):
                params[key] = f"eq.{_clean(value)}"
        return _supabase_delete(table, params=params)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


def _safe_update(table: str, payload: Dict[str, Any], **eq_filters: Any) -> Dict[str, Any]:
    try:
        q = _sb().table(table).update(payload)
        for col, val in eq_filters.items():
            q = q.eq(col, val)
        res = q.execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _safe_upsert(table: str, payload: Dict[str, Any], on_conflict: str) -> Dict[str, Any]:
    try:
        res = _sb().table(table).upsert(payload, on_conflict=on_conflict).execute()
        return {"ok": True, "data": getattr(res, "data", None)}
    except Exception as exc:
        return {"ok": False, "error": f"{table}: {type(exc).__name__}: {_clip(exc)}"}


def _money(amount: int) -> str:
    return f"₦{amount:,.0f}"


# =============================================================================
# WhatsApp payload + outbound
# =============================================================================

def _extract_message(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        entry = (payload.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value") or {}
        messages = value.get("messages") or []
        contacts = value.get("contacts") or []

        if not messages:
            return None

        message = messages[0] or {}
        contact = contacts[0] if contacts else {}

        wa_id = _normalize_phone(message.get("from") or contact.get("wa_id"))
        profile_name = _clean(((contact.get("profile") or {}).get("name")))
        msg_type = _lower(message.get("type"))
        text = ""

        if msg_type == "text":
            text = _clean(((message.get("text") or {}).get("body")))
        elif msg_type == "button":
            button = message.get("button") or {}
            text = _clean(button.get("text") or button.get("payload"))
        elif msg_type == "interactive":
            interactive = message.get("interactive") or {}
            interactive_type = _lower(interactive.get("type"))
            if interactive_type == "button_reply":
                reply = interactive.get("button_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))
            elif interactive_type == "list_reply":
                reply = interactive.get("list_reply") or {}
                text = _clean(reply.get("title") or reply.get("id"))

        return {
            "wa_id": wa_id,
            "from": wa_id,
            "display_phone": _display_phone(wa_id),
            "profile_name": profile_name,
            "message_id": _clean(message.get("id")),
            "type": msg_type,
            "text": text,
            "raw_message": message,
        }
    except Exception:
        return None


def _send_whatsapp_text(to: str, body: str) -> Dict[str, Any]:
    to = _normalize_phone(to)
    body = _clean(body)

    access_token = _clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))
    phone_number_id = _clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))

    if not to or not body:
        return {"ok": False, "error": "missing_to_or_body"}

    if not access_token or not phone_number_id:
        return {
            "ok": False,
            "error": "whatsapp_send_not_configured",
            "missing": {
                "WHATSAPP_ACCESS_TOKEN": not bool(access_token),
                "WHATSAPP_PHONE_NUMBER_ID": not bool(phone_number_id),
            },
            "fallback_body": body,
        }

    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:3900]},
    }

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        result: Dict[str, Any] = {"ok": response.status_code < 400, "status_code": response.status_code}
        try:
            result["response"] = response.json()
        except Exception:
            result["response_text"] = _clip(response.text)
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


# =============================================================================
# State/context
# =============================================================================

def _get_session_state(wa_id: str) -> Dict[str, Any]:
    wa_id = _normalize_phone(wa_id)
    if not wa_id:
        return {"context": "main"}

    row, _err = _query_one("whatsapp_flow_sessions", "*", wa_id=wa_id)
    if row:
        return {
            "context": _clean(row.get("context") or "main"),
            "pending_action": _clean(row.get("pending_action")),
            "data": row.get("data") if isinstance(row.get("data"), dict) else {},
        }
    return {"context": "main", "pending_action": "", "data": {}}


def _set_session_state(wa_id: str, context: str = "main", pending_action: str = "", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    wa_id = _normalize_phone(wa_id)
    if not wa_id:
        return {"ok": False, "error": "missing_wa_id"}

    payload = {
        "wa_id": wa_id,
        "context": context or "main",
        "pending_action": pending_action or None,
        "data": data or {},
        "updated_at": _now_iso(),
    }
    return _safe_upsert("whatsapp_flow_sessions", payload, on_conflict="wa_id")


# =============================================================================
# Account resolution
# =============================================================================

def _account_select_cols() -> str:
    return "id,account_id,provider,provider_user_id,auth_user_id,display_name,phone,phone_e164,email,updated_at,created_at"


def _account_id_from_row(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""
    return _clean(row.get("account_id") or row.get("id"))


def _linked_owner_account_from_channel_account(row: Dict[str, Any], debug: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    If a WhatsApp/Telegram shell account has been linked to a website account,
    use the website owner's account_id for subscription, credits, history and
    dashboard logic.

    This is the production-safe fallback for environments where RLS blocks
    writes to channel_identities but allows the channel shell account to store
    auth_user_id.
    """
    if not isinstance(row, dict):
        return None

    channel_account_id = _account_id_from_row(row)
    owner_account_id = _clean(
        row.get("auth_user_id")
        or row.get("owner_account_id")
        or row.get("linked_account_id")
        or row.get("web_account_id")
    )

    if not owner_account_id or owner_account_id == channel_account_id:
        return None

    owner, err = _query_one("accounts", _account_select_cols(), account_id=owner_account_id)
    debug["steps"].append(
        {
            "table": "accounts",
            "via": "channel_account_auth_user_id",
            "owner_account_id": owner_account_id,
            "channel_account_id": channel_account_id,
            "error": err,
            "found": bool(owner),
        }
    )

    if owner:
        merged = dict(owner)
        merged["linked_channel_account_id"] = channel_account_id
        merged["channel_provider"] = row.get("provider")
        merged["channel_provider_user_id"] = row.get("provider_user_id")
        merged["channel_phone"] = row.get("phone_e164") or row.get("phone")
        return merged

    return {
        "account_id": owner_account_id,
        "id": owner_account_id,
        "provider": "web",
        "provider_user_id": owner_account_id,
        "linked_channel_account_id": channel_account_id,
        "channel_provider": row.get("provider"),
        "channel_provider_user_id": row.get("provider_user_id"),
        "channel_phone": row.get("phone_e164") or row.get("phone"),
    }


def _find_account_by_wa(wa_id: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    wa_id = _normalize_phone(wa_id)
    debug: Dict[str, Any] = {"wa_id": wa_id, "steps": []}

    if not wa_id:
        return None, {**debug, "error": "missing_wa_id"}

    for provider in ("wa", "whatsapp"):
        row, err = _query_one("accounts", _account_select_cols(), provider=provider, provider_user_id=wa_id)
        debug["steps"].append({"table": "accounts", "provider": provider, "error": err, "found": bool(row)})
        if row:
            owner = _linked_owner_account_from_channel_account(row, debug)
            return (owner or row), debug

    for column in ("phone_e164", "phone"):
        for value in (_display_phone(wa_id), wa_id):
            row, err = _query_one("accounts", _account_select_cols(), **{column: value})
            debug["steps"].append({"table": "accounts", "column": column, "error": err, "found": bool(row)})
            if row:
                owner = _linked_owner_account_from_channel_account(row, debug)
                return (owner or row), debug

    for channel_type in ("whatsapp", "wa"):
        identity, err = _query_one("channel_identities", "*", channel_type=channel_type, provider_user_id=wa_id)
        debug["steps"].append({"table": "channel_identities", "channel_type": channel_type, "error": err, "found": bool(identity)})
        if identity:
            account_id = _clean(identity.get("account_id") or identity.get("owner_account_id"))
            if account_id:
                row, row_err = _query_one("accounts", _account_select_cols(), account_id=account_id)
                debug["steps"].append({"table": "accounts", "account_id": account_id, "error": row_err, "found": bool(row)})
                if row:
                    return row, debug
                return {"account_id": account_id, "provider": "wa", "provider_user_id": wa_id}, debug

    return None, debug


def _create_or_update_wa_account(wa_id: str, profile_name: str = "") -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    wa_id = _normalize_phone(wa_id)
    now_iso = _now_iso()

    existing, debug = _find_account_by_wa(wa_id)
    if existing:
        account_id = _account_id_from_row(existing)
        update_payload = {
            "provider": existing.get("provider") or "wa",
            "provider_user_id": existing.get("provider_user_id") or wa_id,
            "phone": existing.get("phone") or _display_phone(wa_id),
            "phone_e164": existing.get("phone_e164") or _display_phone(wa_id),
            "updated_at": now_iso,
        }
        if profile_name and not existing.get("display_name"):
            update_payload["display_name"] = profile_name
        if account_id:
            _safe_update("accounts", update_payload, account_id=account_id)
        return existing, {**debug, "created": False}

    payload = {
        "provider": "wa",
        "provider_user_id": wa_id,
        "display_name": profile_name or _display_phone(wa_id) or "WhatsApp User",
        "phone": _display_phone(wa_id),
        "phone_e164": _display_phone(wa_id),
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    upsert = _safe_upsert("accounts", payload, on_conflict="provider,provider_user_id")
    debug["account_upsert"] = upsert

    row, find_debug = _find_account_by_wa(wa_id)
    debug["after_upsert_find"] = find_debug
    return row, {**debug, "created": True}


def _get_subscription(account_id: str) -> Optional[Dict[str, Any]]:
    row, _err = _query_one("user_subscriptions", "*", account_id=account_id)
    return row


def _plan_family(plan_code: Any) -> str:
    code = _lower(plan_code)
    if "business" in code:
        return "business"
    if "professional" in code:
        return "professional"
    if "starter" in code:
        return "starter"
    return "free"


def _is_active_paid_subscription(account_id: str) -> bool:
    sub = _get_subscription(account_id)
    if not sub:
        return False
    status = _lower(sub.get("status"))
    plan_code = _lower(sub.get("plan_code"))
    if status in {"inactive", "expired", "cancelled", "canceled", "disabled"}:
        return False
    return _plan_family(plan_code) in {"starter", "professional", "business"} and plan_code not in {"free", "free_forever", ""}


def _credit_balance(account_id: str) -> int:
    """Return the shared Usage Credit balance.

    v7 intentionally reads only ai_credit_balances because the live Supabase
    project does not expose credit_balances and that fallback produced noisy
    404 requests in the logs.
    """
    row, _err = _query_one("ai_credit_balances", "*", account_id=account_id)
    if row:
        try:
            return int(row.get("balance") or row.get("credits") or row.get("credit_balance") or 0)
        except Exception:
            return 0
    return 0



def _detect_credit_column(row: Dict[str, Any]) -> str:
    for col in ("balance", "credits", "credit_balance"):
        if col in row:
            return col
    return "balance"


def _debit_q5_usage_credit(account_id: str) -> Dict[str, Any]:
    """
    Hard debit for WhatsApp Q5.

    Reason:
    ask_guarded may call OpenAI successfully without reducing the WhatsApp-visible
    ai_credit_balances row. Q5 must never be free, so this helper checks balance
    and deducts 1 credit before the AI call.
    """
    try:
        row, err = _query_one("ai_credit_balances", "*", account_id=account_id)
        if err:
            return {"ok": False, "error": err, "mode": "balance_lookup_failed"}
        if not row:
            return {"ok": False, "error": "credit_balance_not_found", "mode": "no_balance_row"}

        col = _detect_credit_column(row)
        before = int(row.get(col) or 0)
        if before < 1:
            return {"ok": False, "error": "insufficient_credits", "before": before, "after": before, "column": col}

        after = before - 1
        payload = {col: after, "updated_at": _now_iso()}
        update = _safe_update("ai_credit_balances", payload, account_id=account_id)
        if not update.get("ok"):
            return {
                "ok": False,
                "error": update.get("error") or "credit_update_failed",
                "before": before,
                "after": before,
                "column": col,
            }

        return {"ok": True, "before": before, "after": after, "column": col, "credits_consumed": 1}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}", "mode": "exception"}


def _refund_q5_usage_credit(account_id: str, debit: Dict[str, Any]) -> Dict[str, Any]:
    """
    Refund only if Q5 was pre-debited but the AI explanation failed before delivery.
    """
    try:
        if not debit or not debit.get("ok"):
            return {"ok": False, "error": "no_successful_debit_to_refund"}
        row, err = _query_one("ai_credit_balances", "*", account_id=account_id)
        if err or not row:
            return {"ok": False, "error": err or "balance_row_not_found"}
        col = _clean(debit.get("column") or _detect_credit_column(row))
        current = int(row.get(col) or 0)
        payload = {col: current + int(debit.get("credits_consumed") or 1), "updated_at": _now_iso()}
        update = _safe_update("ai_credit_balances", payload, account_id=account_id)
        return {"ok": bool(update.get("ok")), "data": update}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


def _plan_label(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "Free Forever"

    name = _clean(sub.get("plan_name") or sub.get("plan_code") or "Free Forever")
    status = _clean(sub.get("status") or "active")
    expires = _clean(sub.get("expires_at") or sub.get("current_period_end") or "")
    if expires:
        return f"{name} ({status})\nExpires: {expires[:10]}"
    return f"{name} ({status})"


def _current_plan_code(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "free"
    return _lower(sub.get("plan_code") or sub.get("plan") or "free")


def _subscription_status(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return "inactive"
    return _lower(sub.get("status") or "inactive")


def _subscription_expiry(account_id: str) -> str:
    sub = _get_subscription(account_id)
    if not sub:
        return ""
    return _clean(sub.get("expires_at") or sub.get("current_period_end") or sub.get("valid_until") or "")


def _same_active_plan(account_id: str, selected_plan_code: str) -> bool:
    return _subscription_status(account_id) in {"active", "trialing"} and _current_plan_code(account_id) == _lower(selected_plan_code)


def _plan_rank(plan_code: str) -> int:
    family = _plan_family(plan_code)
    if family == "business":
        return 3
    if family == "professional":
        return 2
    if family == "starter":
        return 1
    return 0


# =============================================================================
# Command recognition
# =============================================================================

def _match_alias(text: str, options: Dict[str, Dict[str, Any]]) -> List[str]:
    norm = _normalize_text(text)
    matches: List[str] = []

    for code, option in options.items():
        aliases = [code] + list(option.get("aliases") or [])
        for alias in aliases:
            alias_norm = _normalize_text(alias)
            if not alias_norm:
                continue
            if norm == alias_norm:
                matches.append(code)
                break
            if len(alias_norm) >= 5 and alias_norm in norm:
                matches.append(code)
                break

    return list(dict.fromkeys(matches))


def _match_amount(text: str, options: Dict[str, Dict[str, Any]]) -> List[str]:
    amounts = set(_extract_amounts(text))
    if not amounts:
        return []
    matches = []
    for code, option in options.items():
        price = int(option.get("price") or 0)
        credits = int(option.get("credits") or 0)
        if price in amounts:
            matches.append(code)
        elif _contains_credit_phrase(text) and credits in amounts:
            matches.append(code)
    return list(dict.fromkeys(matches))


def _recognize(text: str, context: str = "main") -> Dict[str, Any]:
    raw = _clean(text)
    norm = _normalize_text(raw)

    if not raw:
        return {"kind": "empty", "action": "menu"}

    # Compact calculator support: C3985000 means C3 985000.
    # This prevents calculator commands without a space from being treated as link codes or AI questions.
    compact_calc = re.match(r"^(c[1-8])(\d{2,9})$", norm)
    if compact_calc:
        code = compact_calc.group(1).upper()
        amount = compact_calc.group(2)
        if code in CALC_OPTIONS:
            return {
                "kind": "calc",
                "code": code,
                "action": CALC_OPTIONS[code]["action"],
                "rewritten_text": f"{code} {amount}",
            }

    # Exact/prefix command recognition must run before natural question fallback.
    # This guarantees "C1 986000", "D1 PAYE ...", and "Q1" are handled as
    # structured WhatsApp commands, not link codes and not AI questions.
    prefix_match = re.match(r"^(sup[1-6]|cr[1-4]|s[1-3]|p[1-3]|b[1-3]|t(?:10|50|100|500)|f[1-8]|c[1-8]|q[1-5]|d[1-4]|h[1-2])\b", norm)
    if prefix_match:
        code = prefix_match.group(1).upper()
        if code in {"SUP1", "SUP2", "SUP3", "SUP4", "SUP5", "SUP6"}:
            return {"kind": "support", "action": code.lower(), "code": code, "text": raw}
        if code in {"CR1", "CR2", "CR3", "CR4"}:
            return {"kind": "credit_activity", "action": code.lower(), "code": code, "text": raw}
        if code in PLAN_OPTIONS:
            return {"kind": "plan", "code": code}
        if code in TOPUP_OPTIONS:
            return {"kind": "topup", "code": code}
        if code in TOOL_OPTIONS:
            return {"kind": "tool", "code": code, "action": TOOL_OPTIONS[code]["action"]}
        if code in CALC_OPTIONS:
            return {"kind": "calc", "code": code, "action": CALC_OPTIONS[code]["action"]}
        if code in {"Q1", "Q2", "Q3", "Q4", "Q5"}:
            return {"kind": "quiz_action", "code": code, "action": code.lower(), "text": raw}
        if code in {"D1", "D2", "D3", "D4"}:
            return {"kind": "deadline", "action": "deadline", "code": code}
        if code in {"H1", "H2"}:
            return {"kind": "history", "action": code.lower(), "code": code}

    # Invalid command-like inputs should not consume AI credits.
    # Examples: C9, F11, Q9, D9, S9, T20, or unsupported SUP commands.
    if re.match(r"^sup\d+\b", norm):
        return {"kind": "invalid_menu", "action": "invalid_command", "value": raw}
    if re.match(r"^(?:s|p|b|t|f|c|q|d|h|cr)\d+\b", norm):
        return {"kind": "invalid_menu", "action": "invalid_command", "value": raw}

    if norm in {"0", "menu", "main", "main menu", "start", "hello", "hi"}:
        return {"kind": "global", "action": "main_menu"}
    if norm in {"back", "go back", "*"}:
        return {"kind": "global", "action": "back"}
    if norm in {"cancel", "stop", "end"}:
        return {"kind": "global", "action": "cancel"}
    if norm in {"help", "8"}:
        return {"kind": "main", "action": "help"}
    if norm in {"q1", "start quiz", "quiz me", "take quiz", "tax quiz"}:
        return {"kind": "quiz_action", "code": "Q1", "action": "q1", "text": raw}
    if norm in {"q2", "quiz rules", "quiz categories", "choose quiz category"}:
        return {"kind": "quiz_action", "code": "Q2", "action": "q2", "text": raw}
    if norm in {"q3", "quiz score", "score", "my quiz score"}:
        return {"kind": "quiz_action", "code": "Q3", "action": "q3", "text": raw}
    if norm in {"q4", "review wrong answers", "wrong answers"}:
        return {"kind": "quiz_action", "code": "Q4", "action": "q4", "text": raw}
    if norm in {"q5", "explain quiz", "ai explanation", "explain last quiz"}:
        return {"kind": "quiz_action", "code": "Q5", "action": "q5", "text": raw}
    if norm in {"d1", "d2", "d3", "d4", "create deadline", "view deadlines", "delete deadline", "deadline reminder", "view reminders", "delete reminder", "reminder settings"}:
        return {"kind": "deadline", "action": "deadline"}
    if norm in {"h1", "history", "my history", "recent history", "recent tax history", "view history"}:
        return {"kind": "history", "action": "h1", "code": "H1"}
    if norm in {"h2", "last answer", "last tax answer", "last history", "latest answer", "latest tax answer"}:
        return {"kind": "history", "action": "h2", "code": "H2"}
    if norm in {"credits activity", "credit activity", "credit menu", "usage activity", "usage credit activity"}:
        return {"kind": "credit_activity", "action": "menu", "code": "CR", "text": raw}
    if norm in {"cr1", "credit balance", "usage credit balance", "my credit balance"}:
        return {"kind": "credit_activity", "action": "cr1", "code": "CR1", "text": raw}
    if norm in {"cr2", "recent credit activity", "recent credits", "credit logs", "credit history"}:
        return {"kind": "credit_activity", "action": "cr2", "code": "CR2", "text": raw}
    if norm in {"cr3", "ai credit deductions", "credit deductions", "deductions", "credits deducted"}:
        return {"kind": "credit_activity", "action": "cr3", "code": "CR3", "text": raw}
    if norm in {"cr4", "credit additions", "topup history", "top-up history", "credit topups", "credit top-ups"}:
        return {"kind": "credit_activity", "action": "cr4", "code": "CR4", "text": raw}
    if norm in {"support", "help support", "support menu", "customer support", "contact support"}:
        return {"kind": "support", "action": "menu", "code": "SUP", "text": raw}
    if norm in {"sup1", "create ticket", "open ticket", "new ticket", "support ticket"}:
        return {"kind": "support", "action": "sup1", "code": "SUP1", "text": raw}
    if norm in {"sup2", "my tickets", "view tickets", "support tickets"}:
        return {"kind": "support", "action": "sup2", "code": "SUP2", "text": raw}
    if norm in {"sup3", "latest ticket", "last ticket", "recent ticket"}:
        return {"kind": "support", "action": "sup3", "code": "SUP3", "text": raw}
    if norm in {"sup4", "reply ticket", "reply to ticket", "support reply", "respond to ticket"}:
        return {"kind": "support", "action": "sup4", "code": "SUP4", "text": raw}
    if norm in {"sup5", "close ticket", "close support ticket", "resolve ticket"}:
        return {"kind": "support", "action": "sup5", "code": "SUP5", "text": raw}
    if norm in {"sup6", "support email", "contact email", "email support"}:
        return {"kind": "support", "action": "sup6", "code": "SUP6", "text": raw}

    main_map = {
        "1": "ask_prompt",
        "ask": "ask_prompt",
        "question": "ask_prompt",
        "tax question": "ask_prompt",
        "2": "credits",
        "credit": "credits",
        "credits": "credits",
        "balance": "credits",
        "usage credits": "credits",
        "3": "plan",
        "plan": "plan",
        "subscription": "plan",
        "current plan": "plan",
        "4": "plans_menu",
        "plans": "plans_menu",
        "upgrade": "plans_menu",
        "subscribe": "plans_menu",
        "5": "link_instruction",
        "link": "link_instruction",
        "connect": "link_instruction",
        "connect website": "link_instruction",
        "link account": "link_instruction",
        "unlink": "link_instruction",
        "unlink whatsapp": "link_instruction",
        "unlink account": "link_instruction",
        "disconnect": "link_instruction",
        "disconnect whatsapp": "link_instruction",
        "6": "topup_menu",
        "topup": "topup_menu",
        "top up": "topup_menu",
        "buy credits": "topup_menu",
        "addon": "topup_menu",
        "add on": "topup_menu",
        "add ons": "topup_menu",
        "7": "tools_menu",
        "tools": "tools_menu",
        "tax tools": "tools_menu",
        "filing": "tools_menu",
        "tax filing": "tools_menu",
    }
    if norm in main_map:
        return {"kind": "main", "action": main_map[norm]}

    if norm.isdigit() and norm not in {"0", "1", "2", "3", "4", "5", "6", "7", "8"}:
        return {"kind": "invalid_menu", "action": "invalid_menu", "value": raw}

    plan_matches = list(dict.fromkeys(_match_alias(raw, PLAN_OPTIONS) + _match_amount(raw, PLAN_OPTIONS)))
    topup_matches = list(dict.fromkeys(_match_alias(raw, TOPUP_OPTIONS) + _match_amount(raw, TOPUP_OPTIONS)))
    tool_matches = _match_alias(raw, TOOL_OPTIONS)
    calc_matches = _match_alias(raw, CALC_OPTIONS)

    if context == "plans" and plan_matches:
        if len(plan_matches) == 1:
            return {"kind": "plan", "code": plan_matches[0]}
        return {"kind": "ambiguous", "area": "plans", "codes": plan_matches}

    if context == "topup" and topup_matches:
        if len(topup_matches) == 1:
            return {"kind": "topup", "code": topup_matches[0]}
        return {"kind": "ambiguous", "area": "topup", "codes": topup_matches}

    if context == "tools" and tool_matches:
        if len(tool_matches) == 1:
            return {"kind": "tool", "code": tool_matches[0], "action": TOOL_OPTIONS[tool_matches[0]]["action"]}
        return {"kind": "ambiguous", "area": "tools", "codes": tool_matches}

    if context == "calc" and calc_matches:
        if len(calc_matches) == 1:
            return {"kind": "calc", "code": calc_matches[0], "action": CALC_OPTIONS[calc_matches[0]]["action"]}
        return {"kind": "ambiguous", "area": "calc", "codes": calc_matches}

    # Context-free matching.
    combined = []
    for c in plan_matches:
        combined.append(("plan", c))
    for c in topup_matches:
        combined.append(("topup", c))
    for c in tool_matches:
        combined.append(("tool", c))
    for c in calc_matches:
        combined.append(("calc", c))

    if len(combined) == 1:
        kind, code = combined[0]
        if kind == "tool":
            return {"kind": kind, "code": code, "action": TOOL_OPTIONS[code]["action"]}
        if kind == "calc":
            return {"kind": kind, "code": code, "action": CALC_OPTIONS[code]["action"]}
        return {"kind": kind, "code": code}

    if len(combined) > 1:
        return {"kind": "ambiguous", "area": "mixed", "matches": combined}

    return {"kind": "question", "action": "ask_question"}



# =============================================================================
# Dynamic WhatsApp link state helpers
# =============================================================================

def _whatsapp_link_identity(wa_id: str = "", account_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Return the active WhatsApp channel identity for this WhatsApp number/account.

    This reads through the admin/service-role client so the WhatsApp menu can
    reflect the real link state even after production RLS tightening.
    """
    clean_wa = _normalize_phone(wa_id)
    acct = _clean(account_id)

    lookups: List[Dict[str, Any]] = []
    if clean_wa:
        lookups.append({"channel_type": "whatsapp", "provider_user_id": clean_wa})
    if acct:
        lookups.append({"account_id": acct, "channel_type": "whatsapp"})

    for filters in lookups:
        row, _err = _query_one_admin("channel_identities", "*", **filters)
        if not row:
            continue
        if row.get("is_verified") is False or row.get("verified") is False:
            continue
        return row

    # Legacy fallback only. The durable source is now channel_identities.
    if clean_wa:
        row, _err = _query_one_admin("accounts", _account_select_cols(), provider="wa", provider_user_id=clean_wa)
        if row and _clean(row.get("auth_user_id")):
            return {
                "account_id": _clean(row.get("auth_user_id")),
                "channel_type": "whatsapp",
                "provider_user_id": clean_wa,
                "is_verified": True,
                "metadata": {"source": "accounts.auth_user_id_fallback"},
            }

    return None


def _is_whatsapp_linked(wa_id: str = "", account_id: str = "") -> bool:
    return bool(_whatsapp_link_identity(wa_id=wa_id, account_id=account_id))


def _unlink_whatsapp_channel(wa_id: str, account_id: str = "") -> Dict[str, Any]:
    """
    Unlink only the current WhatsApp channel.

    The website remains the main place to manage channels, but WhatsApp menu item
    5 now correctly changes to unlink when already linked. This helper supports
    that action without touching unrelated Telegram records or other users.
    """
    clean_wa = _normalize_phone(wa_id)
    acct = _clean(account_id)
    identity = _whatsapp_link_identity(wa_id=clean_wa, account_id=acct)

    delete_result: Dict[str, Any] = {"ok": True, "data": []}
    if identity and identity.get("id"):
        delete_result = _safe_delete_admin("channel_identities", id=identity.get("id"))
        if not delete_result.get("ok"):
            return {
                "ok": False,
                "error": "channel_identity_delete_failed",
                "detail": delete_result.get("error"),
            }

    # Clear old accounts.auth_user_id fallback rows for this WhatsApp number.
    try:
        wa_account, _err = _query_one_admin("accounts", _account_select_cols(), provider="wa", provider_user_id=clean_wa)
        wa_account_id = _account_id_from_row(wa_account)
        if wa_account_id:
            _safe_update_admin("accounts", {"auth_user_id": None, "updated_at": _now_iso()}, account_id=wa_account_id)
    except Exception:
        pass

    return {
        "ok": True,
        "unlinked": bool(identity),
        "provider_user_id": clean_wa,
        "account_id": acct,
        "delete_result": delete_result if _debug_enabled() else None,
    }


# =============================================================================
# Menus + descriptions
# =============================================================================

def _main_menu(wa_id: str = "", account_id: str = "") -> str:
    linked = _is_whatsapp_linked(wa_id=wa_id, account_id=account_id) if (wa_id or account_id) else False
    option_5 = "5️⃣ Unlink website account 🔓" if linked else "5️⃣ Link website account 🔗"

    return (
        "🇳🇬 *Naija Tax Guide*\n\n"
        "Reply with:\n"
        "1️⃣ Ask a tax question\n"
        "2️⃣ Check Usage Credits 💎\n"
        "3️⃣ Check current plan 📌\n"
        "4️⃣ View subscription plans 🛒\n"
        f"{option_5}\n"
        "6️⃣ Buy Usage Credit add-ons 💳\n"
        "7️⃣ Tax tools, filing & quiz 🧰\n"
        "8️⃣ Help / Menu ℹ️\n\n"
        "Quick commands:\n"
        "H1 - Recent tax history 🕘\n"
        "H2 - Last tax answer 📌\n"
        "SUP1 - Create support ticket 🛟\n"
        "SUP2 - View support tickets 🎫\n"
        "SUP4 - Reply to support ticket ✍️\n"
        "SUP5 - Close support ticket 🔒\n"
        "0 or MENU - Main menu 🏠\n"
        "* or BACK - Go back ↩️\n"
        "CANCEL - Cancel current flow ❌\n\n"
        "You can also type your Nigerian tax question directly."
    )


def _plans_menu() -> str:
    return (
        "📌 Subscription Plans\n\n"
        "S1 - Starter Monthly - ₦5,000 - 100 credits\n"
        "S2 - Starter Quarterly - ₦14,000 - 300 credits\n"
        "S3 - Starter Yearly - ₦51,000 - 1,200 credits\n\n"
        "P1 - Professional Monthly - ₦12,000 - 300 credits\n"
        "P2 - Professional Quarterly - ₦33,600 - 900 credits\n"
        "P3 - Professional Yearly - ₦122,400 - 3,600 credits\n\n"
        "B1 - Business Monthly - ₦25,000 - 800 credits\n"
        "B2 - Business Quarterly - ₦70,000 - 2,400 credits\n"
        "B3 - Business Yearly - ₦255,000 - 9,600 credits\n\n"
        "Reply with a code like S1, P1, or B1.\n"
        "You can also type Starter Monthly, ₦5,000, or 100 credits."
    )


def _topup_menu() -> str:
    return (
        "💎 Usage Credit Add-ons\n\n"
        "T10 - 10 credits - ₦500\n"
        "T50 - 50 credits - ₦2,000\n"
        "T100 - 100 credits - ₦3,500\n"
        "T500 - 500 credits - ₦15,000\n\n"
        "Reply with T10, T50, T100, or T500.\n"
        "Add-ons are available only to active paid subscribers."
    )


def _tools_menu() -> str:
    return (
        "🧰 *Tax Tools & Filing*\n\n"
        "F1 - Tax calculators 🧮\n"
        "F2 - PAYE filing guide 👥\n"
        "F3 - VAT filing guide 🧾\n"
        "F4 - CIT filing guide 🏢\n"
        "F5 - WHT guide 💼\n"
        "F6 - Tax deadlines/calendar 📅\n"
        "F7 - Filing checklist ✅\n"
        "F8 - Back to main menu 🏠\n\n"
        "Reply with a code like F1, F2, or F7."
    )


def _calc_menu() -> str:
    return (
        "🧮 *Tax Calculators & Quiz*\n\n"
        "C1 - PAYE calculator 👥\n"
        "C2 - Company Income Tax calculator 🏢\n"
        "C3 - VAT calculator 🧾\n"
        "C4 - Withholding Tax calculator 💼\n"
        "C5 - Salary/net pay comparison 📊\n"
        "C6 - Tax quiz 🎯\n"
        "C7 - Tax calendar/deadlines 📅\n"
        "C8 - Back to Tax Tools 🏠\n\n"
        "Examples:\n"
        "C1 250000 monthly\n"
        "C1 salary 250000 pension 8% nhf 2.5% hmo 5000 loan 10000 monthly\n"
        "C2 profit 5000000 revenue 30000000\n"
        "C3 1000000\n"
        "C4 500000 5%"
    )


def _help_text() -> str:
    return (
        "ℹ️ *Help - Naija Tax Guide*\n\n"
        "• Main menu uses numbers 1–8.\n"
        "• Submenus use short codes like S1, T50, F1, C1, Q1, D1, H1, H2, and SUP1.\n"
        "• Use H1 for recent history and H2 for your last tax answer.\n"
        "• Use SUP1 for support, SUP2 for tickets, SUP3 for latest ticket, SUP4 to reply, SUP5 to close, and SUP6 for support email.\n"
        "• You can type natural words too, e.g. Starter Monthly or VAT calculator.\n"
        "• Basic calculators are free. 🧮\n"
        "• Database/cache answers may be served without credit charge. ✅\n"
        "• AI answers require an active paid plan and Usage Credits. 💎\n"
        "• Web, WhatsApp, and Telegram share one credit wallet when linked. 🔗\n\n"
        "Reply 0 for main menu."
    )


def _ambiguous_message(recognition: Dict[str, Any]) -> str:
    lines = ["I found more than one possible match. Please reply with the exact code:\n"]

    if recognition.get("area") == "mixed":
        for kind, code in recognition.get("matches") or []:
            if kind == "plan":
                item = PLAN_OPTIONS[code]
                lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
            elif kind == "topup":
                item = TOPUP_OPTIONS[code]
                lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
            elif kind == "tool":
                item = TOOL_OPTIONS[code]
                lines.append(f"{code} - {item['name']}")
            elif kind == "calc":
                item = CALC_OPTIONS[code]
                lines.append(f"{code} - {item['name']}")
        return "\n".join(lines)

    for code in recognition.get("codes") or []:
        if code in PLAN_OPTIONS:
            item = PLAN_OPTIONS[code]
            lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
        elif code in TOPUP_OPTIONS:
            item = TOPUP_OPTIONS[code]
            lines.append(f"{code} - {item['name']} - {_money(item['price'])} - {item['credits']} credits")
        elif code in TOOL_OPTIONS:
            lines.append(f"{code} - {TOOL_OPTIONS[code]['name']}")
        elif code in CALC_OPTIONS:
            lines.append(f"{code} - {CALC_OPTIONS[code]['name']}")

    return "\n".join(lines)


# =============================================================================
# Link code
# =============================================================================

def _mark_link_token_used_schema_safe(
    *,
    token_table: str,
    token_row: Dict[str, Any],
    wa_id: str,
    wa_account_id: Optional[str] = None,
) -> None:
    """
    Mark a WhatsApp link token as consumed using the confirmed production schema.

    Batch 17 includes the Batch 16 cleanup:
    - The active production link_tokens table uses used_at.
    - provider_user_id is supported and useful for audit.
    - Avoid old/non-confirmed fields such as used, status, channel_account_id,
      used_by_channel_type, and used_by_provider_user_id because they can create
      harmless but noisy Supabase 400 responses.
    - Linking must never fail because token audit update fails.
    """
    token_id = _clean(token_row.get("id"))
    if not token_table or not token_id:
        return

    now_iso = _now_iso()
    clean_wa = _normalize_phone(wa_id)

    payload = {
        "used_at": now_iso,
        "provider_user_id": clean_wa,
    }

    result = _safe_update_admin(token_table, payload, id=token_id)
    if result.get("ok"):
        return

    # Backward-safe fallback for any legacy token table that has used_at
    # but does not expose provider_user_id.
    _safe_update_admin(token_table, {"used_at": now_iso}, id=token_id)


def _link_wa_account_to_owner_fallback(
    *,
    account_id: str,
    wa_id: str,
    profile_name: str = "",
    wa_account_id: Optional[str] = None,
    previous_error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fallback link store when channel_identities insert/update is blocked by RLS.

    Current production logs show channel_identities INSERT can return 401 even
    though accounts PATCH succeeds. To keep linking reliable, we store the web
    account owner on the WhatsApp shell account using auth_user_id.
    
    Other routes in this batch also read this fallback link, so the channel
    appears linked in the website even without a channel_identities row.
    """
    acct = _clean(account_id)
    clean_wa = _normalize_phone(wa_id)
    name = _clean(profile_name) or _display_phone(clean_wa)
    if not acct or not clean_wa:
        return {"ok": False, "error": "accounts_fallback_missing_account_or_wa", "previous_error": previous_error}

    target_account_id = _clean(wa_account_id)
    if not target_account_id:
        for provider in ("wa", "whatsapp"):
            row, _err = _query_one("accounts", _account_select_cols(), provider=provider, provider_user_id=clean_wa)
            if row:
                target_account_id = _account_id_from_row(row)
                break

    if not target_account_id:
        # Last resort: create/update the shell account then locate it.
        upsert = _safe_upsert(
            "accounts",
            {
                "provider": "wa",
                "provider_user_id": clean_wa,
                "display_name": name,
                "phone": _display_phone(clean_wa),
                "phone_e164": _display_phone(clean_wa),
                "auth_user_id": acct,
                "updated_at": _now_iso(),
            },
            on_conflict="provider,provider_user_id",
        )
        row, _err = _query_one("accounts", _account_select_cols(), provider="wa", provider_user_id=clean_wa)
        target_account_id = _account_id_from_row(row)
        if not target_account_id and not upsert.get("ok"):
            return {"ok": False, "error": "accounts_fallback_shell_create_failed", "write_error": upsert.get("error"), "previous_error": previous_error}

    payloads: List[Dict[str, Any]] = [
        {
            "auth_user_id": acct,
            "provider": "wa",
            "provider_user_id": clean_wa,
            "display_name": name,
            "phone": _display_phone(clean_wa),
            "phone_e164": _display_phone(clean_wa),
            "updated_at": _now_iso(),
        },
        {
            "auth_user_id": acct,
            "provider": "wa",
            "provider_user_id": clean_wa,
            "updated_at": _now_iso(),
        },
        {
            "auth_user_id": acct,
            "updated_at": _now_iso(),
        },
    ]

    last_error = None
    for payload in payloads:
        if target_account_id:
            result = _safe_update("accounts", payload, account_id=target_account_id)
        else:
            result = _safe_update("accounts", payload, provider="wa", provider_user_id=clean_wa)
        if result.get("ok"):
            return {
                "ok": True,
                "channel_identity": {
                    "account_id": acct,
                    "channel_type": "whatsapp",
                    "provider_user_id": clean_wa,
                    "is_verified": True,
                    "metadata": {
                        "display_name": name,
                        "source": "accounts.auth_user_id_fallback",
                        "linked_at": _now_iso(),
                    },
                },
                "fallback": "accounts_auth_user_id",
                "wa_account_id": target_account_id,
                "previous_error": previous_error,
            }
        last_error = result.get("error")

    return {
        "ok": False,
        "error": "accounts_auth_user_id_link_failed",
        "write_error": last_error,
        "previous_error": previous_error,
    }


def _write_channel_identity_schema_safe(
    *,
    account_id: str,
    wa_id: str,
    profile_name: str = "",
    token_row: Optional[Dict[str, Any]] = None,
    wa_account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Link WhatsApp to the website account using service-role writes.

    Production finding after RLS hardening:
    - link_tokens lookup works.
    - public/anon channel_identities insert correctly returns 401.
    - accounts.auth_user_id fallback can conflict with unique constraints.

    Therefore, the correct durable store is channel_identities written through a
    service-role/admin Supabase client. accounts.auth_user_id is no longer the
    primary path; it is only a non-blocking mirror attempt.
    """
    acct = _clean(account_id)
    clean_wa = _normalize_phone(wa_id)
    name = _clean(profile_name) or _display_phone(clean_wa)
    referral_code = _clean((token_row or {}).get("referral_code")) or None
    guest_session_id = _clean((token_row or {}).get("guest_session_id")) or None

    if not acct or not clean_wa:
        return {"ok": False, "error": "account_id_or_wa_id_missing"}

    now_iso = _now_iso()
    metadata = {
        "display_name": name,
        "verified_via_link_code": True,
        "verified_at": now_iso,
        "created_from": "whatsapp_link_code_v22_service_role_channel_identity",
    }

    # Prefer existing row for this WhatsApp number so the same phone is not
    # linked to multiple accounts.
    existing, existing_err = _query_one_admin(
        "channel_identities",
        "*",
        channel_type="whatsapp",
        provider_user_id=clean_wa,
    )

    # If the number row does not exist, also check whether the owner already has
    # a WhatsApp identity row and update it instead of creating duplicates.
    if not existing:
        existing_by_owner, _owner_err = _query_one_admin(
            "channel_identities",
            "*",
            account_id=acct,
            channel_type="whatsapp",
        )
        if existing_by_owner:
            existing = existing_by_owner

    payload: Dict[str, Any] = {
        "account_id": acct,
        "channel_type": "whatsapp",
        "provider_user_id": clean_wa,
        "is_verified": True,
        "linked_at": now_iso,
        "last_seen_at": now_iso,
        "metadata": {**((existing or {}).get("metadata") or {}), **metadata} if isinstance((existing or {}).get("metadata"), dict) else metadata,
    }

    if referral_code:
        payload["referral_code"] = referral_code
        payload["referral_locked"] = True
    if guest_session_id:
        payload["guest_session_id"] = guest_session_id

    if existing and existing.get("id"):
        updated = _safe_update_admin("channel_identities", payload, id=existing.get("id"))
        if updated.get("ok"):
            rows = updated.get("data") or []
            return {
                "ok": True,
                "channel_identity": rows[0] if isinstance(rows, list) and rows else {**existing, **payload},
                "link_store": "channel_identities_service_role_update",
                "service_role_write": True,
                "previous_lookup_error": existing_err,
            }
        # If update failed because a row has conflicting unique fields, try an
        # insert only as a secondary path.
        update_error = updated.get("error")
    else:
        update_error = None

    insert_payload = {**payload, "first_seen_at": now_iso}
    inserted = _safe_insert_admin("channel_identities", insert_payload)
    if inserted.get("ok"):
        rows = inserted.get("data") or []
        return {
            "ok": True,
            "channel_identity": rows[0] if isinstance(rows, list) and rows else insert_payload,
            "link_store": "channel_identities_service_role_insert",
            "service_role_write": True,
            "previous_update_error": update_error,
            "previous_lookup_error": existing_err,
        }

    # Last resort: try account fallback, but do not rely on it first because
    # auth_user_id can be uniquely constrained on the web account row.
    fallback = _link_wa_account_to_owner_fallback(
        account_id=acct,
        wa_id=clean_wa,
        profile_name=name,
        wa_account_id=wa_account_id,
        previous_error={
            "channel_identity_insert_error": inserted.get("error"),
            "channel_identity_update_error": update_error,
            "lookup_error": existing_err,
        },
    )
    if fallback.get("ok"):
        fallback["link_store"] = "accounts_auth_user_id_last_resort"
        return fallback

    return {
        "ok": False,
        "error": "channel_identity_service_role_write_failed",
        "channel_identity_insert_error": inserted.get("error"),
        "channel_identity_update_error": update_error,
        "lookup_error": existing_err,
        "accounts_fallback_error": fallback,
    }


def _try_link_code(wa_id: str, text: str, profile_name: str = "") -> Optional[str]:
    code = _clean(text).upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{5,12}", code):
        return None

    token_tables = ("channel_link_tokens", "link_tokens", "channel_link_codes")
    token_row = None
    token_table = ""

    for table in token_tables:
        for provider_value in ("wa", "whatsapp"):
            try:
                q = _sb().table(table).select("*").eq("code", code)
                try:
                    q = q.eq("provider", provider_value)
                except Exception:
                    pass
                res = q.limit(1).execute()
                rows = getattr(res, "data", None) or []
                if rows and isinstance(rows[0], dict):
                    token_row = rows[0]
                    token_table = table
                    break
            except Exception:
                continue
        if token_row:
            break

    if not token_row:
        return None

    # Link tokens created by the web Channels page may store ownership under
    # different column names depending on which migration created the row.
    # The current production link_tokens table uses auth_user_id.
    account_id = _clean(
        token_row.get("account_id")
        or token_row.get("owner_account_id")
        or token_row.get("auth_user_id")
        or token_row.get("app_user_id")
        or token_row.get("user_account_id")
        or token_row.get("user_id")
    )
    if not account_id:
        return "The link code was found, but it is missing account ownership. Please generate a new code from the website."

    # Reject already-used/expired tokens where the table exposes those fields.
    if token_row.get("used") is True or _clean(token_row.get("used_at")) or _lower(token_row.get("status")) in {"used", "expired"}:
        return "This link code has already been used or expired. Please generate a new code from the website."

    expires_at_raw = _clean(token_row.get("expires_at"))
    if expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                return "This link code has expired. Please generate a new code from the website."
        except Exception:
            # Do not block linking only because an old row has a non-standard date format.
            pass

    wa_id = _normalize_phone(wa_id)
    account_row, _debug = _create_or_update_wa_account(wa_id, profile_name=profile_name)
    wa_account_id = _account_id_from_row(account_row)

    identity_result = _write_channel_identity_schema_safe(
        account_id=account_id,
        wa_id=wa_id,
        profile_name=profile_name,
        token_row=token_row,
        wa_account_id=wa_account_id,
    )

    if not identity_result.get("ok"):
        return "I found your link code, but linking failed. Please contact support with this message: channel_identity_write_failed"

    _mark_link_token_used_schema_safe(
        token_table=token_table,
        token_row=token_row,
        wa_id=wa_id,
        wa_account_id=wa_account_id,
    )

    return "✅ WhatsApp linked successfully.\n\nYou can now ask Nigeria tax questions here. Reply 0 for main menu."


# =============================================================================
# Payment initialization
# =============================================================================

def _checkout_email(account: Optional[Dict[str, Any]], account_id: str) -> str:
    email = _account_email(account)
    if email and not _is_placeholder_email(email):
        return email
    return f"user_{account_id[:8]}@naijataxguides.com"


def _init_paystack_checkout(account: Optional[Dict[str, Any]], account_id: str, item: Dict[str, Any], payment_type: str, wa_id: str = "") -> Dict[str, Any]:
    if create_reference is None or initialize_transaction is None:
        return {
            "ok": False,
            "message": f"{item['name']} selected.\n\nPayment link could not be generated right now. Please try again shortly or contact support.",
            "error": "paystack_service_unavailable",
        }

    prefix = "NTG-WA-TOPUP" if payment_type == "topup" else "NTG-WA"
    reference = create_reference(prefix)
    amount_kobo = int(item["price"]) * 100

    metadata = {
        "account_id": account_id,
        "source": "whatsapp",
        "channel_type": "whatsapp",
        "wa_id": _normalize_phone(wa_id),
        "customer_wa_id": _normalize_phone(wa_id),
        "bot_wa_phone": _whatsapp_bot_phone(),
        "return_channel": "whatsapp",
        "type": "credit_topup" if payment_type == "topup" else "subscription",
    }

    if payment_type == "topup":
        metadata.update(
            {
                "purpose": "usage_topup",
                "topup_code": item["code"],
                "package_code": item["code"],
                "package_name": item["name"],
                "credits": item["credits"],
                "amount_ngn": item["price"],
            }
        )
        callback_url = _whatsapp_return_url(
            wa_id,
            f"Payment completed for {item['name']}. Reference: {reference}. Please send this message to confirm my updated credits.",
        )
    else:
        metadata.update(
            {
                "plan_code": item["plan_code"],
                "plan_name": item["name"],
                "amount_ngn": item["price"],
                "credits": item["credits"],
            }
        )
        callback_url = _whatsapp_return_url(
            wa_id,
            f"Payment completed for {item['name']}. Reference: {reference}. Please send this message to confirm my plan.",
        )

    try:
        result = initialize_transaction(
            email=_checkout_email(account, account_id),
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata=metadata,
        )
        auth_url = ((result or {}).get("data") or {}).get("authorization_url") or (result or {}).get("authorization_url")
        if not auth_url:
            return {"ok": False, "error": "paystack_authorization_url_missing", "paystack_response": result}

        return {"ok": True, "authorization_url": auth_url, "reference": reference}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {_clip(exc)}"}


# =============================================================================
# Guides + calculators
# =============================================================================

def _guide(action: str) -> str:
    guides = {
        "paye_guide": (
            "👥 *PAYE Filing Guide*\n\n"
            "1. Confirm employee gross pay, allowances, benefits, and approved deductions.\n"
            "2. Compute taxable income using the applicable PAYE rules.\n"
            "3. Deduct PAYE from payroll and remit to the relevant State Internal Revenue Service.\n"
            "4. Keep payroll schedules, payment receipts, pension/NHF records, and employee files.\n\n"
            "Try: C1 salary 250000 pension 8% nhf 2.5% monthly\n"
            "Reply F7 for filing checklist or D1 to start a deadline reminder."
        ),
        "vat_guide": (
            "🧾 *VAT Filing Guide*\n\n"
            "1. Confirm if your business and transaction are VATable.\n"
            "2. Record output VAT on taxable sales and input VAT on eligible purchases.\n"
            "3. File VAT returns and remit net VAT by the required deadline.\n"
            "4. Keep invoices, receipts, and payment records.\n\n"
            "Try: C3 1000000\n"
            "Reply F7 for filing checklist or D1 to start a deadline reminder."
        ),
        "cit_guide": (
            "🏢 *Company Income Tax Filing Guide*\n\n"
            "1. Prepare financial statements and supporting schedules.\n"
            "2. Compute taxable profit after allowable deductions and adjustments.\n"
            "3. Apply the correct CIT rate based on company size/turnover.\n"
            "4. File returns with FIRS and keep all supporting records.\n\n"
            "Try: C2 profit 5000000 revenue 30000000\n"
            "For final filing, confirm with a qualified accountant."
        ),
        "wht_guide": (
            "💼 *Withholding Tax Guide*\n\n"
            "1. Confirm if the transaction is subject to WHT.\n"
            "2. Apply the correct WHT rate for the transaction type.\n"
            "3. Deduct WHT at payment point.\n"
            "4. Remit to the relevant tax authority and issue credit notes where applicable.\n\n"
            "Try: C4 500000 5%"
        ),
        "deadlines": (
            "📅 *Tax Deadlines / Calendar*\n\n"
            "• PAYE: usually monthly remittance to the State IRS.\n"
            "• VAT: usually monthly filing/remittance.\n"
            "• WHT: remit according to the applicable authority timeline.\n"
            "• CIT: annual company filing after financial year-end.\n\n"
            "WhatsApp reminder commands:\n"
            "D1 - Create reminder 🔔\n"
            "D2 - View reminders 📋\n"
            "D3 - Delete reminder 🗑️\n\n"
            "Free users can view the calendar. Paid users can create custom reminders."
        ),
        "filing_checklist": (
            "✅ *Filing Checklist*\n\n"
            "• Taxpayer/company registration details\n"
            "• TIN / CAC details where applicable\n"
            "• Sales and expense records\n"
            "• Payroll/PAYE records\n"
            "• Pension/NHF/approved deduction records where applicable\n"
            "• VAT invoices\n"
            "• WHT receipts/credit notes\n"
            "• Bank statements and payment confirmations\n"
            "• Prior filings and assessment notices\n\n"
            "Reply F2, F3, F4, or F5 for a specific filing guide."
        ),
    }
    return guides.get(action) or _tools_menu()


def _keyword_number(text: str, keywords: List[str]) -> Optional[Tuple[float, bool]]:
    raw = _clean(text).replace(",", "")
    for keyword in keywords:
        pattern = re.compile(rf"\b{re.escape(keyword)}\b\s*(?:=|:)?\s*(?:₦\s*)?(\d+(?:\.\d+)?)\s*(%)?", re.I)
        match = pattern.search(raw)
        if match:
            try:
                value = float(match.group(1))
                is_percent = bool(match.group(2)) or (value <= 100 and keyword in {"pension", "voluntary pension", "voluntary_pension", "vpension", "nhf"})
                return value, is_percent
            except Exception:
                return None
    return None


def _annualize_monthly_value(value: float, is_monthly: bool) -> int:
    return int(round(value * 12 if is_monthly else value))


def _parse_payroll_deductions(text: str, annual_gross: int, is_monthly: bool) -> Dict[str, Any]:
    deductible_specs = [
        ("Pension", ["pension"]),
        ("Voluntary pension", ["voluntary pension", "voluntary_pension", "vpension"]),
        ("NHF", ["nhf", "national housing fund"]),
    ]
    net_only_specs = [
        ("HMO", ["hmo", "health"]),
        ("Loan", ["loan", "salary advance"]),
        ("Cooperative", ["cooperative", "coop"]),
        ("Union dues", ["union", "union dues"]),
        ("Other deduction", ["other", "other deduction", "deduction"]),
    ]

    lines: List[str] = []
    taxable_deductions = 0
    net_only_deductions = 0

    for label, keywords in deductible_specs:
        found = _keyword_number(text, keywords)
        if not found:
            continue
        value, is_percent = found
        annual_value = int(round(annual_gross * (value / 100))) if is_percent else _annualize_monthly_value(value, is_monthly)
        taxable_deductions += max(0, annual_value)
        suffix = f"{value:g}%" if is_percent else _money(int(round(value))) + (" monthly" if is_monthly else " yearly")
        lines.append(f"• {label}: {suffix} = {_money(annual_value)} yearly")

    for label, keywords in net_only_specs:
        found = _keyword_number(text, keywords)
        if not found:
            continue
        value, is_percent = found
        annual_value = int(round(annual_gross * (value / 100))) if is_percent else _annualize_monthly_value(value, is_monthly)
        net_only_deductions += max(0, annual_value)
        suffix = f"{value:g}%" if is_percent else _money(int(round(value))) + (" monthly" if is_monthly else " yearly")
        lines.append(f"• {label}: {suffix} = {_money(annual_value)} yearly")

    return {
        "taxable_deductions": taxable_deductions,
        "net_only_deductions": net_only_deductions,
        "total_deductions": taxable_deductions + net_only_deductions,
        "lines": lines,
    }


def _calculate_paye(text: str) -> str:
    amounts = _extract_amounts(text)
    norm = _normalize_text(text)

    if not amounts:
        return (
            "👥 *PAYE Calculator*\n\n"
            "Send salary like this:\n"
            "C1 250000 monthly\n"
            "or\n"
            "C1 3000000 yearly\n\n"
            "For company-specific payroll deductions, use:\n"
            "C1 salary 250000 pension 8% nhf 2.5% hmo 5000 loan 10000 monthly\n\n"
            "Supported deductions: pension, voluntary pension, NHF, HMO, loan, cooperative, union, other.\n"
            "This basic calculator is free. 🧮"
        )

    amount = amounts[0]
    is_monthly = "month" in norm or "monthly" in norm
    annual = amount * 12 if is_monthly else amount

    payroll = _parse_payroll_deductions(text, annual, is_monthly)

    relief = max(200000, int(annual * 0.01)) + int(annual * 0.20)
    taxable = max(0, annual - relief - int(payroll["taxable_deductions"]))

    bands = [
        (300000, 0.07),
        (300000, 0.11),
        (500000, 0.15),
        (500000, 0.19),
        (1600000, 0.21),
        (10**15, 0.24),
    ]
    remaining = taxable
    tax = 0.0
    for band, rate in bands:
        if remaining <= 0:
            break
        take = min(remaining, band)
        tax += take * rate
        remaining -= take

    monthly_tax = tax / 12
    monthly_gross = annual / 12
    monthly_all_deductions = int(payroll["total_deductions"]) / 12
    net_monthly = monthly_gross - monthly_tax - monthly_all_deductions

    deduction_section = ""
    if payroll["lines"]:
        deduction_section = (
            "\n🏢 Company payroll deductions used:\n"
            + "\n".join(payroll["lines"])
            + "\n"
        )

    return (
        "👥 *PAYE Calculator Result*\n\n"
        f"Gross annual income: {_money(annual)}\n"
        f"Estimated annual relief: {_money(relief)}\n"
        f"Tax-deductible payroll deductions: {_money(int(payroll['taxable_deductions']))}\n"
        f"Estimated taxable income: {_money(int(taxable))}\n"
        f"Estimated annual PAYE: {_money(int(round(tax)))}\n"
        f"Estimated monthly PAYE: {_money(int(round(monthly_tax)))}\n"
        f"Estimated monthly net after PAYE/deductions: {_money(int(round(net_monthly)))}\n"
        f"{deduction_section}\n"
        "⚠️ Note: This is an estimate. Nigerian payroll policies vary by employer. Confirm pension, NHF, allowances, benefits, voluntary deductions, and state-specific treatment before final filing."
    )


def _calculate_cit(text: str) -> str:
    amounts = _extract_amounts(text)
    if not amounts:
        return (
            "🏢 *CIT Calculator*\n\n"
            "Send taxable profit and turnover like this:\n"
            "C2 profit 5000000 revenue 30000000\n\n"
            "This basic calculator is free."
        )

    norm = _normalize_text(text)
    profit = amounts[0]
    revenue = amounts[1] if len(amounts) > 1 else 0

    if revenue and revenue <= 25000000:
        rate = 0.0
        category = "Small company"
    elif revenue and revenue <= 100000000:
        rate = 0.20
        category = "Medium company"
    else:
        rate = 0.30
        category = "Large/unspecified company"

    tax = profit * rate

    return (
        "🏢 *Company Income Tax Calculator Result*\n\n"
        f"Taxable profit used: {_money(profit)}\n"
        f"Turnover/revenue used: {_money(revenue) if revenue else 'Not provided'}\n"
        f"Category: {category}\n"
        f"Estimated CIT rate: {int(rate * 100)}%\n"
        f"Estimated CIT: {_money(int(round(tax)))}\n\n"
        "Note: This is a basic estimate. Final CIT depends on allowable deductions, exemptions, and current FIRS rules."
    )


def _calculate_vat(text: str) -> str:
    amounts = _extract_amounts(text)
    if not amounts:
        return "🧾 *VAT Calculator*\n\nSend taxable sales amount like this:\nC3 1000000\n\nThis basic calculator is free. 🧮"

    amount = amounts[0]
    vat = amount * 0.075
    total = amount + vat

    return (
        "🧾 *VAT Calculator Result*\n\n"
        f"Taxable amount: {_money(amount)}\n"
        "VAT rate used: 7.5%\n"
        f"VAT amount: {_money(int(round(vat)))}\n"
        f"Total including VAT: {_money(int(round(total)))}\n\n"
        "Note: Confirm that the transaction is VATable before charging VAT."
    )


def _calculate_wht(text: str) -> str:
    amounts = _extract_amounts(text)
    rate_match = re.search(r"(\d+(?:\.\d+)?)\s*%", _clean(text))
    rate = float(rate_match.group(1)) if rate_match else None

    if not amounts or rate is None:
        return (
            "💼 *WHT Calculator*\n\n"
            "Send amount and WHT rate like this:\n"
            "C4 500000 5%\n\n"
            "WHT rates vary by transaction type, so include the rate."
        )

    amount = amounts[0]
    wht = amount * (rate / 100)

    return (
        "💼 *Withholding Tax Calculator Result*\n\n"
        f"Transaction amount: {_money(amount)}\n"
        f"WHT rate used: {rate:g}%\n"
        f"WHT to deduct: {_money(int(round(wht)))}\n"
        f"Net payment after WHT: {_money(int(round(amount - wht)))}\n\n"
        "Note: Confirm the correct WHT rate for the transaction type."
    )


def _salary_compare(text: str) -> str:
    amounts = _extract_amounts(text)
    if len(amounts) < 2:
        return (
            "📊 *Salary Comparison*\n\n"
            "Send two salary amounts like this:\n"
            "C5 250000 350000 monthly\n\n"
            "The app will estimate the net difference."
        )

    first = _calculate_paye(f"C1 {amounts[0]} monthly")
    second = _calculate_paye(f"C1 {amounts[1]} monthly")
    return (
        "📊 *Salary Comparison*\n\n"
        "First salary estimate:\n"
        f"{first}\n\n"
        "Second salary estimate:\n"
        f"{second}"
    )


def _quiz_text() -> str:
    return (
        "🧠 *Tax Quiz Centre*\n\n"
        "Q1 - Start mixed quiz\n"
        "Q1 PAYE - Start PAYE quiz\n"
        "Q1 VAT - Start VAT quiz\n"
        "Q1 CIT - Start Company Tax quiz\n"
        "Q1 WHT - Start WHT quiz\n"
        "Q2 - Choose category\n"
        "Q3 - Today's score\n"
        "Q4 - Review last answer\n"
        "Q5 - Short paid AI explanation for last answer 💎\n\n"
        f"Free users: {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily.\n"
        "Paid users: unlimited non-AI quiz attempts.\n"
        "Q5 costs 1 Usage Credit and returns a short AI explanation only."
    )


def _quiz_rules_text() -> str:
    return (
        "🧠 *Quiz Rules*\n\n"
        "✅ Q1 non-AI quiz questions do not consume Usage Credits.\n"
        f"✅ Free users get {QUIZ_FREE_DAILY_LIMIT} attempts daily.\n"
        "✅ Paid users get unlimited non-AI quiz attempts.\n"
        "✅ Reply A, B, C, or D to answer.\n"
        "✅ Reply Q2 to choose a category.\n"
        "💎 Q5 costs 1 Usage Credit and returns a short AI explanation only.\n\n"
        "Reply Q1 to start or 0 for main menu."
    )


def _quiz_categories() -> List[str]:
    preferred = ["PAYE", "VAT", "Company Tax", "WHT", "Deadlines", "Records", "General"]
    available = {str(q.get("category") or "General") for q in QUIZ_BANK}
    ordered = [cat for cat in preferred if cat in available]
    ordered.extend(sorted(available.difference(set(ordered))))
    return ordered


def _quiz_category_menu() -> str:
    categories = _quiz_categories()
    lines = ["🧠 *Choose Quiz Category*", ""]
    for index, category in enumerate(categories, start=1):
        code = "CIT" if category == "Company Tax" else category.upper().replace(" ", "")
        lines.append(f"Q2 {index} - {category}  |  Q1 {code}")
    lines.extend([
        "",
        "Examples:",
        "Q1 PAYE",
        "Q1 VAT",
        "Q1 CIT",
        "Q1 WHT",
        "",
        "Reply 0 for main menu.",
    ])
    return "\n".join(lines)


def _resolve_quiz_category(text: str) -> str:
    norm = _normalize_text(text)
    categories = _quiz_categories()

    if norm in {"q1", "quiz", "start quiz", "tax quiz", "q2", "category", "quiz categories"}:
        return ""

    if re.search(r"\\b(paye|salary|personal income|pit)\\b", norm):
        return "PAYE"
    if re.search(r"\\bvat\\b", norm):
        return "VAT"
    if re.search(r"\\b(cit|company tax|company income tax|companies income tax|company)\\b", norm):
        return "Company Tax"
    if re.search(r"\\b(wht|withholding|withholding tax)\\b", norm):
        return "WHT"
    if re.search(r"\\b(deadline|deadlines|calendar|reminder)\\b", norm):
        return "Deadlines"
    if re.search(r"\\b(record|records|receipt|invoice|payroll)\\b", norm):
        return "Records"
    if re.search(r"\\b(general|mixed|all)\\b", norm):
        return ""

    m = re.search(r"q[12]\\s+(\\d+)", norm)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(categories):
            return categories[idx]

    for cat in categories:
        if _normalize_text(cat) in norm:
            return cat

    return ""


def _quiz_attempt_info(state: Dict[str, Any]) -> Tuple[str, int]:
    today = datetime.now(timezone.utc).date().isoformat()
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    if data.get("quiz_date") == today:
        try:
            return today, int(data.get("quiz_attempts") or 0)
        except Exception:
            return today, 0
    return today, 0


def _quiz_daily_numbers(data: Dict[str, Any]) -> Dict[str, int]:
    today = datetime.now(timezone.utc).date().isoformat()
    if data.get("quiz_date") != today:
        return {"attempts": 0, "correct": 0, "wrong": 0}
    return {
        "attempts": int(data.get("quiz_attempts") or 0),
        "correct": int(data.get("quiz_correct_count") or 0),
        "wrong": int(data.get("quiz_wrong_count") or 0),
    }


def _select_quiz_question(pool: List[Dict[str, Any]], data: Dict[str, Any], category: str, attempts: int) -> Dict[str, Any]:
    if not pool:
        pool = QUIZ_BANK

    seen_key = "quiz_seen_ids_" + (_normalize_text(category or "mixed").replace(" ", "_") or "mixed")
    seen = data.get(seen_key)
    if not isinstance(seen, list):
        seen = []

    available = [q for q in pool if q.get("id") not in set(seen)]
    if not available:
        seen = []
        available = pool

    index = attempts % len(available)
    quiz = available[index]
    seen.append(quiz.get("id"))
    data[seen_key] = seen[-50:]
    return quiz


def _start_quiz(wa_id: str, account_id: str, state: Optional[Dict[str, Any]] = None, category: str = "") -> Dict[str, Any]:
    state = state or _get_session_state(wa_id)
    today, attempts = _quiz_attempt_info(state)
    old_data = state.get("data") if isinstance(state.get("data"), dict) else {}

    if not _is_active_paid_subscription(account_id) and attempts >= QUIZ_FREE_DAILY_LIMIT:
        body = (
            "🔒 *Daily Quiz Limit Reached*\n\n"
            f"Free users can take {QUIZ_FREE_DAILY_LIMIT} non-AI quiz attempts daily.\n"
            "Paid users get unlimited non-AI quiz attempts.\n\n"
            "Reply 4 to view plans, Q3 to see score, or 0 for main menu."
        )
        return {"ok": True, "handled": "quiz_limit", "send_result": _send_whatsapp_text(wa_id, body)}

    category = _resolve_quiz_category(category or "")
    pool = [q for q in QUIZ_BANK if not category or _normalize_text(q.get("category")) == _normalize_text(category)]
    if not pool:
        pool = QUIZ_BANK
        category = ""

    working_data = dict(old_data)
    quiz = _select_quiz_question(pool, working_data, category, attempts)

    data = {
        **working_data,
        "quiz_date": today,
        "quiz_attempts": attempts,
        "quiz_id": quiz["id"],
        "quiz_category": quiz.get("category"),
        "quiz_question": quiz.get("question"),
        "quiz_options": quiz.get("options"),
        "correct": quiz["answer"],
        "explain": quiz["explain"],
        "active_quiz_started_at": _now_iso(),
    }
    _set_session_state(wa_id, "quiz_answer", "tax_quiz", data)

    options = "\n".join([f"{key}. {value}" for key, value in quiz["options"].items()])
    remaining = "Unlimited" if _is_active_paid_subscription(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - attempts))
    body = (
        f"🧠 *Tax Quiz* ({quiz['category']})\n\n"
        f"Question {attempts + 1}: {quiz['question']}\n\n"
        f"{options}\n\n"
        f"Remaining today: {remaining}\n\n"
        "Reply A, B, C, or D.\n"
        "Reply CANCEL to stop."
    )
    return {"ok": True, "handled": "quiz_start", "send_result": _send_whatsapp_text(wa_id, body)}


def _handle_quiz_answer(wa_id: str, account_id: str, text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    answer = _normalize_text(text).upper()
    if answer in {"CANCEL", "STOP", "END"}:
        _set_session_state(wa_id, "main", "", state.get("data") if isinstance(state.get("data"), dict) else {})
        return {"ok": True, "handled": "quiz_cancelled", "send_result": _send_whatsapp_text(wa_id, "Quiz cancelled.\n\nReply Q1 to start again or 0 for menu.")}

    if answer not in {"A", "B", "C", "D"}:
        return {
            "ok": True,
            "handled": "quiz_invalid_answer",
            "send_result": _send_whatsapp_text(wa_id, "Please reply with A, B, C, or D.\n\nReply CANCEL to stop the quiz."),
        }

    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    correct = _clean(data.get("correct")).upper()
    if correct not in {"A", "B", "C", "D"}:
        correct = "A"

    today, attempts = _quiz_attempt_info(state)
    attempts += 1

    passed = answer == correct
    correct_count = int(data.get("quiz_correct_count") or 0) + (1 if passed else 0)
    wrong_count = int(data.get("quiz_wrong_count") or 0) + (0 if passed else 1)
    verdict = "✅ Correct!" if passed else f"❌ Not correct. Correct answer: {correct}."
    explain = _clean(data.get("explain"))

    last_quiz = {
        "id": data.get("quiz_id"),
        "category": data.get("quiz_category"),
        "question": data.get("quiz_question"),
        "options": data.get("quiz_options"),
        "selected": answer,
        "correct": correct,
        "is_correct": passed,
        "explanation": explain,
        "answered_at": _now_iso(),
    }

    new_data = {
        **data,
        "quiz_date": today,
        "quiz_attempts": attempts,
        "quiz_correct_count": correct_count,
        "quiz_wrong_count": wrong_count,
        "last_quiz": last_quiz,
        "last_quiz_answer": answer,
        "last_quiz_correct": correct,
        "last_quiz_passed": passed,
        "last_quiz_explain": explain,
    }
    _set_session_state(wa_id, "main", "", new_data)

    remaining = "Unlimited" if _is_active_paid_subscription(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - attempts))
    body = (
        f"🧠 *Quiz Result*\n\n"
        f"{verdict}\n\n"
        f"Explanation: {explain}\n\n"
        f"Attempts today: {attempts}\n"
        f"Correct today: {correct_count}\n"
        f"Wrong today: {wrong_count}\n"
        f"Remaining today: {remaining}\n\n"
        "Reply Q1 for another quiz, Q2 for categories, Q3 for score, Q4 to review, Q5 for AI explanation, or 0 for menu."
    )
    return {"ok": True, "handled": "quiz_answer", "send_result": _send_whatsapp_text(wa_id, body)}


def _handle_quiz_command(wa_id: str, account_id: str, text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    norm = _normalize_text(text)

    if norm.startswith("q1") or norm in {"quiz", "start quiz", "tax quiz", "quiz me", "take quiz"}:
        return _start_quiz(wa_id, account_id, state, _resolve_quiz_category(text))

    if norm.startswith("q2") or "category" in norm:
        category = _resolve_quiz_category(text)
        if category:
            return _start_quiz(wa_id, account_id, state, category)
        return {"ok": True, "handled": "quiz_categories", "send_result": _send_whatsapp_text(wa_id, _quiz_category_menu())}

    if norm.startswith("q3") or "score" in norm:
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        numbers = _quiz_daily_numbers(data)
        remaining = "Unlimited" if _is_active_paid_subscription(account_id) else str(max(0, QUIZ_FREE_DAILY_LIMIT - numbers["attempts"]))
        accuracy = "0%" if numbers["attempts"] <= 0 else f"{round((numbers['correct'] / numbers['attempts']) * 100)}%"
        body = (
            "📊 *Today's Quiz Score*\n\n"
            f"Attempts: {numbers['attempts']}\n"
            f"Correct: {numbers['correct']}\n"
            f"Wrong: {numbers['wrong']}\n"
            f"Accuracy: {accuracy}\n"
            f"Remaining: {remaining}\n\n"
            "Reply Q1 to continue, Q2 to choose category, or 0 for menu."
        )
        return {"ok": True, "handled": "quiz_score", "send_result": _send_whatsapp_text(wa_id, body)}

    if norm.startswith("q4") or "review" in norm:
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        last = data.get("last_quiz") if isinstance(data.get("last_quiz"), dict) else None
        if not last:
            return {"ok": True, "handled": "quiz_review_empty", "send_result": _send_whatsapp_text(wa_id, "📌 No quiz answer to review yet. Reply Q1 to start a quiz.")}
        status = "✅ Correct" if last.get("is_correct") else "❌ Not correct"
        options = last.get("options") if isinstance(last.get("options"), dict) else {}
        selected_text = options.get(last.get("selected"), "")
        correct_text = options.get(last.get("correct"), "")
        body = (
            "📌 *Last Quiz Review*\n\n"
            f"Category: {_clean(last.get('category')) or 'General'}\n"
            f"Question: {_clean(last.get('question'))}\n\n"
            f"Your answer: {_clean(last.get('selected'))}. {_clean(selected_text)}\n"
            f"Correct answer: {_clean(last.get('correct'))}. {_clean(correct_text)}\n"
            f"Status: {status}\n\n"
            f"Why: {_clean(last.get('explanation'))}\n\n"
            "Reply Q1 for another quiz or Q5 for AI explanation."
        )
        return {"ok": True, "handled": "quiz_review", "send_result": _send_whatsapp_text(wa_id, _clip(body, 3900))}

    if norm.startswith("q5") or "explain" in norm:
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        last = data.get("last_quiz") if isinstance(data.get("last_quiz"), dict) else None
        question = _clean((last or {}).get("question") or data.get("quiz_question"))
        correct = _clean((last or {}).get("correct") or data.get("last_quiz_correct"))
        explanation = _clean((last or {}).get("explanation") or data.get("last_quiz_explain"))

        if not question:
            return {
                "ok": True,
                "handled": "quiz_ai_no_context",
                "send_result": _send_whatsapp_text(wa_id, "No last quiz question found yet. Reply Q1 to start a quiz first."),
            }

        # v17: Q5 is never free. Block free/no-plan users before any OpenAI call.
        if not _is_active_paid_subscription(account_id):
            body = (
                "🔒 *Q5 AI Explanation is a paid feature*\n\n"
                "Q1–Q4 remain non-AI according to your plan limits.\n"
                "Q5 costs 1 Usage Credit because it calls the AI explanation engine.\n\n"
                "Reply 4 to view plans or Q4 to review the normal non-AI explanation."
            )
            return {"ok": True, "handled": "quiz_ai_paid_required", "send_result": _send_whatsapp_text(wa_id, body)}

        # v17: pre-debit the WhatsApp-visible balance before calling OpenAI.
        debit = _debit_q5_usage_credit(account_id)
        if not debit.get("ok"):
            body = (
                "🔒 *Q5 AI Explanation not available*\n\n"
                "Q5 costs 1 Usage Credit, but your credit balance could not be charged.\n"
                "No AI explanation was generated.\n\n"
                "Reply 3 to check your plan/credits, 4 to view plans, or Q4 for the normal non-AI review."
            )
            return {
                "ok": True,
                "handled": "quiz_ai_debit_failed",
                "send_result": _send_whatsapp_text(wa_id, body),
                "debit_error": debit if _debug_enabled() else None,
            }

        result = ask_guarded({
            "account_id": account_id,
            "question": (
                "Give a very short Nigerian tax quiz explanation in 2 to 4 simple bullet points only. "
                "Maximum 90 words. Do not add long disclaimers. "
                f"Question: {question}. Correct option: {correct}. Base explanation: {explanation}."
            ),
            "lang": "en",
            "channel": "whatsapp",
            "provider": "wa",
            "provider_user_id": wa_id,
            "action_code": "quiz_ai_explanation_q5_manual_credit",
            "max_words": 90,
            "max_output_tokens": 180,
        })

        answer = _clean(result.get("answer") or result.get("message") or "")
        result_ok = bool(isinstance(result, dict) and (result.get("ok") is True or answer))

        if not result_ok:
            _refund_q5_usage_credit(account_id, debit)
            body = (
                "⚠️ *Q5 AI Explanation failed*\n\n"
                "The AI explanation could not be generated, so the Usage Credit was returned.\n\n"
                "Reply Q5 to try again, Q4 for normal review, or 0 for menu."
            )
            return {"ok": True, "handled": "quiz_ai_failed_refunded", "send_result": _send_whatsapp_text(wa_id, body)}

        answer = _clip(answer, 650)
        body = (
            "💡 *Q5 Short AI Explanation*\n\n"
            f"{answer}\n\n"
            f"💎 Usage Credit deducted: 1\n"
            f"Balance: {debit.get('after')}\n\n"
            "Reply Q1 for another quiz, Q3 for score, or 0 for menu."
        )
        return {
            "ok": True,
            "handled": "quiz_ai_explanation_paid_short_manual_debit",
            "send_result": _send_whatsapp_text(wa_id, _clip(body, 1300)),
            "credits_consumed": 1,
            "credits_left": debit.get("after"),
        }

    return {"ok": True, "handled": "quiz_menu", "send_result": _send_whatsapp_text(wa_id, _quiz_text())}


def _deadline_menu(account_id: str) -> str:
    if not _is_active_paid_subscription(account_id):
        return (
            "📅 *Tax Deadline Reminders*\n\n"
            "Free users can view the general tax calendar. Custom reminders are available on paid plans.\n\n"
            "D1 - Create reminder 🔔 (paid)\n"
            "D2 - View reminders 📋\n"
            "D3 - Delete reminder 🗑️\n"
            "D4 - Reminder settings ⚙️\n\n"
            "Reply 4 to view plans or 0 for main menu."
        )
    return (
        "📅 *Tax Deadline Reminders*\n\n"
        "D1 - Create reminder 🔔\n"
        "D2 - View reminders 📋\n"
        "D3 - Delete reminder 🗑️\n"
        "D4 - Reminder settings ⚙️\n\n"
        "Example: D1 PAYE 2026-05-29 7\n"
        "This means PAYE due date is 2026-05-29 and reminder is 7 days before."
    )


def _parse_deadline_create(text: str) -> Optional[Dict[str, Any]]:
    raw = _clean(text).upper()
    # D1 PAYE 2026-05-29 7 09:00 whatsapp
    m = re.search(r"\bD1\s+(PAYE|VAT|CIT|WHT)\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,3}))?(?:\s+(\d{1,2}:\d{2}))?(?:\s+(WHATSAPP|EMAIL|SMS|WHATSAPP,EMAIL|WHATSAPP,SMS|EMAIL,SMS|WHATSAPP,EMAIL,SMS))?", raw)
    if not m:
        return None
    tax_type = m.group(1)
    due_date = m.group(2)
    reminder_days = int(m.group(3) or 7)
    reminder_time = _valid_reminder_time_v14(m.group(4) or "09:00")
    reminder_mode = _valid_reminder_mode_v14(m.group(5) or "whatsapp")
    try:
        datetime.strptime(due_date, "%Y-%m-%d")
    except Exception:
        return None
    return {
        "tax_type": tax_type,
        "due_date": due_date,
        "reminder_days_before": reminder_days,
        "reminder_time": reminder_time,
        "timezone": "Africa/Lagos",
        "reminder_mode": reminder_mode,
    }


def _deadline_table_payload(account_id: str, wa_id: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    tax_type = parsed["tax_type"]
    due_date = parsed["due_date"]
    reminder_days = int(parsed.get("reminder_days_before") or 7)
    validation = _deadline_validation_v13(due_date, reminder_days)
    payload = {
        "user_id": account_id,
        "account_id": account_id,
        "tax_type": tax_type,
        "due_date": due_date,
        "reminder_days_before": reminder_days,
        "enabled": bool(validation.get("ok")),
        "updated_at": _now_iso(),
    }
    payload.update(_deadline_optional_payload_v14(parsed, wa_id))
    return payload


def _create_deadline_reminder(wa_id: str, account_id: str, text: str) -> str:
    parsed = _parse_deadline_create(text)
    if not parsed:
        return (
            "🔔 *Create Deadline Reminder*\n\n"
            "Send it like this:\n"
            "D1 PAYE 2026-05-29 7 09:00 whatsapp\n\n"
            "Format: D1 tax_type due_date reminder_days_before time mode\n"
            "Modes: whatsapp, email, sms, whatsapp,email\n"
            "Supported types: PAYE, VAT, CIT, WHT."
        )
    validation = _deadline_validation_v13(parsed["due_date"], parsed.get("reminder_days_before", 7))
    if not validation.get("ok"):
        max_days = validation.get("max_days", 0)
        return (
            "⚠️ *Reminder Not Created*\n\n"
            f"{validation.get('message')}\n\n"
            f"Try: D1 {parsed['tax_type']} {parsed['due_date']} {max_days} {_valid_reminder_time_v14(parsed.get('reminder_time'))} {_valid_reminder_mode_v14(parsed.get('reminder_mode'))}\n"
            "Or choose a later due date."
        )
    payload = _deadline_table_payload(account_id, wa_id, parsed)
    result = _safe_insert("tax_deadlines", payload)
    # If DB does not yet have v14 optional columns, retry with the stable base schema.
    if not result.get("ok"):
        base_payload = {k: v for k, v in payload.items() if k in {"user_id", "account_id", "tax_type", "due_date", "reminder_days_before", "enabled", "updated_at"}}
        result = _safe_insert("tax_deadlines", base_payload)
    if not result.get("ok"):
        return (
            "⚠️ Reminder saving failed. Please try again.\n\n"
            f"{parsed['tax_type']} due date: {parsed['due_date']}\n"
            f"Reminder: {parsed['reminder_days_before']} days before"
        )
    return (
        "✅ *Deadline Reminder Saved*\n\n"
        f"Tax type: {parsed['tax_type']}\n"
        f"Due date: {parsed['due_date']}\n"
        f"Reminder: {parsed['reminder_days_before']} days before\n"
        f"Time: {_valid_reminder_time_v14(parsed.get('reminder_time'))}\n"
        f"Mode: {_valid_reminder_mode_v14(parsed.get('reminder_mode'))}\n"
        f"Reminder date: {validation.get('reminder_date')}\n\n"
        "Reply D2 to view reminders or 0 for menu."
    )


def _view_deadline_reminders(account_id: str) -> str:
    rows, err = _query_many("tax_deadlines", "*", limit=10, account_id=account_id)
    if err:
        return "📋 Reminder viewing is not fully connected yet. Reply F6 for the general tax calendar or 0 for menu."
    if not rows:
        return "📋 No saved deadline reminders yet.\n\nCreate one like this:\nD1 PAYE 2026-05-29 7"
    lines = ["📋 *Your Deadline Reminders*", ""]
    for i, row in enumerate(rows, start=1):
        lines.append(_deadline_display_line(row, i))
    lines.extend(["", "To delete, use D3 plus the reminder number. Example: D3 1", "To update, use D4 plus number, days, time and mode. Example: D4 1 3 08:30 whatsapp"])
    return "\n".join(lines)


def _handle_deadline_command(wa_id: str, account_id: str, text: str) -> Dict[str, Any]:
    norm = _normalize_text(text)
    if norm.startswith("d1") or norm in {"create deadline", "deadline reminder", "create reminder"}:
        if not _is_active_paid_subscription(account_id):
            return {"ok": True, "handled": "deadline_paid_required", "send_result": _send_whatsapp_text(wa_id, _deadline_menu(account_id))}
        return {"ok": True, "handled": "deadline_create", "send_result": _send_whatsapp_text(wa_id, _create_deadline_reminder(wa_id, account_id, text))}
    if norm.startswith("d2") or norm in {"view deadlines", "view reminders"}:
        return {"ok": True, "handled": "deadline_view", "send_result": _send_whatsapp_text(wa_id, _view_deadline_reminders(account_id))}
    if norm.startswith("d3") or norm in {"delete deadline", "delete reminder"}:
        return _handle_deadline_delete_v10(wa_id, account_id, text)
    if norm.startswith("d4") or norm in {"reminder settings", "deadline settings"}:
        return _handle_deadline_settings_v10(wa_id, account_id, text)
    return {"ok": True, "handled": "deadline_menu", "send_result": _send_whatsapp_text(wa_id, _deadline_menu(account_id))}


def _handle_calculator_action(action: str, text: str) -> str:
    if action == "paye_calc":
        return _calculate_paye(text)
    if action == "cit_calc":
        return _calculate_cit(text)
    if action == "vat_calc":
        return _calculate_vat(text)
    if action == "wht_calc":
        return _calculate_wht(text)
    if action == "salary_compare":
        return _salary_compare(text)
    if action == "tax_quiz":
        return _quiz_text()
    if action == "deadlines":
        return _guide("deadlines")
    if action == "tools_menu":
        return _tools_menu()
    return _calc_menu()


# =============================================================================
# History
# =============================================================================

def _history_key(value: Any) -> str:
    text = _normalize_text(value)
    return re.sub(r"\s+", "_", text)[:180]


def _log_whatsapp_history(*, account_id: str, question: str, answer: str, result: Dict[str, Any]) -> Dict[str, Any]:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    try:
        credits_consumed = int(meta.get("credits_consumed") or 0)
    except Exception:
        credits_consumed = 0

    usage_charged = bool(meta.get("usage_charged") is True or credits_consumed > 0)
    from_cache = bool(result.get("ok") is True and (result.get("source") == "database" or result.get("mode") == "direct_cache"))
    now_iso = _now_iso()

    payloads = [
        {
            "account_id": account_id or None,
            "question": _clip(question, 5000),
            "answer": _clip(answer, 20000),
            "lang": "en",
            "source": "whatsapp",
            "from_cache": from_cache,
            "canonical_key": _history_key(question),
            "normalized_question": _normalize_text(question),
            "plan_code": _clean(meta.get("plan_code")) or None,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "whatsapp",
            "created_at": now_iso,
            "updated_at": now_iso,
        },
        {
            "account_id": account_id or None,
            "question": _clip(question, 5000),
            "answer": _clip(answer, 20000),
            "lang": "en",
            "source": "whatsapp",
            "from_cache": from_cache,
            "credits_consumed": credits_consumed,
            "usage_charged": usage_charged,
            "channel": "whatsapp",
            "created_at": now_iso,
        },
        {"question": _clip(question, 5000), "answer": _clip(answer, 20000), "created_at": now_iso},
    ]

    errors: List[str] = []
    for idx, payload in enumerate(payloads):
        inserted = _safe_insert("qa_history", payload)
        if inserted.get("ok"):
            return {"ok": True, "mode": f"whatsapp_direct_history_{idx}"}
        errors.append(str(inserted.get("error")))

    return {"ok": False, "error": "whatsapp_history_insert_failed", "errors": errors[:3]}




def _history_date_label(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return "date not shown"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return raw[:19]


def _history_excerpt(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", _clean(value))
    if not text:
        return "Not shown"
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _history_rows(account_id: str, limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Read the user's Q&A history from the confirmed qa_history table.

    Confirmed columns include:
    account_id, question, answer, source, provider, created_at,
    credits_consumed, usage_charged, and channel.
    """
    return _query_many(
        "qa_history",
        "id,question,answer,source,provider,channel,created_at,credits_consumed,usage_charged",
        limit=max(1, min(limit, 10)),
        order_col="created_at",
        desc=True,
        account_id=account_id,
    )


def _history_recent_text(account_id: str, limit: int = 5) -> str:
    rows, err = _history_rows(account_id, limit=limit)
    if err:
        return (
            "⚠️ I could not load your tax history right now.\n\n"
            "Please try again shortly or use the History page on the website.\n\n"
            "Reply 0 for main menu."
        )

    if not rows:
        return (
            "🕘 *Recent Tax History*\n\n"
            "No tax history found yet.\n\n"
            "Ask a tax question here or on the website, then reply H1 again."
        )

    lines = ["🕘 *Recent Tax History*", ""]
    for index, row in enumerate(rows, start=1):
        question = _history_excerpt(row.get("question"), 110)
        source = _clean(row.get("channel") or row.get("provider") or row.get("source") or "app")
        created = _history_date_label(row.get("created_at"))
        try:
            credits = int(row.get("credits_consumed") or 0)
        except Exception:
            credits = 0
        credit_text = f" | credits: {credits}" if credits else ""
        lines.append(f"{index}. {question}")
        lines.append(f"   {created} | {source}{credit_text}")

    lines.extend(["", "Reply H2 to view your last tax answer, or 0 for main menu."])
    return _clip("\n".join(lines), 3900)


def _history_last_answer_text(account_id: str) -> str:
    rows, err = _history_rows(account_id, limit=1)
    if err:
        return (
            "⚠️ I could not load your last tax answer right now.\n\n"
            "Please try again shortly or use the History page on the website.\n\n"
            "Reply 0 for main menu."
        )

    if not rows:
        return (
            "📌 *Last Tax Answer*\n\n"
            "No saved tax answer found yet.\n\n"
            "Ask a tax question first, then reply H2 again."
        )

    row = rows[0]
    question = _history_excerpt(row.get("question"), 500)
    answer = _history_excerpt(row.get("answer"), 2500)
    created = _history_date_label(row.get("created_at"))
    source = _clean(row.get("channel") or row.get("provider") or row.get("source") or "app")
    try:
        credits = int(row.get("credits_consumed") or 0)
    except Exception:
        credits = 0

    credit_line = f"\nCredits used: {credits}" if credits else "\nCredits used: 0 or not charged"
    body = (
        "📌 *Last Tax Answer*\n\n"
        f"Date: {created}\n"
        f"Source: {source}"
        f"{credit_line}\n\n"
        f"Question:\n{question}\n\n"
        f"Answer:\n{answer}\n\n"
        "Reply H1 for recent history or 0 for main menu."
    )
    return _clip(body, 3900)


def _handle_history_command(wa_id: str, account_id: str, text: str) -> Dict[str, Any]:
    norm = _normalize_text(text)
    if norm in {"h2", "last answer", "last tax answer", "last history", "latest answer", "latest tax answer"}:
        return {
            "ok": True,
            "handled": "history_last_answer",
            "send_result": _send_whatsapp_text(wa_id, _history_last_answer_text(account_id)),
        }

    return {
        "ok": True,
        "handled": "history_recent",
        "send_result": _send_whatsapp_text(wa_id, _history_recent_text(account_id, limit=5)),
    }


# =============================================================================
# WhatsApp Support helpers
# =============================================================================

def _support_to_email() -> str:
    return (
        _clean(os.getenv("SUPPORT_TO_EMAIL"))
        or _clean(os.getenv("SUPPORT_EMAIL"))
        or _clean(os.getenv("MAIL_FROM_EMAIL"))
        or _clean(os.getenv("SMTP_FROM"))
        or _clean(os.getenv("MAIL_USER"))
        or _clean(os.getenv("SMTP_USER"))
        or "support@naijataxguides.com"
    )


def _support_ticket_id() -> str:
    return f"NTG-WA-{str(uuid.uuid4()).split('-')[0].upper()}"


def _support_category_from_text(value: Any) -> str:
    text = _normalize_text(value)
    if any(word in text for word in ("pay", "paid", "payment", "paystack", "subscription", "subscribe", "billing", "plan", "renew")):
        return "billing"
    if any(word in text for word in ("credit", "credits", "balance", "deduct", "deducted", "topup", "top up", "usage")):
        return "credits"
    if any(word in text for word in ("link", "unlink", "whatsapp", "telegram", "channel", "connect")):
        return "channels"
    if any(word in text for word in ("login", "otp", "password", "account", "cookie", "session")):
        return "login"
    if any(word in text for word in ("bug", "error", "not working", "failed", "technical", "server")):
        return "technical"
    return "general"


def _support_priority_from_text(value: Any) -> str:
    text = _normalize_text(value)
    if any(word in text for word in ("urgent", "emergency", "critical", "immediately")):
        return "urgent"
    if any(word in text for word in ("failed", "cannot", "can't", "blocked", "stuck", "payment")):
        return "high"
    return "normal"


def _support_subject_from_message(value: Any) -> str:
    text = " ".join(_clean(value).split())
    if not text:
        return "WhatsApp support request"
    # Remove the command prefix if the user wrote: SUP1 my issue...
    text = re.sub(r"^SUP1\b[:\-\s]*", "", text, flags=re.I).strip()
    if not text:
        return "WhatsApp support request"
    first_sentence = re.split(r"[\n\r.!?]", text, maxsplit=1)[0].strip()
    subject = first_sentence or text
    return subject[:120] if len(subject) > 120 else subject


def _support_message_from_command(text: Any) -> str:
    raw = _clean(text)
    return re.sub(r"^SUP1\b[:\-\s]*", "", raw, flags=re.I).strip()


def _support_menu() -> str:
    return (
        "🛟 *Support Centre*\n\n"
        "SUP1 - Create support ticket\n"
        "SUP2 - View my support tickets\n"
        "SUP3 - View latest ticket\n"
        "SUP4 - Reply to latest/open ticket\n"
        "SUP5 - Close latest/open ticket\n"
        "SUP6 - Contact support email\n\n"
        "Quick examples:\n"
        "SUP1 I paid but my plan has not updated. Reference NTG-...\n"
        "SUP4 Please note that my Paystack reference is NTG-...\n"
        "SUP5\n\n"
        "Reply 0 for main menu."
    )


def _support_contact_text() -> str:
    email = _support_to_email()
    return (
        "📧 *Contact Support*\n\n"
        f"Email: {email}\n\n"
        "For faster help, include your phone number, payment reference if any, and a short description of the issue.\n\n"
        "You can also reply SUP1 here to create a support ticket directly from WhatsApp."
    )


def _support_ticket_line(ticket: Dict[str, Any], index: int) -> str:
    ticket_id = _clean(ticket.get("ticket_id") or ticket.get("id") or "Ticket")
    status = _clean(ticket.get("status") or "open")
    category = _clean(ticket.get("category") or ticket.get("issue_type") or "general")
    subject = _clip(_clean(ticket.get("subject") or ticket.get("last_message_preview") or "Support request"), 80)
    created = _history_date_label(ticket.get("created_at"))
    return f"{index}. {ticket_id} - {status}\n   {category} | {created}\n   {subject}"


def _support_tickets_for_account(account_id: str, limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        q = (
            _admin_sb()
            .table("support_tickets")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 10)))
        )
        res = q.execute()
        rows = getattr(res, "data", None) or []
        return [row for row in rows if isinstance(row, dict)], None
    except Exception as exc:
        return [], f"support_tickets: {type(exc).__name__}: {_clip(exc)}"


def _support_list_text(account_id: str, limit: int = 5) -> str:
    rows, err = _support_tickets_for_account(account_id, limit=limit)
    if err:
        return (
            "⚠️ I could not load your support tickets right now.\n\n"
            "Please try again shortly or email support.\n\n"
            f"Support email: {_support_to_email()}"
        )
    if not rows:
        return (
            "🎫 *My Support Tickets*\n\n"
            "No support ticket found yet.\n\n"
            "Reply SUP1 to create a ticket, or SUP6 for the support email."
        )
    lines = ["🎫 *My Support Tickets*", ""]
    for index, ticket in enumerate(rows, start=1):
        lines.append(_support_ticket_line(ticket, index))
    lines.extend(["", "Reply SUP3 to view the latest ticket, SUP4 to reply, SUP5 to close a ticket, SUP1 to create a new ticket, or 0 for main menu."])
    return _clip("\n".join(lines), 3900)


def _support_latest_text(account_id: str) -> str:
    rows, err = _support_tickets_for_account(account_id, limit=1)
    if err:
        return (
            "⚠️ I could not load your latest support ticket right now.\n\n"
            f"Support email: {_support_to_email()}"
        )
    if not rows:
        return (
            "🎫 *Latest Support Ticket*\n\n"
            "No support ticket found yet.\n\n"
            "Reply SUP1 to create one."
        )

    ticket = rows[0]
    ticket_id = _clean(ticket.get("ticket_id") or ticket.get("id") or "Ticket")
    status = _clean(ticket.get("status") or "open")
    category = _clean(ticket.get("category") or ticket.get("issue_type") or "general")
    priority = _clean(ticket.get("priority") or "normal")
    subject = _clean(ticket.get("subject") or "Support request")
    message = _clean(ticket.get("message") or ticket.get("last_message_preview") or "No message preview.")
    created = _history_date_label(ticket.get("created_at"))
    updated = _history_date_label(ticket.get("updated_at"))

    body = (
        "🎫 *Latest Support Ticket*\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Status: {status}\n"
        f"Category: {category}\n"
        f"Priority: {priority}\n"
        f"Created: {created}\n"
        f"Updated: {updated}\n\n"
        f"Subject:\n{_clip(subject, 300)}\n\n"
        f"Message:\n{_clip(message, 1400)}\n\n"
        "Reply SUP4 to reply to this ticket, SUP5 to close it, SUP2 for all tickets, SUP1 to create a new ticket, or 0 for main menu."
    )
    return _clip(body, 3900)


def _create_support_ticket_from_message(
    *,
    wa_id: str,
    account_id: str,
    account: Optional[Dict[str, Any]],
    message: str,
    profile_name: str = "",
) -> Dict[str, Any]:
    clean_message = _support_message_from_command(message)
    if len(clean_message) < 10:
        _set_session_state(wa_id, context="support_create", pending_action="support_create", data={})
        return {
            "ok": True,
            "handled": "support_create_prompt",
            "send_result": _send_whatsapp_text(
                wa_id,
                "🛟 *Create Support Ticket*\n\n"
                "Please type your issue in one clear message.\n\n"
                "Example:\n"
                "I paid for Starter Monthly but my plan has not updated. Reference NTG-...\n\n"
                "Reply CANCEL to stop.",
            ),
        }

    now = _now_iso()
    ticket_id = _support_ticket_id()
    category = _support_category_from_text(clean_message)
    priority = _support_priority_from_text(clean_message)
    subject = _support_subject_from_message(clean_message)
    preview = " ".join(clean_message.split())[:200]
    account = account or {}
    email = _clean(account.get("email"))
    display_name = _clean(profile_name or account.get("display_name") or _display_phone(wa_id))
    plan_code = _current_plan_code(account_id)
    plan_name = _plan_label(account_id).split("\n", 1)[0]
    balance = _credit_balance(account_id)

    metadata = {
        "created_from": "whatsapp",
        "wa_id": _normalize_phone(wa_id),
        "profile_name": profile_name or None,
        "flow_version": WHATSAPP_FLOW_VERSION,
    }

    rich_payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "account_email": email or None,
        "account_name": display_name or None,
        "category": category,
        "priority": priority,
        "subject": subject,
        "message": clean_message,
        "plan_name": plan_name,
        "credit_balance": balance,
        "channel_state": "whatsapp",
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "last_reply_at": now,
        "last_reply_by": "user",
        "last_message_preview": preview,
        "issue_type": category,
        "channel": "whatsapp",
        "source": "whatsapp",
        "plan_code": plan_code,
        "metadata": metadata,
    }

    mid_payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "category": category,
        "priority": priority,
        "subject": subject,
        "message": clean_message,
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "last_message_preview": preview,
        "issue_type": category,
        "channel": "whatsapp",
        "source": "whatsapp",
    }

    minimal_payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "subject": subject,
        "message": clean_message,
        "category": category,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }

    insert_result = None
    last_error = ""
    for payload in (rich_payload, mid_payload, minimal_payload):
        insert_result = _safe_insert_admin("support_tickets", payload)
        if insert_result.get("ok"):
            break
        last_error = _clean(insert_result.get("error"))

    _set_session_state(wa_id, context="main", pending_action="", data={})

    if not insert_result or not insert_result.get("ok"):
        return {
            "ok": True,
            "handled": "support_create_failed",
            "send_result": _send_whatsapp_text(
                wa_id,
                "⚠️ I could not create the support ticket right now.\n\n"
                f"Please email: {_support_to_email()}\n\n"
                "Include your issue and payment reference if any.",
            ),
            "debug": {"error": last_error} if _debug_enabled() else None,
        }

    body = (
        "✅ *Support ticket created successfully*\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Category: {category}\n"
        f"Priority: {priority}\n"
        f"Status: open\n\n"
        f"Subject:\n{subject}\n\n"
        "Our support team can review it from the support dashboard.\n\n"
        "Reply SUP2 to view your tickets, SUP3 for the latest ticket, SUP4 to add a reply, or 0 for main menu."
    )
    return {"ok": True, "handled": "support_ticket_created", "send_result": _send_whatsapp_text(wa_id, _clip(body, 3900))}



def _support_extract_ticket_id(value: Any) -> str:
    text = _clean(value).upper()
    match = re.search(r"\bNTG-WA-[A-Z0-9]{4,20}\b", text)
    return match.group(0) if match else ""


def _support_reply_message_from_command(text: Any) -> str:
    raw = _clean(text)
    raw = re.sub(r"^SUP4\b[:\-\s]*", "", raw, flags=re.I).strip()
    raw = re.sub(r"\bNTG-WA-[A-Z0-9]{4,20}\b", "", raw, count=1, flags=re.I).strip()
    return raw


def _support_latest_open_ticket(account_id: str) -> Optional[Dict[str, Any]]:
    rows, _ = _support_tickets_for_account(account_id, limit=10)
    if not rows:
        return None

    for row in rows:
        status = _normalize_text(row.get("status") or "open")
        if status not in {"closed", "resolved", "cancelled", "canceled"}:
            return row

    return rows[0]


def _support_ticket_by_reference(account_id: str, ticket_ref: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    ticket_ref = _clean(ticket_ref).upper()

    if ticket_ref:
        try:
            res = (
                _admin_sb()
                .table("support_tickets")
                .select("*")
                .eq("account_id", account_id)
                .eq("ticket_id", ticket_ref)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if rows and isinstance(rows[0], dict):
                return rows[0], None
        except Exception as exc:
            return None, f"support_tickets: {type(exc).__name__}: {_clip(exc)}"
        return None, None

    return _support_latest_open_ticket(account_id), None


def _support_ticket_ref(ticket: Dict[str, Any]) -> str:
    return _clean(ticket.get("ticket_id") or ticket.get("id") or "Ticket")


def _insert_support_reply_row(
    *,
    ticket: Dict[str, Any],
    account_id: str,
    message: str,
    wa_id: str,
    sender_type: str = "user",
) -> Dict[str, Any]:
    now = _now_iso()
    ticket_id = _support_ticket_ref(ticket)
    metadata = {
        "wa_id": _normalize_phone(wa_id),
        "flow_version": WHATSAPP_FLOW_VERSION,
        "support_ticket_row_id": _clean(ticket.get("id")) or None,
    }

    rich_payload = {
        "ticket_id": ticket_id,
        "ticket_row_id": _clean(ticket.get("id")) or None,
        "account_id": account_id,
        "message": message,
        "sender_type": sender_type,
        "channel": "whatsapp",
        "source": "whatsapp",
        "metadata": metadata,
        "created_at": now,
    }
    mid_payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "message": message,
        "sender_type": sender_type,
        "channel": "whatsapp",
        "source": "whatsapp",
        "created_at": now,
    }
    minimal_payload = {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "message": message,
        "created_at": now,
    }

    last: Dict[str, Any] = {"ok": False, "error": "not_attempted"}
    for payload in (rich_payload, mid_payload, minimal_payload):
        last = _safe_insert_admin("support_ticket_replies", payload)
        if last.get("ok"):
            return last
    return last


def _update_support_ticket_after_reply(ticket: Dict[str, Any], account_id: str, message: str, status: str = "open") -> Dict[str, Any]:
    now = _now_iso()
    ticket_id = _support_ticket_ref(ticket)
    preview = " ".join(_clean(message).split())[:200]

    rich_payload = {
        "status": status,
        "updated_at": now,
        "last_reply_at": now,
        "last_reply_by": "user",
        "last_message_preview": preview,
    }
    mid_payload = {
        "updated_at": now,
        "last_reply_at": now,
        "last_reply_by": "user",
        "last_message_preview": preview,
    }
    minimal_payload = {
        "updated_at": now,
        "last_message_preview": preview,
    }

    last: Dict[str, Any] = {"ok": False, "error": "not_attempted"}
    for payload in (rich_payload, mid_payload, minimal_payload):
        last = _safe_update_admin("support_tickets", payload, account_id=account_id, ticket_id=ticket_id)
        if last.get("ok"):
            return last
    return last


def _create_support_reply_from_message(
    *,
    wa_id: str,
    account_id: str,
    message: str,
    ticket_ref: str = "",
) -> Dict[str, Any]:
    ticket_ref = ticket_ref or _support_extract_ticket_id(message)
    clean_message = _support_reply_message_from_command(message)
    ticket, err = _support_ticket_by_reference(account_id, ticket_ref)

    if err:
        return {
            "ok": True,
            "handled": "support_reply_lookup_failed",
            "send_result": _send_whatsapp_text(
                wa_id,
                "⚠️ I could not load your support ticket right now.\n\n"
                f"Please email: {_support_to_email()}",
            ),
        }

    if not ticket:
        _set_session_state(wa_id, context="main", pending_action="", data={})
        return {
            "ok": True,
            "handled": "support_reply_no_ticket",
            "send_result": _send_whatsapp_text(
                wa_id,
                "No support ticket was found for your account yet.\n\n"
                "Reply SUP1 to create a new support ticket.",
            ),
        }

    ticket_id = _support_ticket_ref(ticket)
    if len(clean_message) < 3:
        _set_session_state(
            wa_id,
            context="support_reply",
            pending_action="support_reply",
            data={"ticket_id": ticket_id},
        )
        return {
            "ok": True,
            "handled": "support_reply_prompt",
            "send_result": _send_whatsapp_text(
                wa_id,
                "✍️ *Reply to Support Ticket*\n\n"
                f"Ticket ID: {ticket_id}\n\n"
                "Please type the message you want to add to this ticket.\n\n"
                "Reply CANCEL to stop.",
            ),
        }

    insert_result = _insert_support_reply_row(
        ticket=ticket,
        account_id=account_id,
        message=clean_message,
        wa_id=wa_id,
        sender_type="user",
    )

    if not insert_result.get("ok"):
        return {
            "ok": True,
            "handled": "support_reply_insert_failed",
            "send_result": _send_whatsapp_text(
                wa_id,
                "⚠️ I found the ticket, but I could not save your reply right now.\n\n"
                "Please try again shortly or email support.\n\n"
                f"Support email: {_support_to_email()}",
            ),
            "debug": {"error": insert_result.get("error")} if _debug_enabled() else None,
        }

    _update_support_ticket_after_reply(ticket, account_id, clean_message, status="open")
    _set_session_state(wa_id, context="main", pending_action="", data={})

    body = (
        "✅ *Reply added to support ticket*\n\n"
        f"Ticket ID: {ticket_id}\n"
        "Status: open\n\n"
        f"Your reply:\n{_clip(clean_message, 1200)}\n\n"
        "Reply SUP3 to view the latest ticket, SUP2 for all tickets, or 0 for main menu."
    )
    return {"ok": True, "handled": "support_reply_created", "send_result": _send_whatsapp_text(wa_id, _clip(body, 3900))}


def _support_close_prompt(wa_id: str, account_id: str, ticket_ref: str = "") -> Dict[str, Any]:
    ticket, err = _support_ticket_by_reference(account_id, ticket_ref)
    if err:
        return {
            "ok": True,
            "handled": "support_close_lookup_failed",
            "send_result": _send_whatsapp_text(wa_id, "⚠️ I could not load your support ticket right now. Please try again shortly."),
        }
    if not ticket:
        return {
            "ok": True,
            "handled": "support_close_no_ticket",
            "send_result": _send_whatsapp_text(wa_id, "No support ticket was found for your account yet.\n\nReply SUP1 to create a ticket."),
        }

    ticket_id = _support_ticket_ref(ticket)
    status = _clean(ticket.get("status") or "open")
    if _normalize_text(status) in {"closed", "resolved"}:
        return {
            "ok": True,
            "handled": "support_close_already_closed",
            "send_result": _send_whatsapp_text(
                wa_id,
                f"This ticket is already closed/resolved.\n\nTicket ID: {ticket_id}\nStatus: {status}\n\nReply SUP2 for all tickets.",
            ),
        }

    _set_session_state(
        wa_id,
        context="support_close_confirm",
        pending_action="support_close_confirm",
        data={"ticket_id": ticket_id},
    )
    return {
        "ok": True,
        "handled": "support_close_confirm_prompt",
        "send_result": _send_whatsapp_text(
            wa_id,
            "🔒 *Close Support Ticket*\n\n"
            f"Ticket ID: {ticket_id}\n"
            f"Current status: {status}\n\n"
            "Reply YES CLOSE to close this ticket, or CANCEL to keep it open.",
        ),
    }


def _confirm_close_support_ticket(wa_id: str, account_id: str, ticket_ref: str = "") -> Dict[str, Any]:
    ticket, err = _support_ticket_by_reference(account_id, ticket_ref)
    if err or not ticket:
        _set_session_state(wa_id, context="main", pending_action="", data={})
        return {
            "ok": True,
            "handled": "support_close_not_found",
            "send_result": _send_whatsapp_text(wa_id, "I could not find that support ticket again. Reply SUP2 to view your tickets."),
        }

    ticket_id = _support_ticket_ref(ticket)
    close_message = "Ticket closed by user from WhatsApp."
    _insert_support_reply_row(
        ticket=ticket,
        account_id=account_id,
        message=close_message,
        wa_id=wa_id,
        sender_type="user",
    )

    now = _now_iso()
    update_attempts = [
        {
            "status": "closed",
            "updated_at": now,
            "last_reply_at": now,
            "last_reply_by": "user",
            "last_message_preview": close_message,
        },
        {
            "status": "resolved",
            "updated_at": now,
            "last_reply_at": now,
            "last_reply_by": "user",
            "last_message_preview": close_message,
        },
        {
            "updated_at": now,
            "last_reply_at": now,
            "last_reply_by": "user",
            "last_message_preview": close_message,
        },
        {
            "updated_at": now,
            "last_message_preview": close_message,
        },
    ]

    update_result: Dict[str, Any] = {"ok": False}
    for payload in update_attempts:
        update_result = _safe_update_admin("support_tickets", payload, account_id=account_id, ticket_id=ticket_id)
        if update_result.get("ok"):
            break

    _set_session_state(wa_id, context="main", pending_action="", data={})

    if not update_result.get("ok"):
        return {
            "ok": True,
            "handled": "support_close_failed",
            "send_result": _send_whatsapp_text(
                wa_id,
                "⚠️ I could not close the ticket right now. Please try again shortly or contact support."
            ),
            "debug": {"error": update_result.get("error")} if _debug_enabled() else None,
        }

    return {
        "ok": True,
        "handled": "support_ticket_closed",
        "send_result": _send_whatsapp_text(
            wa_id,
            "✅ *Support ticket closed*\n\n"
            f"Ticket ID: {ticket_id}\n\n"
            "Reply SUP2 to view your tickets, SUP1 to create a new ticket, or 0 for main menu.",
        ),
    }


def _handle_support_command(
    wa_id: str,
    account_id: str,
    text: str,
    account: Optional[Dict[str, Any]] = None,
    profile_name: str = "",
) -> Dict[str, Any]:
    norm = _normalize_text(text)
    if norm in {"support", "help support", "support menu", "customer support", "contact support"}:
        return {"ok": True, "handled": "support_menu", "send_result": _send_whatsapp_text(wa_id, _support_menu())}
    if norm.startswith("sup1"):
        return _create_support_ticket_from_message(
            wa_id=wa_id,
            account_id=account_id,
            account=account,
            message=text,
            profile_name=profile_name,
        )
    if norm in {"sup2", "my tickets", "view tickets", "support tickets"}:
        return {"ok": True, "handled": "support_tickets", "send_result": _send_whatsapp_text(wa_id, _support_list_text(account_id, limit=5))}
    if norm in {"sup3", "latest ticket", "last ticket", "recent ticket"}:
        return {"ok": True, "handled": "support_latest", "send_result": _send_whatsapp_text(wa_id, _support_latest_text(account_id))}
    if norm.startswith("sup4") or norm in {"reply ticket", "reply to ticket", "support reply", "respond to ticket"}:
        return _create_support_reply_from_message(wa_id=wa_id, account_id=account_id, message=text)
    if norm.startswith("sup5") or norm in {"close ticket", "close support ticket", "resolve ticket"}:
        return _support_close_prompt(wa_id=wa_id, account_id=account_id, ticket_ref=_support_extract_ticket_id(text))
    if norm in {"sup6", "support email", "contact email", "email support"}:
        return {"ok": True, "handled": "support_contact", "send_result": _send_whatsapp_text(wa_id, _support_contact_text())}
    return {"ok": True, "handled": "support_menu", "send_result": _send_whatsapp_text(wa_id, _support_menu())}


# =============================================================================
# WhatsApp Credit Activity helpers
# =============================================================================

def _credit_activity_menu() -> str:
    return (
        "💎 *Credit Activity*\n\n"
        "CR1 - Credit balance\n"
        "CR2 - Recent credit activity\n"
        "CR3 - AI credit deductions\n"
        "CR4 - Credit additions / top-up history\n\n"
        "Reply with CR1, CR2, CR3, or CR4.\n"
        "Reply 0 for main menu."
    )


def _safe_int_value(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _credit_delta_label(delta: Any) -> str:
    value = _safe_int_value(delta, 0)
    if value > 0:
        return f"+{value}"
    return str(value)


def _credit_activity_rows(account_id: str, limit: int = 10) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        q = (
            _admin_sb()
            .table("credit_usage_logs")
            .select("account_id,reference,action_code,description,channel,credits_delta,balance_after,metadata,created_at")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 30)))
        )
        res = q.execute()
        rows = getattr(res, "data", None) or []
        return [row for row in rows if isinstance(row, dict)], None
    except Exception as exc:
        return [], f"credit_usage_logs: {type(exc).__name__}: {_clip(exc)}"


def _credit_activity_line(row: Dict[str, Any], index: int) -> str:
    action = _clean(row.get("action_code") or "credit_activity")
    desc = _clean(row.get("description") or action.replace("_", " ").title())
    channel = _clean(row.get("channel") or "app")
    created = _history_date_label(row.get("created_at"))
    delta = _credit_delta_label(row.get("credits_delta"))
    balance_after = row.get("balance_after")
    balance_text = ""
    if balance_after is not None:
        balance_text = f" | balance: {_safe_int_value(balance_after, 0)}"
    ref = _clean(row.get("reference"))
    ref_text = f"\n   Ref: {_clip(ref, 80)}" if ref else ""
    return f"{index}. {delta} credit(s) - {_clip(desc, 100)}\n   {created} | {channel}{balance_text}{ref_text}"


def _credit_balance_text(account_id: str) -> str:
    balance = _credit_balance(account_id)
    plan = _plan_label(account_id).split("\n", 1)[0]
    return (
        "💎 *Usage Credit Balance*\n\n"
        f"Available balance: {balance}\n"
        f"Current plan: {plan}\n\n"
        "Use CR2 to view recent credit activity.\n"
        "Use CR3 to view AI credit deductions.\n"
        "Use CR4 to view credit additions/top-ups.\n\n"
        "Reply 0 for main menu."
    )


def _credit_activity_text(account_id: str, mode: str = "all") -> str:
    rows, err = _credit_activity_rows(account_id, limit=20)
    if err:
        return (
            "⚠️ I could not load your credit activity right now.\n\n"
            "Please try again shortly, or reply SUP1 to contact support."
        )

    title = "📒 *Recent Credit Activity*"
    empty = "No credit activity log found yet."

    if mode == "debits":
        rows = [r for r in rows if _safe_int_value(r.get("credits_delta"), 0) < 0]
        title = "📉 *AI Credit Deductions*"
        empty = "No AI credit deduction log found yet."
    elif mode == "credits":
        rows = [r for r in rows if _safe_int_value(r.get("credits_delta"), 0) > 0]
        title = "📈 *Credit Additions / Top-ups*"
        empty = "No credit addition or top-up log found yet."

    rows = rows[:5]
    if not rows:
        return (
            f"{title}\n\n"
            f"{empty}\n\n"
            f"Current balance: {_credit_balance(account_id)}\n\n"
            "Reply CR1 for balance or 0 for main menu."
        )

    lines = [title, ""]
    for index, row in enumerate(rows, start=1):
        lines.append(_credit_activity_line(row, index))
    lines.extend([
        "",
        f"Current balance: {_credit_balance(account_id)}",
        "",
        "Reply CR1 for balance, CR3 for deductions, CR4 for additions, or 0 for main menu.",
    ])
    return _clip("\n".join(lines), 3900)


def _handle_credit_activity_command(wa_id: str, account_id: str, text: str) -> Dict[str, Any]:
    norm = _normalize_text(text)
    if norm in {"credits activity", "credit activity", "credit menu", "usage activity", "usage credit activity"}:
        return {"ok": True, "handled": "credit_activity_menu", "send_result": _send_whatsapp_text(wa_id, _credit_activity_menu())}
    if norm in {"cr1", "credit balance", "usage credit balance", "my credit balance"}:
        return {"ok": True, "handled": "credit_balance", "send_result": _send_whatsapp_text(wa_id, _credit_balance_text(account_id))}
    if norm in {"cr2", "recent credit activity", "recent credits", "credit logs", "credit history"}:
        return {"ok": True, "handled": "credit_activity_recent", "send_result": _send_whatsapp_text(wa_id, _credit_activity_text(account_id, mode="all"))}
    if norm in {"cr3", "ai credit deductions", "credit deductions", "deductions", "credits deducted"}:
        return {"ok": True, "handled": "credit_activity_debits", "send_result": _send_whatsapp_text(wa_id, _credit_activity_text(account_id, mode="debits"))}
    if norm in {"cr4", "credit additions", "topup history", "top-up history", "credit topups", "credit top-ups"}:
        return {"ok": True, "handled": "credit_activity_credits", "send_result": _send_whatsapp_text(wa_id, _credit_activity_text(account_id, mode="credits"))}
    return {"ok": True, "handled": "credit_activity_menu", "send_result": _send_whatsapp_text(wa_id, _credit_activity_menu())}


# =============================================================================
# Core action handlers
# =============================================================================

def _handle_plan_selection(wa_id: str, account: Dict[str, Any], account_id: str, code: str) -> Dict[str, Any]:
    item = PLAN_OPTIONS[code]

    if _same_active_plan(account_id, item["plan_code"]):
        expiry = _subscription_expiry(account_id)
        body = (
            f"You already have {item['name']} active.\n\n"
            f"Current balance: {_credit_balance(account_id)} Usage Credits\n"
            f"Plan expiry: {expiry[:10] if expiry else 'Not shown'}\n\n"
            "To upgrade, choose a higher plan like P1, P2, P3, B1, B2, or B3.\n"
            "Reply 4 to view plans or 0 for main menu."
        )
        return {"ok": True, "handled": "same_active_plan_blocked", "send_result": _send_whatsapp_text(wa_id, body)}

    current = _current_plan_code(account_id)
    if _plan_rank(current) > _plan_rank(item["plan_code"]) and _subscription_status(account_id) == "active":
        body = (
            f"You currently have a higher plan active:\n{_plan_label(account_id)}\n\n"
            f"You selected {item['name']}. Downgrades should be handled carefully so your current access is not lost.\n\n"
            "For now, choose a higher plan from this chat or contact support for downgrade help."
        )
        return {"ok": True, "handled": "downgrade_redirected", "send_result": _send_whatsapp_text(wa_id, body)}

    email_prompt = _ensure_email_or_prompt(
        wa_id=wa_id,
        account=account,
        pending_action="plan_selection",
        data={"code": code},
    )
    if email_prompt:
        return email_prompt

    checkout = _init_paystack_checkout(account, account_id, item, "subscription", wa_id=wa_id)

    if checkout.get("ok"):
        _set_session_state(
            wa_id,
            "payment_pending",
            "plan_selection",
            {
                "code": code,
                "reference": checkout.get("reference"),
                "authorization_url": checkout.get("authorization_url"),
                "created_at": _now_iso(),
            },
        )
        body = (
            f"{item['name']} selected.\n\n"
            f"Price: {_money(item['price'])}\n"
            f"Included Usage Credits: {item['credits']}\n\n"
            f"Complete payment here:\n{checkout['authorization_url']}\n\n"
            "After payment, Paystack should return you to this bot chat. A success message will also be sent after webhook confirmation."
        )
    else:
        body = (
            f"{item['name']} selected.\n\n"
            f"Price: {_money(item['price'])}\n"
            f"Included Usage Credits: {item['credits']}\n\n"
            f"Payment link could not be generated right now. Please reply 4 to try again or contact support."
        )

    return {"ok": True, "handled": "plan_selection", "send_result": _send_whatsapp_text(wa_id, body), "checkout": checkout}


def _handle_topup_selection(wa_id: str, account: Dict[str, Any], account_id: str, code: str) -> Dict[str, Any]:
    if not _is_active_paid_subscription(account_id):
        body = (
            "Usage Credit add-ons are available only to active paid subscribers.\n\n"
            "Please upgrade first by replying 4 to view plans.\n\n"
            "Reply 0 for main menu."
        )
        return {"ok": True, "handled": "topup_blocked_no_paid_plan", "send_result": _send_whatsapp_text(wa_id, body)}

    item = TOPUP_OPTIONS[code]

    email_prompt = _ensure_email_or_prompt(
        wa_id=wa_id,
        account=account,
        pending_action="topup_selection",
        data={"code": code},
    )
    if email_prompt:
        return email_prompt

    checkout = _init_paystack_checkout(account, account_id, item, "topup", wa_id=wa_id)

    if checkout.get("ok"):
        _set_session_state(
            wa_id,
            "payment_pending",
            "topup_selection",
            {
                "code": code,
                "reference": checkout.get("reference"),
                "authorization_url": checkout.get("authorization_url"),
                "created_at": _now_iso(),
            },
        )
        body = (
            f"{item['name']} selected.\n\n"
            f"Credits: {item['credits']}\n"
            f"Price: {_money(item['price'])}\n\n"
            f"Complete payment here:\n{checkout['authorization_url']}\n\n"
            "Top-ups add credits only. They do not renew or extend your plan."
        )
    else:
        body = (
            f"{item['name']} selected.\n\n"
            f"Credits: {item['credits']}\n"
            f"Price: {_money(item['price'])}\n\n"
            f"Payment link could not be generated right now. Please reply 6 to try again or contact support."
        )

    return {"ok": True, "handled": "topup_selection", "send_result": _send_whatsapp_text(wa_id, body), "checkout": checkout}





# v11 override: safe deadline delete helper that does not depend on missing helpers.
def _safe_delete_v11(table: str, **filters: Any) -> Dict[str, Any]:
    try:
        params = {}
        for key, value in filters.items():
            if value is not None and _clean(value):
                params[key] = f"eq.{_clean(value)}"
        url = f"{_supabase_url().rstrip('/')}/rest/v1/{table}"
        headers = _supabase_headers()
        headers["Prefer"] = "return=representation"
        response = requests.delete(url, headers=headers, params=params, timeout=25)
        try:
            data = response.json() if response.text else []
        except Exception:
            data = response.text
        return {"ok": response.status_code < 400, "status_code": response.status_code, "data": data}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"}



def _handle_deadline_delete_v10(wa_id: str, account_id: str, text: str) -> Dict[str, Any]:
    if not _deadline_allowed_for_account(account_id):
        body = (
            "🔒 Custom deadline management is available on paid plans.\n\n"
            "Reply 4 to view plans, or D2 to view any existing reminders."
        )
        return {"ok": True, "handled": "deadline_delete_blocked", "send_result": _send_whatsapp_text(wa_id, body)}

    match = re.search(r"\bD3\s+(\d{1,2})\b", _clean(text), flags=re.I)
    if not match:
        return {"ok": True, "handled": "deadline_delete_help", "send_result": _send_whatsapp_text(wa_id, "🗑️ To delete a reminder, reply like this:\n\nD3 1\n\nUse D2 first to see your reminder numbers.")}

    idx = int(match.group(1))
    rows = _get_deadline_list(account_id, limit=10)
    if idx < 1 or idx > len(rows):
        return {"ok": True, "handled": "deadline_delete_not_found", "send_result": _send_whatsapp_text(wa_id, "I could not find that reminder number. Reply D2 to view your current reminders.")}

    item = rows[idx - 1]
    deadline_id = _clean(item.get("id"))
    if not deadline_id:
        return {"ok": True, "handled": "deadline_delete_missing_id", "send_result": _send_whatsapp_text(wa_id, "I found the reminder, but it has no valid ID. Please try D2 again or contact support.")}

    result = _delete_deadline_by_id_v14(deadline_id)
    if not result.get("ok"):
        return {"ok": True, "handled": "deadline_delete_failed", "send_result": _send_whatsapp_text(wa_id, "⚠️ I could not delete that reminder now. Reply D2 and try again, for example D3 1.")}

    body = (
        "🗑️ *Deadline Reminder Deleted*\n\n"
        f"{_deadline_display_line(item, idx)}\n\n"
        "Reply D2 to view remaining reminders."
    )
    return {"ok": True, "handled": "deadline_deleted", "send_result": _send_whatsapp_text(wa_id, body), "delete_result": result if _debug_enabled() else None}


def _handle_deadline_settings_v10(wa_id: str, account_id: str, text: str) -> Dict[str, Any]:
    if not _deadline_allowed_for_account(account_id):
        body = (
            "🔒 Custom reminder settings are available on paid plans.\n\n"
            "Reply 4 to view plans, or D2 to view any existing reminders."
        )
        return {"ok": True, "handled": "deadline_settings_blocked", "send_result": _send_whatsapp_text(wa_id, body)}
    match = re.search(r"\bD4\s+(\d{1,2})\s+(\d{1,3})(?:\s+(\d{1,2}:\d{2}))?(?:\s+(WHATSAPP|EMAIL|SMS|WHATSAPP,EMAIL|WHATSAPP,SMS|EMAIL,SMS|WHATSAPP,EMAIL,SMS))?\b", _clean(text), flags=re.I)
    if not match:
        return {"ok": True, "handled": "deadline_settings_help", "send_result": _send_whatsapp_text(wa_id, "⚙️ To update reminder, reply like this:\n\nD4 1 3 09:00 whatsapp\n\nUse D2 first to see your reminder numbers.")}
    idx = int(match.group(1))
    days = max(0, min(365, int(match.group(2))))
    reminder_time = _valid_reminder_time_v14(match.group(3) or "09:00")
    reminder_mode = _valid_reminder_mode_v14(match.group(4) or "whatsapp")
    rows = _get_deadline_list(account_id, limit=10)
    if idx < 1 or idx > len(rows):
        return {"ok": True, "handled": "deadline_settings_not_found", "send_result": _send_whatsapp_text(wa_id, "I could not find that reminder number. Reply D2 to view your current reminders.")}
    item = rows[idx - 1]
    validation = _deadline_validation_v13(item.get("due_date"), days)
    if not validation.get("ok"):
        return {"ok": True, "handled": "deadline_settings_invalid", "send_result": _send_whatsapp_text(wa_id, "⚠️ *Reminder Not Updated*\n\n" + str(validation.get("message")) + "\n\nReply D2 to view your reminders.")}
    deadline_id = _clean(item.get("id"))
    payload = {"reminder_days_before": days, "enabled": True, "updated_at": _now_iso(), "reminder_time": reminder_time, "timezone": "Africa/Lagos", "reminder_mode": reminder_mode}
    result = _safe_update("tax_deadlines", payload, id=deadline_id)
    if not result.get("ok"):
        # Retry for databases that have not yet added the optional v14 columns.
        result = _safe_update("tax_deadlines", {"reminder_days_before": days, "enabled": True, "updated_at": _now_iso()}, id=deadline_id)
    if not result.get("ok"):
        return {"ok": True, "handled": "deadline_settings_failed", "send_result": _send_whatsapp_text(wa_id, "⚠️ I could not update that reminder now. Reply D2 and try again.")}
    item = dict(item)
    item["reminder_days_before"] = days
    item["enabled"] = True
    item["reminder_time"] = reminder_time
    item["reminder_mode"] = reminder_mode
    body = (
        "⚙️ *Reminder Updated*\n\n"
        f"{_deadline_display_line(item, idx)}\n\n"
        "Reply D2 to view all reminders."
    )
    return {"ok": True, "handled": "deadline_settings_updated", "send_result": _send_whatsapp_text(wa_id, body), "update_result": result if _debug_enabled() else None}


def _handle_quiz_answer_v10(wa_id: str, account_id: str, text: str) -> Optional[Dict[str, Any]]:
    choice = _extract_choice_letter(text)
    if not choice:
        return None

    state = _get_session_state(wa_id)
    data = _session_data(state)
    active = data.get("active_quiz") if isinstance(data.get("active_quiz"), dict) else None
    if not active:
        return None

    paid = _is_active_paid_subscription(account_id)
    if not paid:
        attempts = _quiz_attempts_for_today(data)
        if attempts >= 12:
            return {
                "ok": True,
                "handled": "quiz_free_limit",
                "send_result": _send_whatsapp_text(
                    wa_id,
                    "🔒 You have used your 12 free non-AI quiz attempts for today.\n\nPaid plans include unlimited non-AI quiz attempts. Reply 4 to view plans.",
                ),
            }

    correct = _clean(active.get("answer") or active.get("correct") or active.get("correct_answer")).upper()[:1]
    if correct not in {"A", "B", "C", "D"}:
        correct = "A"

    attempts_after = _increment_quiz_attempts(wa_id)

    state = _get_session_state(wa_id)
    data = _session_data(state)
    score = data.get("quiz_score") if isinstance(data.get("quiz_score"), dict) else {"correct": 0, "total": 0}
    score["total"] = int(score.get("total") or 0) + 1
    if choice == correct:
        score["correct"] = int(score.get("correct") or 0) + 1

    last_quiz = {
        "question": active.get("question"),
        "selected": choice,
        "correct": correct,
        "is_correct": choice == correct,
        "explanation": active.get("explanation") or active.get("note") or "",
        "category": active.get("category") or "General",
    }
    data["quiz_score"] = score
    data["last_quiz"] = last_quiz
    data["active_quiz"] = None

    _set_session_state(wa_id, context="main", pending_action="", data=data)

    verdict = "✅ Correct!" if choice == correct else f"❌ Not correct. Correct answer: {correct}"
    attempts_text = "Unlimited on paid plan" if paid else str(attempts_after)
    body = (
        f"🧠 *Quiz Result*\n\n"
        f"Your answer: {choice}\n"
        f"{verdict}\n\n"
        f"Score: {score['correct']}/{score['total']}\n"
        f"Today's free attempts used: {attempts_text}\n\n"
        "Reply Q1 for another quiz, Q4 to review last answer, or Q5 for AI explanation."
    )
    return {"ok": True, "handled": "quiz_answer", "send_result": _send_whatsapp_text(wa_id, body)}



def _handle_text_message(msg: Dict[str, Any]) -> Dict[str, Any]:
    wa_id = _normalize_phone(msg.get("wa_id"))
    text = _clean(msg.get("text"))
    profile_name = _clean(msg.get("profile_name"))

    if not wa_id:
        return {"ok": False, "error": "missing_wa_id"}

    state = _get_session_state(wa_id)
    context = _clean(state.get("context") or "main")

    if not text:
        _set_session_state(wa_id, "main")
        return {"ok": True, "handled": "empty_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu())}

    # Recognize commands before trying link-code lookup.
    # This prevents values like C3 985000 or C3985000 from being queried as link codes,
    # and prevents free calculators from falling through to AI credit deduction.
    recognition = _recognize(text, context)
    is_command_like = recognition.get("kind") in {"global", "main", "plan", "topup", "tool", "calc", "quiz_action", "deadline", "history", "support", "credit_activity", "ambiguous", "invalid_menu"}

    # Only attempt link-code lookup when the user is in the link flow, or when the input
    # is not already recognized as a command/calculator/plan/top-up/tool.
    if context == "link" or (not is_command_like and context not in {"support_create", "support_reply", "support_close_confirm"}):
        link_reply = _try_link_code(wa_id, text, profile_name=profile_name)
        if link_reply:
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "link_code", "send_result": _send_whatsapp_text(wa_id, link_reply)}

    account, account_debug = _create_or_update_wa_account(wa_id, profile_name=profile_name)
    account_id = _account_id_from_row(account)

    if not account_id:
        return {
            "ok": False,
            "error": "account_resolution_failed",
            "send_result": _send_whatsapp_text(wa_id, "I could not identify your account yet. Please try again or contact support."),
            "debug": account_debug if _debug_enabled() else None,
        }

    if context == "collect_email":
        if not _is_valid_email(text):
            return {
                "ok": True,
                "handled": "invalid_email",
                "send_result": _send_whatsapp_text(
                    wa_id,
                    "That does not look like a valid email address.\n\nPlease send your email like this: name@email.com\n\nReply CANCEL to stop.",
                ),
            }

        _safe_update("accounts", {"email": _lower(text), "updated_at": _now_iso()}, account_id=account_id)
        refreshed_account = dict(account or {})
        refreshed_account["email"] = _lower(text)

        pending_action = _clean(state.get("pending_action"))
        pending_data = state.get("data") if isinstance(state.get("data"), dict) else {}
        _set_session_state(wa_id, "main")

        if pending_action == "plan_selection" and pending_data.get("code") in PLAN_OPTIONS:
            return _handle_plan_selection(wa_id, refreshed_account, account_id, pending_data["code"])

        if pending_action == "topup_selection" and pending_data.get("code") in TOPUP_OPTIONS:
            return _handle_topup_selection(wa_id, refreshed_account, account_id, pending_data["code"])

        return {
            "ok": True,
            "handled": "email_saved",
            "send_result": _send_whatsapp_text(wa_id, "✅ Email saved successfully.\n\nReply 4 for plans or 6 for Usage Credit add-ons."),
        }

    if context == "support_create":
        normalized = _normalize_text(text)
        if normalized in {"cancel", "stop", "end", "0", "menu", "main", "main menu", "back"}:
            _set_session_state(wa_id, "main")
            return {
                "ok": True,
                "handled": "support_create_cancelled",
                "send_result": _send_whatsapp_text(wa_id, "Support ticket creation cancelled.\n\n" + _main_menu(wa_id, account_id)),
            }
        return _create_support_ticket_from_message(
            wa_id=wa_id,
            account_id=account_id,
            account=account,
            message=text,
            profile_name=profile_name,
        )

    if context == "support_reply":
        normalized = _normalize_text(text)
        if normalized in {"cancel", "stop", "end", "0", "menu", "main", "main menu", "back"}:
            _set_session_state(wa_id, "main")
            return {
                "ok": True,
                "handled": "support_reply_cancelled",
                "send_result": _send_whatsapp_text(wa_id, "Support reply cancelled.\n\n" + _main_menu(wa_id, account_id)),
            }
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        return _create_support_reply_from_message(
            wa_id=wa_id,
            account_id=account_id,
            message=text,
            ticket_ref=_clean(data.get("ticket_id")),
        )

    if context == "support_close_confirm":
        normalized = _normalize_text(text)
        data = state.get("data") if isinstance(state.get("data"), dict) else {}
        ticket_ref = _clean(data.get("ticket_id"))
        if normalized in {"yes", "yes close", "close", "confirm", "confirm close", "resolved", "done"}:
            return _confirm_close_support_ticket(wa_id=wa_id, account_id=account_id, ticket_ref=ticket_ref)
        if normalized in {"cancel", "stop", "end", "0", "menu", "main", "main menu", "back", "no", "keep open"}:
            _set_session_state(wa_id, "main")
            return {
                "ok": True,
                "handled": "support_close_cancelled",
                "send_result": _send_whatsapp_text(wa_id, "Ticket close cancelled. The ticket remains open.\n\n" + _main_menu(wa_id, account_id)),
            }
        return {
            "ok": True,
            "handled": "support_close_confirm_repeat",
            "send_result": _send_whatsapp_text(wa_id, "Please reply YES CLOSE to close the ticket, or CANCEL to keep it open."),
        }

    if context == "unlink_confirm":
        normalized = _normalize_text(text)
        if normalized in {"yes", "yes unlink", "unlink", "confirm", "confirm unlink", "disconnect"}:
            unlink_result = _unlink_whatsapp_channel(wa_id=wa_id, account_id=account_id)
            _set_session_state(wa_id, "main")
            if unlink_result.get("ok"):
                return {
                    "ok": True,
                    "handled": "whatsapp_unlinked",
                    "send_result": _send_whatsapp_text(
                        wa_id,
                        "✅ WhatsApp has been unlinked from the website account.\n\n"
                        + _main_menu(wa_id, account_id),
                    ),
                }
            return {
                "ok": True,
                "handled": "whatsapp_unlink_failed",
                "send_result": _send_whatsapp_text(
                    wa_id,
                    "⚠️ I could not unlink this WhatsApp account now. Please use the Channels page on the website or contact support.\n\nReply 0 for main menu.",
                ),
                "debug": unlink_result if _debug_enabled() else None,
            }

        if normalized in {"no", "cancel", "back", "*", "0", "menu", "main menu"}:
            _set_session_state(wa_id, "main")
            return {
                "ok": True,
                "handled": "whatsapp_unlink_cancelled",
                "send_result": _send_whatsapp_text(wa_id, "Unlink cancelled.\n\n" + _main_menu(wa_id, account_id)),
            }

        return {
            "ok": True,
            "handled": "whatsapp_unlink_confirm_prompt",
            "send_result": _send_whatsapp_text(
                wa_id,
                "Reply YES UNLINK to unlink this WhatsApp number from the website account, or reply 0 to cancel.",
            ),
        }

    if context == "quiz_answer":
        return _handle_quiz_answer(wa_id, account_id, text, state)

    
    # v10: handle quiz answers and deadline management before link-code/AI fallback.
    quiz_answer_result = _handle_quiz_answer_v10(wa_id, account_id, text)
    if quiz_answer_result:
        return quiz_answer_result

    upper_text = _clean(text).upper()
    if re.match(r"^D3\b", upper_text):
        try:
            return _handle_deadline_delete_v10(wa_id, account_id, text)
        except Exception as exc:
            return {"ok": True, "handled": "deadline_delete_error", "send_result": _send_whatsapp_text(wa_id, "⚠️ I could not delete that reminder now. Reply D2 to view your reminders, then try again as D3 1."), "error": str(exc)[:300] if _debug_enabled() else None}

    if re.match(r"^D4\b", upper_text):
        try:
            return _handle_deadline_settings_v10(wa_id, account_id, text)
        except Exception as exc:
            return {"ok": True, "handled": "deadline_settings_error", "send_result": _send_whatsapp_text(wa_id, "⚠️ I could not update that reminder now. Reply D2 to view your reminders, then try again as D4 1 3."), "error": str(exc)[:300] if _debug_enabled() else None}

    # v15: let the main quiz handler own Q1-Q5 so score/review/explanation stay consistent.
    if re.match(r"^Q[1-5]\b", upper_text):
        return _handle_quiz_command(wa_id, account_id, text, state)


    payment_reference = _extract_payment_reference(text)
    if payment_reference:
        _set_session_state(wa_id, "main")
        body = (
            "✅ Payment reference received.\n\n"
            f"Reference: {payment_reference}\n\n"
            f"Current plan:\n{_plan_label(account_id)}\n\n"
            f"Available Usage Credits: {_credit_balance(account_id)}\n\n"
            "If you just paid and this has not updated yet, wait a few seconds and send 3 for plan or 2 for credits."
        )
        return {"ok": True, "handled": "payment_reference_status", "send_result": _send_whatsapp_text(wa_id, body)}

    if recognition["kind"] == "global":
        action = recognition["action"]
        if action == "main_menu":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "main_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu(wa_id, account_id))}
        if action == "back":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "back_main", "send_result": _send_whatsapp_text(wa_id, _main_menu(wa_id, account_id))}
        if action == "cancel":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "cancel", "send_result": _send_whatsapp_text(wa_id, "Current flow cancelled.\n\n" + _main_menu(wa_id, account_id))}

    if recognition.get("kind") == "quiz_action":
        return _handle_quiz_command(wa_id, account_id, text, state)

    if recognition.get("kind") == "quiz_rules":
        return {"ok": True, "handled": "quiz_rules", "send_result": _send_whatsapp_text(wa_id, _quiz_rules_text())}

    if recognition.get("kind") == "deadline":
        return _handle_deadline_command(wa_id, account_id, text)

    if recognition.get("kind") == "history":
        return _handle_history_command(wa_id, account_id, text)

    if recognition.get("kind") == "support":
        return _handle_support_command(wa_id, account_id, text, account=account, profile_name=profile_name)

    if recognition.get("kind") == "credit_activity":
        return _handle_credit_activity_command(wa_id, account_id, text)

    if recognition["kind"] == "invalid_menu":
        return {
            "ok": True,
            "handled": "invalid_menu_option",
            "send_result": _send_whatsapp_text(wa_id, "⚠️ That code/menu option is not available yet, so no AI credit was used.\n\nReply 0 for main menu, F1 for calculators, Q1 for quiz, D1 for reminders, H1 for history, CR1 for credits, SUP1 for support, or type your Nigerian tax question in words."),
        }

    if recognition["kind"] == "ambiguous":
        return {"ok": True, "handled": "ambiguous", "send_result": _send_whatsapp_text(wa_id, _ambiguous_message(recognition))}

    if recognition["kind"] == "main":
        action = recognition["action"]
        if action == "ask_prompt":
            _set_session_state(wa_id, "ask")
            return {"ok": True, "handled": "ask_prompt", "send_result": _send_whatsapp_text(wa_id, "Please type your Nigerian tax question in one clear message.")}
        if action == "credits":
            return {"ok": True, "handled": "credits", "send_result": _send_whatsapp_text(wa_id, f"💎 Usage Credits\n\nAvailable balance: {_credit_balance(account_id)}\n\nReply 0 for main menu.")}
        if action == "plan":
            return {"ok": True, "handled": "plan", "send_result": _send_whatsapp_text(wa_id, f"📌 Current Plan\n\n{_plan_label(account_id)}\n\nUsage Credits: {_credit_balance(account_id)}\n\nReply 0 for main menu.")}
        if action == "plans_menu":
            _set_session_state(wa_id, "plans")
            return {"ok": True, "handled": "plans_menu", "send_result": _send_whatsapp_text(wa_id, _plans_menu())}
        if action == "link_instruction":
            if _is_whatsapp_linked(wa_id=wa_id, account_id=account_id):
                _set_session_state(wa_id, "unlink_confirm")
                return {
                    "ok": True,
                    "handled": "unlink_instruction",
                    "send_result": _send_whatsapp_text(
                        wa_id,
                        "🔓 This WhatsApp number is already linked to a website account.\n\n"
                        "Reply YES UNLINK to unlink it, or reply 0 to keep it linked and return to the main menu.",
                    ),
                }

            _set_session_state(wa_id, "link")
            return {
                "ok": True,
                "handled": "link_instruction",
                "send_result": _send_whatsapp_text(
                    wa_id,
                    "🔗 Send your WhatsApp link code here if you already generated one.\n\n"
                    "If you have not generated a code yet, open Channels once from your website account and copy the WhatsApp code here. "
                    "After linking, you can continue using WhatsApp normally.\n\nReply 0 for main menu.",
                ),
            }
        if action == "topup_menu":
            _set_session_state(wa_id, "topup")
            return {"ok": True, "handled": "topup_menu", "send_result": _send_whatsapp_text(wa_id, _topup_menu())}
        if action == "tools_menu":
            _set_session_state(wa_id, "tools")
            return {"ok": True, "handled": "tools_menu", "send_result": _send_whatsapp_text(wa_id, _tools_menu())}
        if action == "help":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "help", "send_result": _send_whatsapp_text(wa_id, _help_text())}

    if recognition["kind"] == "plan":
        _set_session_state(wa_id, "main")
        return _handle_plan_selection(wa_id, account or {}, account_id, recognition["code"])

    if recognition["kind"] == "topup":
        _set_session_state(wa_id, "main")
        return _handle_topup_selection(wa_id, account or {}, account_id, recognition["code"])

    if recognition["kind"] == "tool":
        action = recognition.get("action")
        if action == "calculator_menu":
            _set_session_state(wa_id, "calc")
            return {"ok": True, "handled": "calculator_menu", "send_result": _send_whatsapp_text(wa_id, _calc_menu())}
        if action == "main_menu":
            _set_session_state(wa_id, "main")
            return {"ok": True, "handled": "main_menu", "send_result": _send_whatsapp_text(wa_id, _main_menu(wa_id, account_id))}
        if action == "deadlines":
            return _handle_deadline_command(wa_id, account_id, text)
        return {"ok": True, "handled": action, "send_result": _send_whatsapp_text(wa_id, _guide(str(action)))}

    if recognition["kind"] == "calc":
        action = recognition.get("action")
        if action == "tools_menu":
            _set_session_state(wa_id, "tools")
            return {"ok": True, "handled": "tools_menu", "send_result": _send_whatsapp_text(wa_id, _tools_menu())}
        if action == "tax_quiz":
            return _start_quiz(wa_id, account_id, state)
        if action == "deadlines":
            return _handle_deadline_command(wa_id, account_id, text)
        calc_text = _clean(recognition.get("rewritten_text") or text)
        return {"ok": True, "handled": action, "send_result": _send_whatsapp_text(wa_id, _handle_calculator_action(str(action), calc_text))}

    # Default: natural tax question.
    result = ask_guarded(
        {
            "account_id": account_id,
            "question": text,
            "lang": "en",
            "channel": "whatsapp",
            "provider": "wa",
            "provider_user_id": wa_id,
            "action_code": "ai_tax_answer",
        }
    )

    answer = _clean(result.get("answer") or result.get("message") or "I could not generate an answer right now. Please try again shortly.")
    _log_whatsapp_history(account_id=account_id, question=text, answer=answer, result=result if isinstance(result, dict) else {})

    meta = result.get("meta") if isinstance(result, dict) and isinstance(result.get("meta"), dict) else {}
    result_ok = bool(isinstance(result, dict) and result.get("ok") is True)

    credit_note = ""
    if result_ok and meta.get("usage_charged") is True:
        credit_note = f"\n\n💎 Credit used: {meta.get('credits_consumed') or meta.get('credit_cost') or 1}. Balance: {meta.get('credits_left', 'not shown')}."
    elif result_ok and (result.get("source") == "database" or result.get("mode") == "direct_cache"):
        credit_note = "\n\n✅ Served from saved database/cache. No new credit charged."
    elif not result_ok and result.get("error") in {"paid_plan_required", "insufficient_credits"}:
        credit_note = "\n\nNo credit was charged for this blocked request."

    return {
        "ok": True,
        "handled": "tax_question",
        "account_id": account_id,
        "send_result": _send_whatsapp_text(wa_id, _clip(answer + credit_note + "\n\nReply 0 for main menu.", 3900)),
        "usage_charged": meta.get("usage_charged"),
        "credits_consumed": meta.get("credits_consumed"),
        "debug": {"recognition": recognition, "account_debug": account_debug} if _debug_enabled() else None,
    }


# =============================================================================
# Routes
# =============================================================================

@bp.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
@bp.route("/whatsapp/webhook", methods=["GET", "POST"], strict_slashes=False)
def whatsapp_webhook():
    """
    Meta WhatsApp webhook.

    Both route patterns are intentionally registered because backend projects
    commonly register this blueprint either with:
      url_prefix="/api"
    or:
      url_prefix="/api/whatsapp"

    This prevents POST /api/whatsapp/webhook from returning 405 after deploy.
    """
    if request.method == "GET":
        verify_token = _clean(os.getenv("WHATSAPP_VERIFY_TOKEN") or os.getenv("META_VERIFY_TOKEN"))
        mode = _clean(request.args.get("hub.mode"))
        token = _clean(request.args.get("hub.verify_token"))
        challenge = _clean(request.args.get("hub.challenge"))

        if mode == "subscribe" and verify_token and token == verify_token:
            return challenge, 200

        if not mode and not token and not challenge:
            return jsonify(
                {
                    "ok": True,
                    "service": "whatsapp_webhook",
                    "version": WHATSAPP_FLOW_VERSION,
                    "methods": ["GET", "POST"],
                    "valid_paths_depend_on_blueprint_prefix": [
                        "/api/whatsapp/webhook",
                        "/api/webhook",
                        "/api/whatsapp/whatsapp/webhook",
                    ],
                    "configured": {
                        "verify_token": bool(verify_token),
                        "access_token": bool(_clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))),
                        "phone_number_id": bool(_clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))),
                        "bot_phone": bool(_whatsapp_bot_phone()),
                    },
                }
            ), 200

        return jsonify({"ok": False, "error": "verification_failed"}), 403

    payload = _safe_json()
    msg = _extract_message(payload)

    if not msg:
        # Meta can send message status updates and other non-message callbacks.
        return jsonify({"ok": True, "ignored": True, "version": WHATSAPP_FLOW_VERSION}), 200

    result = _handle_text_message(msg)

    # Always acknowledge Meta with 200 after handling attempt to avoid retries.
    body: Dict[str, Any] = {
        "ok": True,
        "handled": result.get("handled", "message"),
        "version": WHATSAPP_FLOW_VERSION,
    }
    if _debug_enabled():
        body["debug"] = result

    return jsonify(body), 200


@bp.route("/health", methods=["GET"], strict_slashes=False)
@bp.route("/whatsapp/health", methods=["GET"], strict_slashes=False)
def whatsapp_health():
    return jsonify(
        {
            "ok": True,
            "service": "whatsapp",
            "version": WHATSAPP_FLOW_VERSION,
            "configured": {
                "verify_token": bool(_clean(os.getenv("WHATSAPP_VERIFY_TOKEN") or os.getenv("META_VERIFY_TOKEN"))),
                "access_token": bool(_clean(os.getenv("WHATSAPP_ACCESS_TOKEN") or os.getenv("META_WHATSAPP_TOKEN"))),
                "phone_number_id": bool(_clean(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or os.getenv("META_WHATSAPP_PHONE_NUMBER_ID"))),
            },
        }
    ), 200


@bp.route("/test-reply", methods=["POST"], strict_slashes=False)
@bp.route("/whatsapp/test-reply", methods=["POST"], strict_slashes=False)
def whatsapp_test_reply():
    data = _safe_json()
    result = _send_whatsapp_text(_normalize_phone(data.get("to")), _clean(data.get("text") or "Naija Tax Guide WhatsApp test message."))
    return jsonify(result), 200 if result.get("ok") else 400

