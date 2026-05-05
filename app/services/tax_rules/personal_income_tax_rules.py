from __future__ import annotations

import re
from typing import Optional


CURRENT_PIT_SOURCE = (
    "Source: current official State Internal Revenue Service personal-income-tax guidance, "
    "the current Personal Income Tax framework in force, and the approved state filing and payment channels."
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


def explain_personal_income_tax_basic() -> str:
    return _render_structured(
        body_lines=[
            "Personal Income Tax in Nigeria is the tax charged on the income of an individual under the applicable personal-income-tax rules.",
            "",
            "What it is:",
            "- Personal Income Tax is an individual's income tax, not a company profit tax.",
            "- It may be handled through PAYE where the income is employment income, or through the individual's direct filing path where that is the applicable route.",
            "- The correct treatment depends on the income type, the taxpayer's status, and the current rule that applies.",
            "",
            "Practical rule:",
            "- First confirm that the issue is about an individual's income and not VAT, Company Income Tax, or Withholding Tax.",
            "- Then confirm whether the case should be handled under PAYE or another personal-income-tax path.",
        ],
        next_steps=[
            "Ask who pays Personal Income Tax in your case.",
            "Ask what authority handles Personal Income Tax for the taxpayer involved.",
            "Ask how to file or pay Personal Income Tax where applicable.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


def explain_personal_income_tax_obligation() -> str:
    return _render_structured(
        body_lines=[
            "Individuals with income that falls within the applicable Personal Income Tax rules are the ones expected to comply with Personal Income Tax in Nigeria.",
            "",
            "Who this usually affects:",
            "- individuals earning taxable income under the applicable personal-income-tax rules",
            "- employers where the income is handled through PAYE deduction for employees",
            "- individuals who may need to file directly where their tax position is not handled only through payroll deduction",
            "",
            "Practical rule:",
            "- Do not assume every income question should be treated as PAYE.",
            "- First identify the income type and the taxpayer context, then confirm whether the compliance route is PAYE, direct personal-income-tax filing, or another lawful path.",
        ],
        next_steps=[
            "Ask whether the issue is about PAYE or direct personal-income-tax filing.",
            "Ask which state tax authority should handle the case.",
            "Ask what records should support the Personal Income Tax position.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


def explain_personal_income_tax_rate() -> str:
    return _render_structured(
        body_lines=[
            "Personal Income Tax in Nigeria is not one flat rate that should be quoted blindly for every individual.",
            "",
            "What this means:",
            "- The correct Personal Income Tax result comes from applying the current personal-income-tax bands or rate rules to the individual's chargeable income under the applicable framework.",
            "- Reliefs, deductions, exemptions, payroll treatment, and the taxpayer's income profile can affect the final amount payable.",
            "",
            "Practical rule:",
            "- Do not guess the tax from salary alone and do not assume that one quick percentage will fit every case.",
            "- First confirm the income type, the chargeable-income position, and the current rule that applies before quoting or computing the liability.",
        ],
        next_steps=[
            "Ask how to compute Personal Income Tax for the income involved.",
            "Ask whether the case should be handled through PAYE.",
            "Ask what records should support the Personal Income Tax computation.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


def explain_personal_income_tax_filing() -> str:
    return _render_structured(
        body_lines=[
            "File Personal Income Tax through the approved channel of the State Internal Revenue Service that has the taxing right in the case.",
            "",
            "Before filing:",
            "- Confirm that the issue is a Personal Income Tax matter and not Company Income Tax or VAT.",
            "- Confirm the correct state authority and the filing period involved.",
            "- Gather the income details, computation support, and any records required for the filing.",
            "",
            "Filing steps:",
            "1. Use the approved state filing portal or filing channel for the taxpayer's case.",
            "2. Complete the return or filing process with the correct taxpayer details and figures.",
            "3. Submit the filing within the applicable deadline.",
            "4. Keep the acknowledgement, confirmation page, or filing receipt.",
        ],
        next_steps=[
            "Ask how to pay Personal Income Tax after filing.",
            "Ask what records should be kept for Personal Income Tax.",
            "Ask whether the case should instead be handled through PAYE.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


def explain_personal_income_tax_payment() -> str:
    return _render_structured(
        body_lines=[
            "Pay Personal Income Tax through the approved payment channel of the State Internal Revenue Service that receives the tax in the case.",
            "",
            "Before payment:",
            "- Confirm the correct state authority, taxpayer details, and period involved.",
            "- Make sure the amount being paid matches the return, assessment, or lawful computation.",
            "- Generate or confirm any payment reference required by the official channel.",
            "",
            "Payment steps:",
            "1. Use the approved state portal, bank channel, or payment platform accepted by that authority.",
            "2. Pay the correct amount due for the relevant period or assessment.",
            "3. Keep the receipt, acknowledgement, or payment confirmation.",
            "",
            "After payment:",
            "- Match the payment evidence to the related filing, assessment, or tax record for the same period.",
        ],
        next_steps=[
            "Ask how to file Personal Income Tax if the filing has not yet been completed.",
            "Ask what records should support the Personal Income Tax payment.",
            "Ask which state tax authority should receive the tax in your case.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


def explain_personal_income_tax_records() -> str:
    return _render_structured(
        body_lines=[
            "Keep the income, computation, filing, and payment records that support the Personal Income Tax position for the period involved.",
            "",
            "Records you should normally keep:",
            "- income records, pay statements, or other source records supporting the income reported",
            "- computation schedules or working papers supporting the tax position",
            "- filed return, acknowledgement, or portal confirmation where a filing was made",
            "- payment receipt, assessment notice, or other official evidence supporting the payment where applicable",
            "- any state-authority correspondence or supporting record tied to that same tax period",
            "",
            "Practical rule:",
            "- Keep records in a form that lets you trace the income, the tax computation, the filing made, and any payment or assessment tied to the same period.",
            "- If part of the issue is handled through PAYE, keep the payroll-side records together with the broader tax record where relevant.",
        ],
        next_steps=[
            "Ask how to file Personal Income Tax for the period involved.",
            "Ask how to pay Personal Income Tax once the amount due is confirmed.",
            "Ask whether part of the case should be handled through PAYE.",
        ],
        source_line=CURRENT_PIT_SOURCE,
    )


_BASIC_HINTS = (
    "what is personal income tax",
    "meaning of personal income tax",
    "define personal income tax",
    "what does personal income tax mean",
)

_OBLIGATION_HINTS = (
    "who pays personal income tax",
    "who must comply with personal income tax",
    "who should pay personal income tax",
    "who is liable for personal income tax",
)

_RATE_HINTS = (
    "personal income tax rate",
    "pit rate",
    "rate of personal income tax",
)

_FILING_HINTS = (
    "how do i file personal income tax",
    "how to file personal income tax",
    "file personal income tax",
)

_PAYMENT_HINTS = (
    "how do i pay personal income tax",
    "how to pay personal income tax",
    "pay personal income tax",
)

_RECORDS_HINTS = (
    "what records should i keep for personal income tax",
    "what records should i keep for pit",
    "personal income tax records",
    "pit records",
)


def can_handle_pit_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    pit_context = topic_key in {"personal income tax", "personal_income_tax", "pit"} or "personal income tax" in q or re.search(r"\bpit\b", q)
    if not pit_context:
        pit_context = any(h in q for h in _BASIC_HINTS + _OBLIGATION_HINTS + _RATE_HINTS + _FILING_HINTS + _PAYMENT_HINTS + _RECORDS_HINTS)

    if not pit_context:
        return False

    if intent_key in {"definition", "obligation", "rate", "filing", "payment", "records"}:
        return True

    return any(h in q for h in _BASIC_HINTS + _OBLIGATION_HINTS + _RATE_HINTS + _FILING_HINTS + _PAYMENT_HINTS + _RECORDS_HINTS)


def resolve_pit_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "records" or any(h in q for h in _RECORDS_HINTS):
        return explain_personal_income_tax_records()

    if intent_key == "payment" or any(h in q for h in _PAYMENT_HINTS):
        return explain_personal_income_tax_payment()

    if intent_key == "filing" or any(h in q for h in _FILING_HINTS):
        return explain_personal_income_tax_filing()

    if intent_key == "obligation" or any(h in q for h in _OBLIGATION_HINTS):
        return explain_personal_income_tax_obligation()

    if intent_key == "rate" or any(h in q for h in _RATE_HINTS):
        return explain_personal_income_tax_rate()

    if intent_key == "definition" or any(h in q for h in _BASIC_HINTS):
        return explain_personal_income_tax_basic()

    return None
