from __future__ import annotations

import re
from typing import Optional


CURRENT_PAYE_SOURCE = (
    "Source: current official State Internal Revenue Service PAYE guidance, employer payroll compliance rules, "
    "and the official PAYE filing and remittance channel of the relevant state tax authority."
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


def explain_paye_deduction() -> str:
    return _render_structured(
        body_lines=[
            "PAYE is generally deducted by the employer from taxable employment income before the net pay is released.",
            "",
            "What this means:",
            "- the employer should compute the payroll tax correctly under the applicable rules",
            "- the employer should deduct the tax through payroll records",
            "- the employer should file and remit the deducted amount through the correct State Internal Revenue Service channel",
            "",
            "Important note:",
            "- Do not assume every payment to a worker is treated the same way for PAYE. Confirm the employment and payroll treatment first.",
        ],
        next_steps=[
            "Ask how to file or remit PAYE after deduction.",
            "Ask what payroll records should support the deduction.",
            "Ask what to do if PAYE was not deducted correctly.",
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


_OBLIGATION_HINTS = (
    "who must deduct",
    "who deducts",
    "must deduct",
    "who should deduct",
    "does my employer",
    "is my employer required",
    "who must comply",
    "who is required",
    "does paye apply",
)

_DEDUCTION_HINTS = (
    "deduct paye",
    "paye deduction",
    "deducted from salary",
    "deducted from wages",
    "deducted from payroll",
)

_REMITTANCE_HINTS = (
    "how do i remit paye",
    "how to remit paye",
    "remit paye",
    "paye remittance",
    "how do i file paye",
    "how to file paye",
    "file paye",
    "paye filing",
    "pay paye",
    "paye payment",
)

_RECORDS_HINTS = (
    "what records should i keep for paye",
    "what payroll records should i keep",
    "what records should be kept for paye",
    "paye records",
    "payroll records",
    "records for paye",
    "records should i keep",
    "keep for paye",
    "keep for payroll tax",
    "paye documentation",
    "paye evidence",
)

_DEFINITION_HINTS = (
    "what is paye",
    "define paye",
    "meaning of paye",
    "what does paye mean",
)


def can_handle_paye_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    payroll_context = topic_key in {"paye", "pay as you earn", "payroll"} or "paye" in q or "payroll" in q

    if any(hint in q for hint in _RECORDS_HINTS):
        return payroll_context

    if not payroll_context:
        return False

    if intent_key in {"definition", "obligation", "deduction", "records", "procedure", "filing", "payment"}:
        return True

    if any(hint in q for hint in _OBLIGATION_HINTS):
        return True
    if any(hint in q for hint in _DEDUCTION_HINTS):
        return True
    if any(hint in q for hint in _REMITTANCE_HINTS):
        return True
    if any(hint in q for hint in _DEFINITION_HINTS):
        return True

    return False


def resolve_paye_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "records" or any(hint in q for hint in _RECORDS_HINTS):
        return explain_paye_records()

    if intent_key in {"procedure", "filing", "payment"} and any(hint in q for hint in _REMITTANCE_HINTS):
        return explain_paye_remittance()
    if any(hint in q for hint in _REMITTANCE_HINTS):
        return explain_paye_remittance()

    if intent_key == "obligation" or any(hint in q for hint in _OBLIGATION_HINTS):
        return explain_paye_obligation()

    if intent_key == "deduction" or any(hint in q for hint in _DEDUCTION_HINTS):
        return explain_paye_deduction()

    if intent_key == "definition" or any(hint in q for hint in _DEFINITION_HINTS):
        return explain_paye_basic()

    return None
