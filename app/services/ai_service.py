from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(*names: str, default: str = "") -> str:
    for name in names:
        v = os.getenv(name)
        if v and str(v).strip():
            return str(v).strip()
    return default


def _get_model() -> str:
    return _env(
        "OPENAI_MODEL",
        "OPENAI_CHAT_MODEL",
        "AI_MODEL",
        default="gpt-4o-mini",
    )


def _get_api_key() -> str:
    return _env(
        "OPENAI_API_KEY",
        "AI_API_KEY",
        default="",
    )


def _build_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("openai_sdk_missing")
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("openai_api_key_not_set")
    return OpenAI(api_key=api_key)


def _call_openai_chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> str:
    client = _build_client()
    model = _get_model()

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = response.choices[0].message.content or ""
    if not text:
        raise RuntimeError("openai_empty_answer")
    return text


def call_ai(
    question: str,
    lang: str = "en",
    channel: str = "web",
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    question = _safe_str(question)
    if not question:
        return {
            "ok": False,
            "error": "question_required",
            "root_cause": "missing_question",
            "fix": "Provide a non-empty question.",
        }

    prompt = system_prompt or (
        "You are Naija Tax Guide, a Nigerian tax assistant. "
        "You can ONLY answer questions about Nigerian tax. "
        "If the user asks anything not related to Nigerian tax, politely explain that you are designed exclusively for Nigerian tax questions and ask them to ask a tax‑related question. "
        "Do not attempt to answer non‑tax questions. "
        "Be practical, direct, and accurate. Do not invent legal citations, deadlines, rates, penalties, or procedures. If unsure, say so."
    )

    try:
        answer = _call_openai_chat(system_prompt=prompt, user_prompt=question, temperature=0.2)
        return {
            "ok": True,
            "answer": answer,
            "provider": "openai",
            "model": _get_model(),
            "lang": lang,
            "channel": channel,
        }
    except Exception as e:
        err = str(e)
        fix = "Check OPENAI_API_KEY and OpenAI package installation."
        if "openai_api_key_not_set" in err:
            fix = "Set OPENAI_API_KEY in your backend environment."
        elif "openai_sdk_missing" in err:
            fix = "Add the OpenAI SDK to requirements.txt and redeploy."
        elif "empty_answer" in err:
            fix = "Inspect provider response and retry."
        elif "authentication" in err.lower() or "api key" in err.lower():
            fix = "Verify the OpenAI API key is valid."
        elif "rate" in err.lower() and "limit" in err.lower():
            fix = "Check provider quota and rate limits."

        return {
            "ok": False,
            "error": "ai_failed",
            "root_cause": err,
            "fix": fix,
        }


def generate_grounded_answer(
    question: str,
    context: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
) -> str:
    """
    Simplified version that matches the call from ask_service.py.
    Returns an empty string on failure (caller will fall back).
    """
    if not _truthy(os.getenv("USE_LIVE_GROUNDED_AI")):
        return ""
    if not _get_api_key():
        return ""

    system_prompt = (
        "You are Naija Tax Guide, a grounded Nigerian tax assistant. "
        "Answer only within Nigerian tax context. "
        "If the question is not about Nigerian tax, politely explain that you only answer tax questions. "
        "Be concise and practical. Do not invent information."
    )

    user_prompt = f"Question: {question}\n\n"
    if context:
        user_prompt += f"Context: {context}\n\n"
    user_prompt += "Provide a helpful answer."

    try:
        return _call_openai_chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.2)
    except Exception:
        return ""
