# app/services/tax_intent_service.py
from __future__ import annotations

import re
from typing import Dict, Optional

INTENT_KEYWORDS = {
    "tax_payment_process": [
        "pay tax",
        "how do i pay tax",
        "how to pay tax",
        "tax payment",
        "remita tax",
    ],
    "tin_registration": [
        "tin",
        "tax identification number",
        "register tin",
        "how to get tin",
    ],
    "vat_definition": [
        "what is vat",
        "define vat",
    ],
    "vat_rate": [
        "vat rate",
        "how much is vat",
        "vat percentage",
    ],
    "paye_definition": [
        "what is paye",
        "define paye",
    ],
    "freelancer_tax_obligation": [
        "freelancer tax",
        "do freelancers pay tax",
        "self employed tax nigeria",
    ],
    "record_keeping": [
        "keep records",
        "tax records",
        "accounting records",
    ],
}

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def classify_tax_intent(question: str) -> Optional[str]:
    q = _normalize(question)

    for intent, phrases in INTENT_KEYWORDS.items():
        for phrase in phrases:
            if phrase in q:
                return intent

    return None


def build_intent_meta(intent: Optional[str]) -> Dict:
    return {
        "intent_type": intent,
        "grounded": bool(intent),
    }
