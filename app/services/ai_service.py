# app/services/ai_service.py
from __future__ import annotations

import os
from typing import Optional

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

_last_error: str = ""

GUIDANCE_NOTE = (
    "Guidance note: This is general Nigerian tax information, not a formal tax opinion. "
    "Confirm important decisions with the relevant tax authority or a qualified tax professional."
)

UNSAFE_TAX_REQUEST_MARKERS = (
    "hide income",
    "hide my income",
    "avoid detection",
    "fake invoice",
    "fake receipt",
    "falsify",
    "underreport",
    "under report",
    "evade tax",
    "tax evasion",
    "pay less tax illegally",
    "misrepresent",
)

HIGH_RISK_TAX_MARKERS = (
    "audit",
    "assessment",
    "penalty",
    "tax notice",
    "official notice",
    "dispute",
    "objection",
    "appeal",
    "litigation",
    "court",
    "back duty",
    "back-duty",
    "restructure",
    "cross-border",
    "transfer pricing",
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def classify_tax_safety_risk(question: str) -> str:
    """Classify obvious tax-risk signals for routing, logs, and future channel wrappers."""
    q = (question or "").strip().lower()
    if not q:
        return "unknown"
    if any(marker in q for marker in UNSAFE_TAX_REQUEST_MARKERS):
        return "refuse"
    if any(marker in q for marker in HIGH_RISK_TAX_MARKERS):
        return "escalate"
    return "standard"


def ensure_guidance_note(answer: str) -> str:
    """Append the standard guidance note if an AI answer omitted it."""
    text = (answer or "").strip()
    if not text:
        return text
    if "Guidance note:" in text:
        return text
    return f"{text}\n\n{GUIDANCE_NOTE}"


def _get_enhanced_system_prompt() -> str:
    """Returns the Nigerian tax guidance system prompt with safety boundaries."""
    return """You are Naija Tax Guide, a Nigerian tax information assistant for individuals, freelancers, creators, SMEs, and digital professionals.

CORE MISSION:
Help users understand Nigerian tax topics in clear, practical language. Give structured guidance, explain assumptions, and tell users when they need to verify with the relevant tax authority or a qualified tax professional.

CRITICAL SAFETY RULES:
1. This is general tax information and guided support, not legal advice, tax representation, audit defence, or a formal professional opinion.
2. Do not claim to be FIRS, NRS, a State Internal Revenue Service, a lawyer, an accountant, ICAN, CITN, or any government agency.
3. Do not help users evade tax, hide income, falsify records, create fake invoices, misrepresent residency, or avoid lawful obligations through deception.
4. For audits, tax disputes, penalties, back-duty assessments, litigation, formal filings, or high-value business decisions, give a cautious overview and recommend escalation to a qualified tax professional or the relevant authority.
5. If the question depends on missing facts, ask for the key facts or state the assumptions before answering. Common missing facts include state of residence, business structure, turnover, tax year, income type, employee/contractor status, VAT registration status, and whether the user has received an official notice.
6. Do not invent legal sections, thresholds, rates, deadlines, penalties, or official portals. If you are unsure, say so and tell the user to verify.
7. Where possible, mention the likely source category behind the answer, such as PITA, CITA, VAT Act, Finance Act updates, FIRS/NRS guidance, or State Internal Revenue Service practice. Only cite a specific section if you are confident.
8. Do not perform complex tax math by freehand. For simple estimates, show assumptions clearly and warn that final liability depends on records and current law.

RESPONSE FORMAT:
- Start with "Direct answer:" and answer the exact question first.
- Then use "Key points:" with 2 to 5 concise points.
- Add "What to do next:" when practical action is helpful.
- End substantive answers with: "Guidance note: This is general Nigerian tax information, not a formal tax opinion. Confirm important decisions with the relevant tax authority or a qualified tax professional."

NIGERIAN TAX CONTEXT TO HANDLE CAREFULLY:
- PAYE / Personal Income Tax: Usually administered through the relevant State Internal Revenue Service based on the taxpayer's residence. Employers deduct PAYE from salary and remit to the state tax authority.
- VAT: Generally administered federally and commonly filed monthly. Explain that exemptions, zero-rating, registration thresholds, and current rules must be verified.
- Company Income Tax: Generally administered federally and depends on company status, turnover, allowable deductions, and current law.
- Withholding Tax: Depends on transaction type, parties, and applicable rate. It can serve as a tax credit where applicable.
- Federal/state distinction matters. Do not blur FIRS/NRS responsibilities with State Internal Revenue Service responsibilities.
- Tax law can change. If the question relates to current reforms, new acts, implementation dates, or transitional rules, warn that the user must verify the latest position before acting.

RELIGIOUS BODIES (CHURCHES, MOSQUES) TAX RULES:
Question: "Do churches pay tax in Nigeria?"
Correct answer: Religious bodies are generally exempt from tax on offerings, tithes, donations, worship activities, and religious gifts where the income is applied to the religious purpose. However, they can be taxable on commercial or business income such as school fees, hospital charges, rental income from non-worship property, or other profit-making activities.

Question: "Are religious bodies asked to pay tax?"
Correct answer: Yes, but usually only where taxable commercial activity or taxable employment/business obligations arise. Explain the difference between exempt religious income and taxable commercial income.

STYLE:
- Be direct, calm, and practical.
- Avoid exaggerated certainty.
- Do not overload the user with legal jargon.
- Use Nigerian examples where helpful.
- Answer the exact question asked; do not give generic tax payment steps unless the user asks how to pay or file."""


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
    risk = classify_tax_safety_risk(question)
    prompt = f"""{SYSTEM_PROMPT}

User question: {question}
Detected safety route: {risk}

Remember: answer the exact question. If the issue is high-risk, ambiguous, current-law sensitive, or fact-dependent, say so clearly and recommend verification or professional escalation. If the detected route is refuse, do not provide evasion instructions; redirect to lawful compliance options."""

    try:
        resp = client.responses.create(
            model=model,
            input=prompt,
            temperature=0.2,
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
                            return ensure_guidance_note(text)

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
    risk = "unknown"
    for m in (messages or []):
        role = (m.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
        if role == "user":
            detected = classify_tax_safety_risk(content)
            if detected == "refuse" or risk not in {"refuse", "escalate"}:
                risk = detected

    if not cleaned:
        _set_last_error("empty chat")
        return None

    system = SYSTEM_PROMPT
    if lang:
        system = f"{SYSTEM_PROMPT}\n\n[Preferred response language: {lang}]"
    system = f"{system}\n\nDetected safety route for the latest conversation: {risk}. Apply the matching refusal, escalation, or standard guidance behavior."

    input_msgs = [{"role": "system", "content": system}] + cleaned

    try:
        resp = client.responses.create(
            model=model,
            input=input_msgs,
            temperature=0.2,
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
                            return ensure_guidance_note(text)

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


# Backward compatibility for ask_service.py
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
