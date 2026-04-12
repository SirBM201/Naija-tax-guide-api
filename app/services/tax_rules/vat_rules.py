from __future__ import annotations

import re
from typing import Optional


CURRENT_VAT_SOURCE = (
    "Source: current official Nigeria Revenue Service guidance, "
    "the Nigeria Tax Act 2025 framework, and the official VAT registration, filing, and payment channels of the relevant tax authority."
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
            "Ask how to register for VAT if the activity falls within the VAT rules.",
            "Ask how to file or pay VAT after confirming that VAT applies.",
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
            "Ask what records should support the VAT payment and return.",
            "Ask whether the exact supply is taxable, exempt, or zero-rated before charging VAT next time.",
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


def explain_zero_rated_vat() -> str:
    return _render_structured(
        body_lines=[
            "Zero-rated VAT means the supply falls within the current zero-rating treatment under the applicable VAT rule, so it is not treated in the same way as a standard-rated supply.",
            "",
            "What this means:",
            "- A zero-rated supply is not the same thing as a supply that is exempt from VAT.",
            "- The taxpayer should first confirm that the exact supply is listed under the current zero-rating treatment before applying that classification.",
            "",
            "Practical rule:",
            "- Do not treat a supply as zero-rated just because no VAT was charged in practice.",
            "- Check the current official legal list or authority guidance for the exact good or service before invoicing or filing.",
        ],
        next_steps=[
            "Ask whether your exact good or service is zero-rated under the current rule.",
            "Ask how the supply should be shown in VAT filing after classification.",
            "Ask how zero-rated treatment differs from VAT exemption in your case.",
        ],
        source_line=CURRENT_VAT_SOURCE,
    )


def explain_vat_exemption() -> str:
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


_BASIC_HINTS = (
    "what is vat",
    "define vat",
    "meaning of vat",
    "what does vat mean",
    "what is value added tax",
)

_OBLIGATION_HINTS = (
    "who must comply with vat",
    "who pays vat",
    "who should charge vat",
    "who should comply with vat",
    "does vat apply",
    "must i charge vat",
)

_REGISTRATION_HINTS = (
    "how do i register for vat",
    "how to register for vat",
    "register for vat",
    "vat registration",
)

_FILING_HINTS = (
    "how do i file vat",
    "how to file vat",
    "file vat",
    "vat filing",
)

_PAYMENT_HINTS = (
    "how do i pay vat",
    "how to pay vat",
    "pay vat",
    "vat payment",
)

_RECORDS_HINTS = (
    "what records should i keep for vat",
    "vat records",
    "records for vat",
)

_ZERO_RATED_HINTS = (
    "what is zero rated vat",
    "what is zero rated",
    "what is zero rated supply",
    "zero rated vat",
    "zero rated supplies",
    "zero rated supply",
    "zero rated",
    "zero rated vat",
)

_EXEMPTION_HINTS = (
    "what is vat exemption",
    "vat exemption",
    "vat exempt",
    "what is vat exempt",
    "exempt from vat",
)


def can_handle_vat_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    vat_context = topic_key in {"vat", "value added tax", "value_added_tax"} or "vat" in q or "value added tax" in q
    if not vat_context:
        vat_context = any(
            h in q
            for h in _BASIC_HINTS
            + _OBLIGATION_HINTS
            + _REGISTRATION_HINTS
            + _FILING_HINTS
            + _PAYMENT_HINTS
            + _RECORDS_HINTS
            + _ZERO_RATED_HINTS
            + _EXEMPTION_HINTS
        )

    if not vat_context:
        return False

    if intent_key in {"definition", "obligation", "registration", "filing", "payment", "records", "exemption"}:
        return True

    return True


def resolve_vat_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "records" or any(h in q for h in _RECORDS_HINTS):
        return explain_vat_records()

    if intent_key == "payment" or any(h in q for h in _PAYMENT_HINTS):
        return explain_vat_payment()

    if intent_key == "filing" or any(h in q for h in _FILING_HINTS):
        return explain_vat_filing()

    if intent_key in {"registration", "procedure"} or any(h in q for h in _REGISTRATION_HINTS):
        return explain_vat_registration()

    if any(h in q for h in _ZERO_RATED_HINTS):
        return explain_zero_rated_vat()

    if intent_key == "exemption" or any(h in q for h in _EXEMPTION_HINTS):
        return explain_vat_exemption()

    if intent_key == "obligation" or any(h in q for h in _OBLIGATION_HINTS):
        return explain_vat_obligation()

    if intent_key == "definition" or any(h in q for h in _BASIC_HINTS):
        return explain_vat_basic()

    return explain_vat_basic()
