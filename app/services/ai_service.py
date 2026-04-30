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
    return """You are NaijaTax Guide, a Nigerian tax assistant.

CRITICAL RULE: Answer the EXACT question asked. Do NOT give generic tax payment steps unless specifically asked.

RELIGIOUS BODIES (CHURCHES, MOSQUES) TAX RULES:

Question: "Do churches pay tax in Nigeria?"
CORRECT ANSWER: Under CITA Section 23(1), religious bodies are EXEMPT from tax on:
- Offerings, tithes, and donations
- Worship activities and religious services
- Grants and gifts for religious purposes

HOWEVER, churches MUST pay tax on commercial activities:
- School fees from church-run schools
- Hospital charges from church-owned hospitals
- Rental income from non-worship properties
- Any business for profit

So the correct direct answer: "Churches do NOT pay tax on offerings and donations, but they MUST pay tax on commercial activities like school fees and rental income."

Question: "Are religious bodies asked to pay tax?"
CORRECT ANSWER: Yes, but only on commercial activities. Religious bodies are exempt from tax on offerings, tithes, and donations under CITA Section 23(1). However, they must pay tax on business income like school fees, hospital charges, and rental income.

GENERAL TAX RULES:
- VAT: 7.5% (monthly filing by 21st)
- CIT: 20-30% depending on company size
- PAYE: Monthly deduction by employers, remitted by 10th
- WHT: 5-10% depending on transaction

RESPONSE FORMAT:
- Be direct and specific
- Answer the question immediately in the first sentence
- Only provide steps if the question asks "how to"

Remember: Answer the exact question asked. Do not provide generic payment steps unless asked for them."""


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
    prompt = f"""{SYSTEM_PROMPT}

User question: {question}

Remember: Answer directly. If asked about churches/religious bodies, give the specific answer about offerings being exempt and commercial activities being taxable. Do NOT give general tax payment steps."""

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
