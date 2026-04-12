from __future__ import annotations

import re
from typing import Optional


CURRENT_PAYE_SOURCE = (
    "Source: current official State Internal Revenue Service PAYE guidance, "
    "employer payroll compliance rules, and the official PAYE filing and remittance channel of the relevant state tax authority."
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


def explain_paye_basic() -> str:
    return _render_structured(
        body_lines=[
            "PAYE in Nigeria means Pay As You Earn.",
            "",
            "What it is:",
            "- PAYE is the system under which personal income tax is deducted from employment income by the employer.",
            "- The deducted tax is then filed and remitted to the relevant State Internal Revenue Service for the employee's state tax treatment.",
            "",
            "Practical point:",
            "- PAYE is mainly an employer payroll compliance issue, not a separate tax that the employee usually files by hand each payroll cycle.",
        ],
        next_steps=[
            "Ask who must deduct PAYE in your situation.",
            "Ask how to file or remit PAYE after deduction.",
            "Ask what payroll records should be kept for PAYE compliance.",
        ],
        source_line=CURRENT_PAYE_SOURCE,
    )


def explain_paye_obligation() -> str:
    return _render_structured(
        body_lines=[
            "PAYE usually applies where an employer pays taxable employment income and is expected to deduct tax from the employee's pay under the applicable rules.",
            "",
            "Who this usually affects:",
            "- employers paying salaries, wages, or other taxable employment income",
            "- employees whose pay falls within the personal income tax system handled through payroll deduction",
            "",
            "Practical rule:",
            "- First confirm that the worker is being treated under the employment income rules and not under a different engagement structure.",
            "- Then confirm which state tax authority should receive the PAYE filings and remittances.",
        ],
        next_steps=[
            "Ask who should deduct PAYE for the worker or payroll in your case.",
            "Ask how to file and remit PAYE after deduction.",
            "Ask what state authority should receive the PAYE return.",
        ],
        source_line=CURRENT_PAYE_SOURCE,
    )


def explain_paye_remittance() -> str:
    return _render_structured(
        body_lines=[
            "Handle PAYE remittance through the relevant State Internal Revenue Service channel for the payroll period involved.",
            "",
            "Before remittance:",
            "- Confirm the employees and payroll period involved.",
            "- Compute PAYE correctly for each employee based on the applicable rules.",
            "- Prepare the payroll schedule and supporting deduction records.",
            "",
            "Remittance steps:",
            "1. Use the correct state tax authority channel for PAYE filing and remittance.",
            "2. Submit the required PAYE schedule or return where required.",
            "3. Remit the PAYE amount through the approved payment channel.",
            "4. Keep proof of filing, proof of remittance, and payroll deduction records.",
        ],
        next_steps=[
            "Ask who should deduct PAYE in your case.",
            "Ask what records should be kept for PAYE.",
            "Ask which state authority should receive the PAYE return.",
        ],
        source_line=CURRENT_PAYE_SOURCE,
    )


def explain_paye_records() -> str:
    return _render_structured(
        body_lines=[
            "Keep the core payroll and deduction records that support PAYE computation, filing, and remittance for each payroll period.",
            "",
            "Records you should normally keep:",
            "- payroll register or payroll schedule for the period",
            "- employee pay details showing gross pay, deductions, and net pay",
            "- PAYE computation support for each employee where applicable",
            "- PAYE return or schedule submitted to the relevant State Internal Revenue Service",
            "- payment receipt, remittance acknowledgement, or portal confirmation",
            "",
            "Practical rule:",
            "- Keep records in a form that lets you trace the PAYE deducted, the return filed, and the amount remitted for the same payroll period.",
            "- Where employee details or payroll treatment change, keep the updated records that explain the change.",
        ],
        next_steps=[
            "Ask how to file or remit PAYE after deduction.",
            "Ask who should deduct PAYE in your case.",
            "Ask what to do if payroll records do not match the PAYE return.",
        ],
        source_line=CURRENT_PAYE_SOURCE,
    )


_BASIC_HINTS = (
    "what is paye",
    "meaning of paye",
    "define paye",
    "what does paye mean",
)

_OBLIGATION_HINTS = (
    "who must deduct paye",
    "who should deduct paye",
    "who deducts paye",
    "who must comply with paye",
    "who pays paye",
)

_REMITTANCE_HINTS = (
    "how do i remit paye",
    "how to remit paye",
    "remit paye",
    "how do i pay paye",
    "how to pay paye",
    "paye remittance",
)

_RECORDS_HINTS = (
    "what records should i keep for paye",
    "keep records for paye",
    "paye records",
)


def can_handle_paye_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    paye_context = topic_key in {"paye", "pay as you earn", "pay_as_you_earn"} or "paye" in q or "pay as you earn" in q
    if not paye_context:
        paye_context = any(h in q for h in _BASIC_HINTS + _OBLIGATION_HINTS + _REMITTANCE_HINTS + _RECORDS_HINTS)

    if not paye_context:
        return False

    if intent_key in {"definition", "obligation", "payment", "records", "filing"}:
        return True

    return True


def resolve_paye_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "records" or any(h in q for h in _RECORDS_HINTS):
        return explain_paye_records()

    if intent_key in {"payment", "filing"} or any(h in q for h in _REMITTANCE_HINTS):
        return explain_paye_remittance()

    if intent_key == "obligation" or any(h in q for h in _OBLIGATION_HINTS):
        return explain_paye_obligation()

    if intent_key == "definition" or any(h in q for h in _BASIC_HINTS):
        return explain_paye_basic()

    return explain_paye_basic()
