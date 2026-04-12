from __future__ import annotations

import re
from typing import Optional


CURRENT_VAT_SOURCE = (
    "Source: current official Nigeria Revenue Service guidance, the Nigeria Tax Act 2025 framework, "
    "and the official VAT registration, filing, and payment channels of the relevant tax authority."
)


def _normalize(text: str) -> str:
    value = (text or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _render_structured(*, body_lines: list[str], next_steps: list[str], source_line: str) -> str:
    lines: list[str] = []
    lines.extend(body_lines)
    lines.append("")
    lines.append("What to do next:")
    for idx, item in enumerate(next_steps, start=1):
        lines.append(f"{idx}. {item}")
    lines.append("")
    lines.append(source_line)
    return "\n".join(lines).strip()


def explain_vat_basic() -> str:
    return _render_structured(
        body_lines=[
            "VAT in Nigeria is a consumption tax that generally applies to taxable supplies of goods and services.",
            "",
            "What this means:",
            "- A business that makes taxable supplies generally has to deal with VAT registration, charging VAT where applicable, filing returns, and remitting the tax through the official channel.",
            "- VAT questions should usually be separated into: whether VAT applies, whether the supply may be exempt or zero-rated, how to register, how to file, and how to pay.",
            "",
            "Current practical point:",
            "- Use the current official NRS/FIRS channel and the latest applicable VAT rules before relying on a rate, exemption, or filing step.",
        ],
        next_steps=[
            "Ask who must charge or comply with VAT in your situation.",
            "Ask whether the supply may be exempt or zero-rated.",
            "Ask for the VAT registration, filing, or payment steps.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_rate() -> str:
    return _render_structured(
        body_lines=[
            "The standard VAT rate in Nigeria is 7.5% under the current federal VAT framework.",
            "",
            "Important note:",
            "- Do not apply the standard rate automatically if the supply may be exempt, zero-rated, outside scope, or otherwise treated differently under the current law.",
            "- For production use, always confirm the current treatment of the exact supply on the official authority channel or the latest law in force.",
        ],
        next_steps=[
            "Ask whether the exact supply is taxable, exempt, or zero-rated.",
            "Ask who is expected to charge VAT in your situation.",
            "Ask how to file or pay VAT after charging it.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_obligation() -> str:
    return _render_structured(
        body_lines=[
            "A person or business generally has to comply with VAT when it makes taxable supplies that fall within the current Nigerian VAT rules.",
            "",
            "Who this usually affects:",
            "- businesses making taxable supplies of goods or services",
            "- businesses that should register, charge VAT where applicable, file returns, and remit VAT through the official channel",
            "",
            "Important limits:",
            "- Not every supply should be charged at the standard VAT rate.",
            "- Some supplies may be exempt or zero-rated under the current law.",
            "- The correct answer depends on the exact supply, the nature of the taxpayer, and the current legal treatment of that transaction.",
            "",
            "Practical rule:",
            "- First confirm whether the exact good or service is taxable. If it is taxable, move to registration, invoicing, filing, and remittance compliance.",
        ],
        next_steps=[
            "Ask whether your exact business activity or transaction is taxable for VAT.",
            "Ask whether the supply may be exempt or zero-rated.",
            "Ask how to register, file, or pay VAT once VAT applies.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_exemption_zero_rated() -> str:
    return _render_structured(
        body_lines=[
            "Exempt supplies and zero-rated supplies are not treated the same for VAT, so you should confirm the exact category before charging VAT or filing the return.",
            "",
            "What this means:",
            "- If a supply is exempt, the VAT treatment is different from a supply that is zero-rated.",
            "- You should not assume that a supply is exempt or zero-rated just because VAT is not visibly charged in practice.",
            "",
            "Practical rule:",
            "- Check the current official schedule or legal list for the exact good or service involved.",
            "- If the supply is not clearly listed under the current exemption or zero-rating treatment, do not guess. Confirm with the current official authority guidance before invoicing or filing.",
        ],
        next_steps=[
            "Ask about the exact good or service you want to classify for VAT.",
            "Ask whether the standard VAT rate should be charged on that supply.",
            "Ask how the supply should be treated in VAT filing after classification.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_registration() -> str:
    return _render_structured(
        body_lines=[
            "Register for VAT through the approved registration channel of the relevant federal tax authority once your business falls within the scope of VAT registration.",
            "",
            "Before registration:",
            "- Confirm that the business activity falls within the applicable VAT registration rules.",
            "- Prepare the business details and TIN required for registration.",
            "",
            "Registration steps:",
            "1. Provide the required taxpayer and business information accurately.",
            "2. Complete any activation or confirmation step required by the authority.",
            "3. Keep the acknowledgement and any confirmation notice or certificate issued.",
            "",
            "After registration:",
            "- Make sure your invoicing, record-keeping, filing, and payment process are aligned with VAT compliance.",
        ],
        next_steps=[
            "Ask whether your business must charge VAT.",
            "Ask how to file VAT after registration.",
            "Ask what invoices and records should support VAT compliance.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_filing() -> str:
    return _render_structured(
        body_lines=[
            "File VAT through the approved VAT filing channel for the relevant tax authority and filing period.",
            "",
            "Before filing:",
            "- Confirm the VAT period involved.",
            "- Gather the records for taxable sales, output VAT, input VAT where relevant, invoices, and supporting schedules.",
            "- Reconcile the figures so the return matches your records.",
            "",
            "Filing steps:",
            "1. Submit the VAT return through the approved channel within the applicable deadline.",
            "2. Where VAT is payable, complete payment through the approved payment channel.",
            "3. Keep both the return evidence and payment evidence for your records.",
        ],
        next_steps=[
            "Ask whether VAT applies to your business or transaction first.",
            "Ask how to register for VAT if you are not yet registered.",
            "Ask what records you should keep for VAT compliance.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_payment() -> str:
    return _render_structured(
        body_lines=[
            "Pay VAT through the approved VAT payment channel of the federal tax authority that receives the return.",
            "",
            "Before payment:",
            "- Confirm the VAT period and amount due from the return or assessment.",
            "- Make sure the taxpayer profile, TIN, and VAT return details match the correct business.",
            "- Generate or confirm the payment reference required by the official portal or payment channel.",
            "",
            "Payment steps:",
            "1. Use the approved VAT payment channel accepted by the relevant authority.",
            "2. Pay the exact VAT amount due for the relevant period.",
            "3. Keep the receipt, acknowledgement, or payment confirmation.",
            "",
            "After payment:",
            "- Keep the payment evidence together with the VAT return evidence for that period.",
            "- If the portal still shows unpaid status, confirm whether the payment has posted correctly before assuming there is a failure.",
        ],
        next_steps=[
            "Ask how to file VAT if the return has not yet been submitted.",
            "Ask what records should support the VAT payment.",
            "Ask whether the exact supply is taxable, exempt, or zero-rated.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_records() -> str:
    return _render_structured(
        body_lines=[
            "Keep the sales, invoice, tax-computation, filing, and payment records that support your VAT position for each relevant period.",
            "",
            "Records you should normally keep:",
            "- sales records and transaction schedules for taxable, exempt, or zero-rated supplies",
            "- tax invoices and any supporting commercial documents tied to the supply",
            "- VAT computation schedules showing output VAT, input VAT where relevant, and the amount reported",
            "- filed VAT return, acknowledgement, or portal confirmation",
            "- payment receipt or other official evidence supporting the VAT settlement for the same period",
            "",
            "Practical rule:",
            "- Keep records in a form that lets you trace the underlying transaction, the VAT treatment applied, the return filed, and any payment made for that same period.",
            "- Where the taxpayer treats a supply as exempt or zero-rated, keep the records that support that treatment.",
        ],
        next_steps=[
            "Ask how to file VAT for the relevant period.",
            "Ask how to pay VAT once the amount due is confirmed.",
            "Ask whether the exact supply is taxable, exempt, or zero-rated.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


_OBLIGATION_HINTS = (
    "who must",
    "must charge",
    "must comply",
    "am i required",
    "who should charge",
    "who needs to register",
    "who is required",
    "does my business need",
)

_EXEMPTION_HINTS = (
    "exempt",
    "exemption",
    "zero rated",
    "zero-rated",
    "zero rated supplies",
    "zero-rated supplies",
    "outside scope",
)

_RATE_HINTS = (
    "rate",
    "percentage",
    "7 5",
    "7.5",
)

_DEFINITION_HINTS = (
    "what is vat",
    "meaning of vat",
    "define vat",
    "what does vat mean",
)

_REGISTRATION_HINTS = (
    "register for vat",
    "vat registration",
    "how do i register for vat",
    "how to register for vat",
)

_FILING_HINTS = (
    "file vat",
    "filing vat",
    "vat return",
    "submit vat",
    "how do i file vat",
    "how to file vat",
)

_PAYMENT_HINTS = (
    "pay vat",
    "payment of vat",
    "remit vat",
    "vat payment",
    "how do i pay vat",
    "how to pay vat",
)

_RECORDS_HINTS = (
    "what records should i keep for vat",
    "vat records",
    "records for vat",
    "vat documentation",
    "vat evidence",
    "what should i keep for vat",
)


def can_handle_vat_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    vat_context = topic_key == "vat" or " vat " in f" {q} " or "value added tax" in q
    if not vat_context:
        return False

    if intent_key in {"definition", "obligation", "exemption", "rate", "registration", "filing", "payment", "records", "procedure"}:
        return True

    return any(
        hint in q
        for hint in (
            _OBLIGATION_HINTS
            + _EXEMPTION_HINTS
            + _RATE_HINTS
            + _DEFINITION_HINTS
            + _REGISTRATION_HINTS
            + _FILING_HINTS
            + _PAYMENT_HINTS
            + _RECORDS_HINTS
        )
    )


def resolve_vat_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "records" or any(hint in q for hint in _RECORDS_HINTS):
        return explain_vat_records()

    if intent_key in {"payment", "procedure"} and any(hint in q for hint in _PAYMENT_HINTS):
        return explain_vat_payment()
    if intent_key == "payment" or any(hint in q for hint in _PAYMENT_HINTS):
        return explain_vat_payment()

    if intent_key in {"filing", "procedure"} and any(hint in q for hint in _FILING_HINTS):
        return explain_vat_filing()
    if intent_key == "filing" or any(hint in q for hint in _FILING_HINTS):
        return explain_vat_filing()

    if intent_key in {"registration", "procedure"} and any(hint in q for hint in _REGISTRATION_HINTS):
        return explain_vat_registration()
    if intent_key == "registration" or any(hint in q for hint in _REGISTRATION_HINTS):
        return explain_vat_registration()

    if intent_key == "obligation" or any(hint in q for hint in _OBLIGATION_HINTS):
        return explain_vat_obligation()

    if intent_key == "exemption" or any(hint in q for hint in _EXEMPTION_HINTS):
        return explain_vat_exemption_zero_rated()

    if intent_key == "rate" or any(hint in q for hint in _RATE_HINTS):
        return explain_vat_rate()

    if intent_key == "definition" or any(hint in q for hint in _DEFINITION_HINTS):
        return explain_vat_basic()

    return None
