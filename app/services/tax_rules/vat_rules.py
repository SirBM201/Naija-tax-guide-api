from __future__ import annotations

from typing import Optional


def explain_vat_basic() -> str:
    return (
        "VAT in Nigeria stands for Value Added Tax. It is a consumption tax charged on taxable goods and services. "
        "The standard VAT rate in Nigeria is 7.5%. Businesses that make taxable supplies are generally expected to register, charge VAT where applicable, "
        "file returns, and remit collected VAT in line with FIRS requirements."
    )


def explain_vat_registration() -> str:
    return (
        "To register for VAT in Nigeria, the business should first complete its business registration requirements, obtain its tax identification details, "
        "and register with the appropriate tax authority process used for VAT administration. After registration, the business should issue compliant invoices, "
        "charge VAT on taxable supplies where applicable, keep proper records, and file and remit VAT as required."
    )


def can_handle_vat_rule(question: str, topic: str, intent_type: str) -> bool:
    q = (question or "").lower()
    return topic == "vat" and intent_type in {"definition", "how_to"}


def resolve_vat_rule(question: str, intent_type: str) -> Optional[str]:
    if intent_type == "definition":
        return explain_vat_basic()
    if intent_type == "how_to":
        return explain_vat_registration()
    return None
