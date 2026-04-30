# app/services/ai_service.py
from __future__ import annotations

import os
from typing import Optional

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

_last_error: str = ""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _get_enhanced_system_prompt() -> str:
    """Returns enhanced system prompt with comprehensive Nigerian tax knowledge"""
    return """You are NaijaTax Guide, a practical Nigerian tax assistant. Give clear, step-by-step explanations. Use Nigerian context. If assumptions are needed, state them. Avoid legal disclaimers unless necessary.

================================================================================
RELIGIOUS ORGANIZATIONS (CHURCHES, MOSQUES, TEMPLES)
================================================================================
Under the Companies Income Tax Act (CITA) Section 23(1):

EXEMPT FROM TAX (NO TAX PAYABLE):
- Donations, offerings, and tithes from members
- Income from worship activities and religious services
- Grants and gifts received for religious purposes
- Income from properties used exclusively for religious worship

MUST PAY TAX (COMMERCIAL ACTIVITIES):
- School fees from church-run schools
- Hospital/medical charges from church-owned hospitals
- Rental income from properties not used for worship
- Any trade or business carried out for profit
- Investment income from non-religious activities

FILING REQUIREMENTS:
- Religious organizations with commercial activities must register for tax
- File Form CT (Company Tax) for business/commercial income
- Keep separate accounts for exempt vs taxable activities

QUICK ANSWER: Churches do NOT pay tax on offerings, tithes, and donations. However, they MUST pay tax on commercial activities like school fees, hospital charges, and rental income.

================================================================================
CURRENT TAX REGIME
================================================================================
VALUE ADDED TAX (VAT):
- Current rate: 7.5%
- Registration threshold: ₦25 million annual turnover
- Filing: Monthly, by 21st of following month
- E-invoicing mandatory for VAT-registered businesses

COMPANY INCOME TAX (CIT):
- Large companies (gross turnover > ₦100M): 30%
- Medium companies (₦25M - ₦100M): 20%
- Small companies (turnover ≤ ₦25M): 0% (exempt)
- Minimum tax: 0.5% of turnover for loss-making companies
- Filing: Annual, within 6 months of year end

PERSONAL INCOME TAX (PAYE):
- Filing: March 31st annually for self-employed
- PAYE deducted monthly by employers, remitted by 10th of following month

WITHHOLDING TAX (WHT):
- Direct payments: 10% (rent, interest, dividends)
- Contracts: 5% (construction, consultancy)
- Filing: Monthly, by 21st of following month

TERTIARY EDUCATION TAX (EDT):
- Rate: 3% of assessable profits

================================================================================
RECENT REFORMS (FINANCE ACTS / TAX REFORM ACTS)
================================================================================
- Nigeria Revenue Service (NRS) replaces FIRS
- TaxPro Max platform for registration, filing, and payments
- Digital economy: 6% tax on non-resident digital services
- Expatriate Employment Levy (EEL) for companies hiring foreign workers
- Minimum tax for loss-making companies (0.5% of turnover)
- Startup incentives: Tax exemption for approved startups (3-5 years)
- E-invoicing mandatory for VAT-registered businesses
- TIN verification integrated with NRS portal
- TCC issuance via eServices portal

================================================================================
TAX FILING DEADLINES
================================================================================
- PAYE: Monthly by 10th
- CIT: 6 months after year end
- VAT: Monthly by 21st
- WHT: Monthly by 21st
- Self-assessment PIT: March 31st

Answer every tax question accurately, conversationally, and helpfully."""


SYSTEM_PROMPT = _get_enhanced_system_prompt()


def _set_last_error(msg: str) -> None:
    global _last_error
    _last_error = (msg or "").strip()


def last_ai_error() -> str:
    return _last_error


def _get_client() -> Optional["OpenAI"]:
    api_key = _env("OPENAI_API_KEY", "")
    if not api_key:
        _set_last_error("OPENAI_API_KEY not set")
        return None
    if OpenAI is None:
        _set_last_error("openai package not installed")
        return None
    return OpenAI(api_key=api_key)


def ask_ai(question: str, lang: str = "en") -> Optional[str]:
    """Single-turn ask."""
    client = _get_client()
    if client is None:
        return None

    model = _env("OPENAI_MODEL", "gpt-4o-mini")
    prompt = f"{SYSTEM_PROMPT}\n\n[Language: {lang}]\n\nUser question:\n{question}".strip()

    try:
        resp = client.responses.create(
            model=model,
            input=prompt,
            temperature=0.3,
        )

        out = getattr(resp, "output", None)
        if not out:
            _set_last_error("No output from model")
            return None

        for item in out:
            if getattr(item, "type", None) == "message":
                for c in (getattr(item, "content", None) or []):
                    if getattr(c, "type", None) == "output_text":
                        text = (getattr(c, "text", "") or "").strip()
                        if text:
                            _set_last_error("")
                            return text

        _set_last_error("No output_text content found")
        return None

    except Exception as e:
        msg = str(e).lower()

        if "401" in msg or "unauthorized" in msg or "invalid_api_key" in msg:
            _set_last_error("OpenAI 401 Unauthorized (check OPENAI_API_KEY in Koyeb env vars)")
            return None

        if "429" in msg or "rate limit" in msg or "quota" in msg:
            _set_last_error("OpenAI rate/quota limit reached (429). Try again later.")
            return None

        if "timeout" in msg:
            _set_last_error("OpenAI request timed out. Try again.")
            return None

        _set_last_error(f"OpenAI request failed: {type(e).__name__}")
        return None


def ask_ai_chat(messages: list[dict[str, str]], lang: str = "en") -> Optional[str]:
    """Chat-style AI call. messages: [{role, content}]"""
    client = _get_client()
    if client is None:
        return None

    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    cleaned: list[dict[str, str]] = []
    for m in (messages or []):
        role = (m.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})

    if not cleaned:
        _set_last_error("empty chat")
        return None

    system = SYSTEM_PROMPT
    if lang:
        system = f"{SYSTEM_PROMPT}\n\n[Language: {lang}]"

    input_msgs = [{"role": "system", "content": system}] + cleaned

    try:
        resp = client.responses.create(
            model=model,
            input=input_msgs,
            temperature=0.3,
        )

        out = getattr(resp, "output", None)
        if not out:
            _set_last_error("No output from model")
            return None

        for item in out:
            if getattr(item, "type", None) == "message":
                for c in (getattr(item, "content", None) or []):
                    if getattr(c, "type", None) == "output_text":
                        text = (getattr(c, "text", "") or "").strip()
                        if text:
                            _set_last_error("")
                            return text

        _set_last_error("No output_text content found")
        return None

    except Exception as e:
        msg = str(e).lower()

        if "401" in msg or "unauthorized" in msg or "invalid_api_key" in msg:
            _set_last_error("OpenAI 401 Unauthorized (check OPENAI_API_KEY in Koyeb env vars)")
            return None

        if "429" in msg or "rate limit" in msg or "quota" in msg:
            _set_last_error("OpenAI rate/quota limit reached (429). Try again later.")
            return None

        if "timeout" in msg:
            _set_last_error("OpenAI request timed out. Try again.")
            return None

        _set_last_error(f"OpenAI request failed: {type(e).__name__}")
        return None


# Backward compatibility aliases
def call_ai(question: str, lang: str = "en", channel: str = "web", **kwargs) -> dict:
    """Canonical interface expected by ask_service.py"""
    answer = ask_ai(question, lang)
    if answer:
        return {"ok": True, "answer": answer}
    return {"ok": False, "error": last_ai_error() or "AI call failed"}


def generate_grounded_answer(question: str, context: str = "", lang: str = "en", channel: str = "web") -> str:
    """Generate an answer with optional context grounding"""
    answer = ask_ai(question, lang)
    return answer or "I couldn't process that question right now. Please try again."
