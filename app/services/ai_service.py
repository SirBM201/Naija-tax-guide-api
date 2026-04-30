# app/services/ai_service.py
from __future__ import annotations

"""
AI SERVICE (BOOT-SAFE, CANONICAL EXPORTS)

This file guarantees:
  - call_ai(...) exists (canonical name)
  - call_ai returns a dict: { ok: bool, answer?: str, error?: str }

Provider strategy:
  - If OPENAI_API_KEY exists -> try OpenAI
  - Else -> returns clear error (boot-safe)
"""

import os
from datetime import datetime
from typing import Any, Dict, Optional


# -----------------------------
# Helpers
# -----------------------------
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clip(s: str, n: int = 280) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "..."


def _debug_enabled() -> bool:
    return _truthy(_env("AI_DEBUG", "0")) or _truthy(_env("DEBUG", "0"))


def _dbg(msg: str) -> None:
    if _debug_enabled():
        print(msg, flush=True)


def _get_system_prompt() -> str:
    """Returns the comprehensive system prompt with Nigerian tax knowledge"""
    current_year = datetime.now().strftime('%Y')
    
    return f"""You are Naija Tax Guide, an expert AI tax assistant specializing in Nigerian taxation.

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
- Non-commercial religious income is exempt but should be documented

================================================================================
CURRENT TAX REGIME ({current_year})
================================================================================
VALUE ADDED TAX (VAT):
- Rate: 7.5% (unchanged from recent budgets)
- Registration threshold: ₦25 million annual turnover
- Filing: Monthly, by 21st of following month

COMPANY INCOME TAX (CIT):
- Large companies (gross turnover > ₦100M): 30%
- Medium companies (₦25M - ₦100M): 20%
- Small companies (gross turnover ≤ ₦25M): 0% (exempt)
- Minimum tax: 0.5% of turnover for companies reporting losses
- Filing: Annual, within 6 months of year end

PERSONAL INCOME TAX (PAYE):
- Tax bands (annual income):
  * Up to ₦300,000: 7%
  * ₦300,001 - ₦600,000: 11%
  * ₦600,001 - ₦1,100,000: 15%
  * ₦1,100,001 - ₦1,600,000: 19%
  * ₦1,600,001 - ₦3,200,000: 21%
  * Above ₦3,200,000: 24%
- Consolidated Relief Allowance: ₦200,000 OR 1% of income (whichever higher) + 20% of gross income
- Filing: March 31st annually for self-employed; PAYE deducted monthly by employers

WITHHOLDING TAX (WHT):
- Direct payments: 10% (rent, interest, dividends)
- Contracts: 5% (construction, consultancy)
- Filing: Monthly, by 21st of following month
- WHT is an advance payment of CIT/PIT (not an additional tax)

TERTIARY EDUCATION TAX (EDT):
- Rate: 3% of assessable profits
- Applies to all companies registered in Nigeria
- Filing: Same as CIT (annual)

CAPITAL GAINS TAX (CGT):
- Rate: 10% on capital gains from asset disposal
- Exemptions: Principal residence, agricultural property, government securities

================================================================================
RECENT REFORMS (FINANCE ACTS 2020-2024)
================================================================================
- Digital economy: 6% tax on non-resident digital services (Netflix, Google, Meta)
- Expatriate Employment Levy (EEL) for companies hiring foreign workers
- Minimum tax introduced for loss-making companies (0.5% of turnover)
- Startup incentives: Tax exemption for approved startups (3-5 years)
- Cryptocurrency taxation: Capital gains on digital assets
- VAT on online/digital services from foreign providers
- Non-resident companies providing digital services must register for VAT

================================================================================
TAX FILING DEADLINES
================================================================================
- PAYE: Monthly deduction by employer, remitted by 10th of following month
- Self-assessment (PIT): March 31st annually
- CIT: Within 6 months of accounting year end (max 12 months with extension)
- VAT: Monthly, by 21st of following month
- WHT: Monthly, by 21st of following month
- EDT: Same as CIT deadline

================================================================================
RESPONSE GUIDELINES
================================================================================
1. Be conversational, clear, and helpful
2. Cite specific laws when possible (CITA, PITA, VAT Act, Finance Acts)
3. If a question is ambiguous, ask for clarification
4. Always note that tax laws change and suggest verifying with a tax professional
5. For specific personal/corporate tax situations, give general principles only (no binding advice)
6. Be empathetic - tax can be confusing. Guide users step by step
7. Use examples to illustrate complex concepts

================================================================================
COMMON QUESTION PATTERNS
================================================================================
- "Do churches pay tax?" → Explain exemption vs commercial activities
- "What is PAYE?" → Explain Pay As You Earn system
- "When is VAT due?" → Monthly by 21st
- "How to register for tax?" → Step by step process
- "What's new in tax regime?" → List Finance Act changes

Current year: {current_year}

Answer every tax question accurately, conversationally, and helpfully. You are helping Nigerians understand their tax obligations better."""


# -----------------------------
# Canonical API
# -----------------------------
def call_ai(
    *,
    question: str,
    lang: str = "en",
    channel: str = "web",
    system_prompt: Optional[str] = None,
    max_tokens: int = 700,
) -> Dict[str, Any]:
    """
    Canonical function for AI calls.
    
    Returns:
      { ok: True, answer: "..." }
      { ok: False, error: "...", root_cause: "...", fix: "..." }
    """
    q = (question or "").strip()
    if not q:
        return {
            "ok": False,
            "error": "question_required",
            "root_cause": "question_empty",
            "fix": "Pass a non-empty question string to call_ai(question=...).",
        }

    # Choose provider
    if _env("OPENAI_API_KEY", ""):
        return _call_openai(
            question=q,
            lang=lang,
            channel=channel,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        )

    # No provider configured -> boot-safe error
    return {
        "ok": False,
        "error": "ai_not_configured",
        "root_cause": "No AI provider API key is configured on the backend.",
        "fix": (
            "Set one provider key in your backend env. "
            "For OpenAI set OPENAI_API_KEY (and optionally OPENAI_MODEL)."
        ),
        "details": {
            "expected_env": ["OPENAI_API_KEY", "OPENAI_MODEL(optional)"],
            "lang": lang,
            "channel": channel,
        },
    }


# Backwards compatibility aliases
def ask_ai(*args, **kwargs) -> Dict[str, Any]:
    """Alias for older code paths."""
    return call_ai(*args, **kwargs)


def generate_ai_answer(*args, **kwargs) -> Dict[str, Any]:
    """Alias for older code paths."""
    return call_ai(*args, **kwargs)


def generate_grounded_answer(
    question: str,
    context: str = "",
    lang: str = "en",
    channel: str = "web",
) -> str:
    """
    Generate an answer with optional context grounding.
    Returns the answer string directly.
    """
    system_prompt = _get_system_prompt()
    
    if context:
        system_prompt = f"{system_prompt}\n\nRELEVANT CONTEXT FROM TAX KNOWLEDGE BASE:\n{context}\n\nUse this context to inform your answer when relevant."
    
    result = call_ai(
        question=question,
        lang=lang,
        channel=channel,
        system_prompt=system_prompt,
    )
    
    if result.get("ok"):
        return result.get("answer", "")
    
    return "I couldn't process that question right now. Please try again or rephrase."


# -----------------------------
# OpenAI implementation
# -----------------------------
def _call_openai(
    *,
    question: str,
    lang: str,
    channel: str,
    system_prompt: Optional[str],
    max_tokens: int,
) -> Dict[str, Any]:
    """
    Uses OpenAI if the SDK is installed.
    """
    api_key = _env("OPENAI_API_KEY", "")
    model = _env("OPENAI_MODEL", _env("AI_MODEL", "gpt-4o-mini"))
    
    if not api_key:
        return {
            "ok": False,
            "error": "openai_missing_key",
            "root_cause": "OPENAI_API_KEY is empty",
            "fix": "Set OPENAI_API_KEY in backend environment variables.",
        }

    # Import SDK safely
    try:
        from openai import OpenAI
    except Exception as e:
        return {
            "ok": False,
            "error": "openai_sdk_missing",
            "root_cause": f"OpenAI SDK import failed: {type(e).__name__}: {_clip(str(e))}",
            "fix": "Add openai to requirements.txt (pip install openai)",
        }

    try:
        client = OpenAI(api_key=api_key)

        sys = system_prompt or _get_system_prompt()

        # Try Responses API first (newer), fallback to ChatCompletions
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": sys},
                    {"role": "user", "content": question},
                ],
                max_output_tokens=int(max_tokens or 700),
            )
            answer = getattr(resp, "output_text", None)
            if not answer:
                answer = str(resp)
        except Exception:
            # Fallback to ChatCompletions
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys},
                    {"role": "user", "content": question},
                ],
                max_tokens=int(max_tokens or 700),
            )
            answer = (resp.choices[0].message.content or "").strip()

        answer = (answer or "").strip()
        if not answer:
            return {
                "ok": False,
                "error": "openai_empty_answer",
                "root_cause": "OpenAI returned empty content.",
                "fix": "Check provider status and model name.",
                "details": {"model": model, "lang": lang, "channel": channel},
            }

        return {"ok": True, "answer": answer, "provider": "openai", "model": model}

    except Exception as e:
        return {
            "ok": False,
            "error": "openai_call_failed",
            "root_cause": f"{type(e).__name__}: {_clip(str(e))}",
            "fix": "Check OPENAI_API_KEY, model name, outbound network access, and OpenAI account status.",
            "details": {"model": model, "lang": lang, "channel": channel},
        }
