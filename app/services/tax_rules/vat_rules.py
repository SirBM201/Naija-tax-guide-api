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


def _looks_structured(text: str) -> bool:
    raw = _normalize(text)
    return "what to do next:" in raw or "what this means:" in raw or raw.startswith("answer")


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


def can_handle_vat_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    if topic_key != "vat":
        return False

    if intent_key in {"definition", "obligation", "exemption", "rate"}:
        return True

    if any(hint in q for hint in _OBLIGATION_HINTS):
        return True
    if any(hint in q for hint in _EXEMPTION_HINTS):
        return True
    if any(hint in q for hint in _RATE_HINTS):
        return True
    if any(hint in q for hint in _DEFINITION_HINTS):
        return True

    return False


def resolve_vat_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "obligation" or any(hint in q for hint in _OBLIGATION_HINTS):
        return explain_vat_obligation()

    if intent_key == "exemption" or any(hint in q for hint in _EXEMPTION_HINTS):
        return explain_vat_exemption_zero_rated()

    if intent_key == "rate" or any(hint in q for hint in _RATE_HINTS):
        return explain_vat_rate()

    if intent_key == "definition" or any(hint in q for hint in _DEFINITION_HINTS):
        return explain_vat_basic()

    return None
