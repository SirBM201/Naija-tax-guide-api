from __future__ import annotations

import re
from typing import Optional


CURRENT_TIN_SOURCE = (
    "Source: current official Nigeria Revenue Service / Joint Tax Board TIN registration and TIN verification channels."
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


def explain_tin_basic() -> str:
    return _render_structured(
        body_lines=[
            "A TIN in Nigeria is a Tax Identification Number used to identify a taxpayer for registration, filing, payment, verification, and general tax-compliance purposes.",
            "",
            "What it is:",
            "- A TIN is the taxpayer identifier used across the relevant tax authority's records.",
            "- It may apply to an individual, a business, or another qualifying taxpayer record under the applicable system.",
            "- It should be kept accurately because the same TIN is often needed for registration, tax filing, payment, verification, and compliance requests.",
            "",
            "Practical rule:",
            "- Do not create duplicate taxpayer records just because the TIN is not immediately available.",
            "- First check whether the taxpayer already has a TIN or needs a fresh registration under the correct authority channel.",
        ],
        next_steps=[
            "Ask who should issue or manage the TIN in your case.",
            "Ask how to register for a TIN if one has not yet been issued.",
            "Ask how to verify a TIN before using it for filing or payment.",
        ],
        source_line=CURRENT_TIN_SOURCE,
    )


def explain_tin_authority() -> str:
    return _render_structured(
        body_lines=[
            "The tax authority channel that issues or manages a TIN depends on the taxpayer's registration path and the approved TIN system being used for that taxpayer record.",
            "",
            "What this usually means:",
            "- The Joint Tax Board / Joint Revenue Board TIN infrastructure and the Nigeria Revenue Service channels are commonly part of the TIN administration path.",
            "- The correct route should be confirmed before starting a fresh registration or relying on an existing TIN.",
            "",
            "Practical rule:",
            "- Do not assume every TIN question belongs only to one portal without checking the taxpayer type and the registration context.",
            "- First confirm whether you are asking about TIN registration, TIN verification, or recovery of an already-issued TIN, then use the matching official channel.",
        ],
        next_steps=[
            "Ask how to register for a TIN in your case.",
            "Ask how to verify an issued TIN before using it.",
            "Ask what documents should support the TIN registration request.",
        ],
        source_line=CURRENT_TIN_SOURCE,
    )


def explain_tin_registration() -> str:
    return _render_structured(
        body_lines=[
            "Register for a TIN through the approved taxpayer-registration channel that matches the individual or business involved.",
            "",
            "Before registration:",
            "- Confirm whether the registration is for an individual or a business.",
            "- Prepare the taxpayer details and supporting information required by the official channel.",
            "- Check whether the taxpayer may already have a TIN before starting a fresh registration.",
            "",
            "Registration steps:",
            "1. Open the approved TIN registration channel for the taxpayer involved.",
            "2. Enter the taxpayer and profile details accurately so they match the underlying identity or business records.",
            "3. Complete any confirmation or activation step required by the authority.",
            "4. Keep the acknowledgement or confirmation issued after submission.",
            "5. Once the registration is processed, confirm that the TIN has been issued correctly and keep it safely for future filing, payment, and compliance use.",
        ],
        next_steps=[
            "Ask what documents should support the TIN registration in your case.",
            "Ask who should issue or manage the TIN for that taxpayer record.",
            "Ask how to verify the TIN once it has been issued.",
        ],
        source_line=CURRENT_TIN_SOURCE,
    )


def explain_tin_verification() -> str:
    return _render_structured(
        body_lines=[
            "Verify the TIN through the approved TIN verification or taxpayer-search channel used by the relevant tax authority infrastructure.",
            "",
            "Verification steps:",
            "1. Open the approved TIN verification channel.",
            "2. Search using the TIN or another accepted identifier such as the registration details allowed by that channel.",
            "3. Check that the returned taxpayer details match the correct person or business.",
            "4. Keep a screenshot or confirmation where available for your records.",
            "",
            "If verification fails:",
            "- Recheck that the identifier was entered exactly as issued.",
            "- Confirm that you are using the correct verification channel for the taxpayer's case.",
            "- Contact the relevant authority before using the TIN for filing, payment, or compliance work if the details do not match.",
        ],
        next_steps=[
            "Ask who should issue or manage the TIN in your case.",
            "Ask what to do if the TIN does not validate correctly.",
            "Ask what documents should be ready for a fresh TIN registration if no valid TIN exists.",
        ],
        source_line=CURRENT_TIN_SOURCE,
    )


def explain_tin_documents() -> str:
    return _render_structured(
        body_lines=[
            "Prepare the identity, business, and supporting registration details required by the approved TIN registration channel for the taxpayer involved.",
            "",
            "Documents or details you should normally be ready with:",
            "- taxpayer name and other identifying details exactly as they should appear on the tax record",
            "- business registration or incorporation details where the registration is for a business",
            "- address, contact details, and any other profile information required by the authority",
            "- any identity or supporting document the approved registration channel asks for in that case",
            "",
            "Practical rule:",
            "- The exact document set can differ depending on whether the registration is for an individual or a business and on the authority channel being used.",
            "- First confirm the taxpayer type, then prepare the details and documents requested by the official TIN registration process for that case.",
        ],
        next_steps=[
            "Ask how to register for a TIN after preparing the required details.",
            "Ask which authority channel should handle the TIN registration in your case.",
            "Ask how to verify the issued TIN after registration.",
        ],
        source_line=CURRENT_TIN_SOURCE,
    )


_DEFINITION_HINTS = (
    "what is a tin",
    "what is tin",
    "define tin",
    "meaning of tin",
    "what does tin mean",
    "tax identification number",
    "tax id",
)

_AUTHORITY_HINTS = (
    "who issues a tin",
    "who issues tin",
    "which tax authority handles tin registration",
    "which authority handles tin registration",
    "who handles tin registration",
)

_DOCUMENT_HINTS = (
    "what documents are needed for tin registration",
    "what documents are required for tin registration",
    "documents needed for tin registration",
    "documents for tin registration",
    "documents needed to register for tin",
    "requirements for tin registration",
)

_REGISTRATION_HINTS = (
    "how do i register for a tin",
    "how do i register for tin",
    "how to register for a tin",
    "how to register for tin",
    "get a tin",
    "obtain a tin",
    "apply for a tin",
    "register tin",
)

_VERIFICATION_HINTS = (
    "how do i verify a tin",
    "how do i validate a tin",
    "how to verify a tin",
    "verify tin",
    "tin verification",
    "check tin",
    "validate tin",
)


def can_handle_tin_rule(question: str, topic: str, intent_type: str) -> bool:
    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)

    tin_context = topic_key in {"tin", "tax identification number", "tax id"} or " tin " in f" {q} " or "tax identification number" in q or "tax id" in q
    if not tin_context:
        tin_context = any(h in q for h in _AUTHORITY_HINTS + _DOCUMENT_HINTS + _REGISTRATION_HINTS + _VERIFICATION_HINTS + _DEFINITION_HINTS)

    if not tin_context:
        return False

    if intent_key in {"definition", "authority", "documents", "procedure", "registration", "verification"}:
        return True

    if any(h in q for h in _DEFINITION_HINTS):
        return True
    if any(h in q for h in _AUTHORITY_HINTS):
        return True
    if any(h in q for h in _DOCUMENT_HINTS):
        return True
    if any(h in q for h in _REGISTRATION_HINTS):
        return True
    if any(h in q for h in _VERIFICATION_HINTS):
        return True

    return False


def resolve_tin_rule(question: str, intent_type: str) -> Optional[str]:
    q = _normalize(question)
    intent_key = _normalize(intent_type)

    if intent_key == "documents" or any(h in q for h in _DOCUMENT_HINTS):
        return explain_tin_documents()

    if intent_key == "authority" or any(h in q for h in _AUTHORITY_HINTS):
        return explain_tin_authority()

    if intent_key in {"registration", "procedure"} or any(h in q for h in _REGISTRATION_HINTS):
        return explain_tin_registration()

    if intent_key == "verification" or any(h in q for h in _VERIFICATION_HINTS):
        return explain_tin_verification()

    if intent_key == "definition" or any(h in q for h in _DEFINITION_HINTS):
        return explain_tin_basic()

    return None
