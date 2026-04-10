from __future__ import annotations

import re
from typing import Dict, List, Optional


ACTION_PATTERNS = {
    "verify": [
        r"\bverify\b",
        r"\bverification\b",
        r"\bvalidate\b",
        r"\bvalidation\b",
        r"\bconfirm\b",
        r"\bcheck\b",
        r"\bauthentic(?:ity)?\b",
        r"\bgenuine\b",
        r"\bstatus\b",
    ],
    "apply": [
        r"\bapply\b",
        r"\bapplication\b",
        r"\bobtain\b",
        r"\bget\b",
        r"\brequest\b",
    ],
    "register": [
        r"\bregister\b",
        r"\bregistration\b",
        r"\benrol\b",
        r"\benroll\b",
        r"\benrollment\b",
        r"\bsign up\b",
    ],
    "file": [
        r"\bfile\b",
        r"\bfiling\b",
        r"\bsubmit\b",
        r"\breturn\b",
    ],
    "pay": [
        r"\bpay\b",
        r"\bpayment\b",
        r"\bremit\b",
        r"\bremittance\b",
        r"\bsettle\b",
    ],
}


GENERIC_SOURCE_LINE = "Source: official tax authority portal, eServices platform, or approved compliance channel for the relevant issuing authority."
TCC_SOURCE_LINE = "Source: official State Internal Revenue Service or FIRS portal/eServices channel that handles TCC issuance or verification."
TIN_SOURCE_LINE = "Source: official tax authority taxpayer registration, TIN lookup, or taxpayer verification channel."
VAT_SOURCE_LINE = "Source: official VAT registration, filing, payment, and compliance channel of the relevant tax authority."
PAYE_SOURCE_LINE = "Source: official State Internal Revenue Service PAYE filing and remittance channel."


def _normalize(text: Optional[str]) -> str:
    raw = str(text or "").strip().lower()
    raw = raw.replace("_", " ")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _detect_action(question: Optional[str]) -> Optional[str]:
    q = _normalize(question)
    if not q:
        return None

    for action, patterns in ACTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, q):
                return action
    return None


def _topic_in(topic: Optional[str], *aliases: str) -> bool:
    value = _normalize(topic)
    alias_set = {_normalize(a) for a in aliases if a}
    return bool(value and value in alias_set)


def _render_lines(lines: List[str]) -> str:
    return "\n".join(line.rstrip() for line in lines if line is not None).strip()


def _section(title: str, items: List[str], *, numbered: bool = False) -> List[str]:
    lines: List[str] = [title]
    for idx, item in enumerate(items, start=1):
        prefix = f"{idx}. " if numbered else "- "
        lines.append(f"{prefix}{item}")
    return lines


def _compose_answer(
    *,
    direct_answer: str,
    sections: List[List[str]],
    next_steps: List[str],
    source_line: str,
    intent_type: str,
    source_label: str,
) -> Dict:
    lines: List[str] = [direct_answer, ""]

    for section in sections:
        if not section:
            continue
        lines.extend(section)
        lines.append("")

    lines.append("What to do next:")
    for idx, step in enumerate(next_steps, start=1):
        lines.append(f"{idx}. {step}")
    lines.append("")
    lines.append(source_line)

    return {
        "ok": True,
        "answer": _render_lines(lines),
        "meta": {
            "intent_type": intent_type,
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": source_label,
            "grounded": True,
        },
    }


def compose_tax_payment_process() -> Dict:
    return _compose_answer(
        direct_answer="Pay tax through the official channel for the exact tax type and the correct tax authority.",
        sections=[
            _section(
                "Before payment:",
                [
                    "Confirm the exact tax involved, such as Personal Income Tax, Company Income Tax, VAT, Withholding Tax, or PAYE.",
                    "Confirm whether the payment belongs to FIRS/NRS or the relevant State Internal Revenue Service.",
                    "Make sure the taxpayer details and TIN are correct before generating or using a payment reference.",
                ],
            ),
            _section(
                "Payment steps:",
                [
                    "Use the official portal, approved bank channel, or approved payment platform accepted by that authority.",
                    "Generate or confirm the assessment, invoice, or payment reference where required.",
                    "Pay the correct amount and keep the receipt or payment acknowledgement.",
                ],
                numbered=True,
            ),
            _section(
                "After payment:",
                [
                    "Keep both the payment evidence and any related filing records.",
                    "Where the tax also requires a return, confirm that the filing step is completed separately.",
                ],
            ),
        ],
        next_steps=[
            "Ask which tax authority should receive the payment in your case.",
            "Ask whether the tax also requires a filing after payment.",
            "Ask what records you should keep after payment.",
        ],
        source_line=GENERIC_SOURCE_LINE,
        intent_type="tax_payment_process",
        source_label="General Nigerian Tax Payment Process",
    )


def compose_tin_registration() -> Dict:
    return _compose_answer(
        direct_answer="Register for a TIN through the official taxpayer registration channel of the relevant tax authority.",
        sections=[
            _section(
                "Before you register:",
                [
                    "Confirm whether you are registering as an individual or for a business.",
                    "Prepare the core details normally required, such as legal name, phone number, address, and business registration details where applicable.",
                ],
            ),
            _section(
                "Registration steps:",
                [
                    "Complete the taxpayer registration form carefully and ensure the identity or business details match your records.",
                    "Submit the registration through the official channel and keep the acknowledgement.",
                    "Confirm that the issued TIN matches the correct taxpayer details before using it for filing or payment.",
                ],
                numbered=True,
            ),
            _section(
                "Important note:",
                [
                    "If you already have a TIN but cannot find it, use the authority's recovery or verification option instead of creating a duplicate record.",
                ],
            ),
        ],
        next_steps=[
            "Ask how to verify a TIN after registration.",
            "Ask what details are usually required for TIN registration.",
            "Ask which tax authority should issue the TIN in your case.",
        ],
        source_line=TIN_SOURCE_LINE,
        intent_type="tin_registration",
        source_label="TIN Registration Process",
    )


def compose_tin_verification() -> Dict:
    return _compose_answer(
        direct_answer="Verify the TIN through the official taxpayer search or TIN verification channel of the authority that manages it.",
        sections=[
            _section(
                "Verification steps:",
                [
                    "Open the TIN verification or taxpayer lookup page where available.",
                    "Enter the TIN exactly as issued and, where the portal allows, confirm with the taxpayer or business name.",
                    "Check that the returned taxpayer details match the correct person or business.",
                ],
                numbered=True,
            ),
            _section(
                "If the result does not match:",
                [
                    "Do not use the TIN for filing, payment, or compliance work until the issuing authority confirms it.",
                    "Keep a screenshot or confirmation page where available for your records.",
                ],
            ),
        ],
        next_steps=[
            "Ask how to register for a TIN if you do not have one yet.",
            "Ask what to do when a TIN does not validate.",
            "Ask which authority should manage the TIN in your case.",
        ],
        source_line=TIN_SOURCE_LINE,
        intent_type="tin_verification",
        source_label="TIN Verification Process",
    )


def compose_tax_filing_process() -> Dict:
    return _compose_answer(
        direct_answer="File through the official filing channel for the exact tax type, filing period, and tax authority involved.",
        sections=[
            _section(
                "Before filing:",
                [
                    "Confirm the exact tax type and filing period.",
                    "Confirm whether the return is for an individual, employer, or company.",
                    "Gather the supporting records for the period, such as income records, invoices, payroll schedules, prior payments, and deduction details where relevant.",
                ],
            ),
            _section(
                "Filing steps:",
                [
                    "Compute the figures correctly before submission.",
                    "Use the official filing portal or approved filing channel of the correct authority.",
                    "Submit the return and keep proof of filing.",
                    "Where tax is payable, complete payment and keep the receipt with the return evidence.",
                ],
                numbered=True,
            ),
        ],
        next_steps=[
            "Ask for the filing steps for the exact tax type involved.",
            "Ask what documents are usually needed before filing.",
            "Ask which authority should receive the return in your case.",
        ],
        source_line=GENERIC_SOURCE_LINE,
        intent_type="tax_filing_process",
        source_label="General Tax Filing Process",
    )


def compose_vat_filing_process() -> Dict:
    return _compose_answer(
        direct_answer="File VAT through the approved VAT filing channel for the relevant tax authority and filing period.",
        sections=[
            _section(
                "Before filing:",
                [
                    "Confirm the VAT period involved.",
                    "Gather the records for taxable sales, output VAT, input VAT where relevant, invoices, and supporting schedules.",
                    "Reconcile the figures so the return matches your records.",
                ],
            ),
            _section(
                "Filing steps:",
                [
                    "Submit the VAT return through the approved channel within the applicable deadline.",
                    "Where VAT is payable, complete payment through the approved payment channel.",
                    "Keep both the return evidence and payment evidence for your records.",
                ],
                numbered=True,
            ),
        ],
        next_steps=[
            "Ask whether VAT applies to your business or transaction first.",
            "Ask how to register for VAT if you are not yet registered.",
            "Ask what records you should keep for VAT compliance.",
        ],
        source_line=VAT_SOURCE_LINE,
        intent_type="vat_filing_process",
        source_label="VAT Filing Process",
    )


def compose_vat_registration_process() -> Dict:
    return _compose_answer(
        direct_answer="Register for VAT through the approved registration channel of the relevant tax authority once your business falls within the scope of VAT registration.",
        sections=[
            _section(
                "Before registration:",
                [
                    "Confirm that your business activity falls within the applicable VAT registration rules.",
                    "Prepare the business details and TIN required for registration.",
                ],
            ),
            _section(
                "Registration steps:",
                [
                    "Provide the required taxpayer and business information accurately.",
                    "Complete any activation or confirmation step required by the authority.",
                    "Keep the acknowledgement and any confirmation notice or certificate issued.",
                ],
                numbered=True,
            ),
            _section(
                "After registration:",
                [
                    "Make sure your invoicing, record-keeping, filing, and payment process are aligned with VAT compliance.",
                ],
            ),
        ],
        next_steps=[
            "Ask whether your business must charge VAT.",
            "Ask how to file VAT after registration.",
            "Ask what invoices and records should support VAT compliance.",
        ],
        source_line=VAT_SOURCE_LINE,
        intent_type="vat_registration_process",
        source_label="VAT Registration Process",
    )


def compose_vat_payment_process() -> Dict:
    return _compose_answer(
        direct_answer="Pay VAT through the approved VAT payment channel of the tax authority that receives the return.",
        sections=[
            _section(
                "Before payment:",
                [
                    "Confirm the VAT period and the amount due from the return or assessment.",
                    "Make sure the taxpayer profile, TIN, and VAT return details match the correct business.",
                    "Generate or confirm the payment reference required by the official portal or payment channel.",
                ],
            ),
            _section(
                "Payment steps:",
                [
                    "Use the approved VAT payment channel accepted by the relevant authority.",
                    "Pay the exact VAT amount due for the relevant period.",
                    "Keep the receipt, acknowledgement, or payment confirmation.",
                ],
                numbered=True,
            ),
            _section(
                "After payment:",
                [
                    "Keep the payment evidence together with the VAT return evidence for that period.",
                    "If the portal still shows unpaid status, confirm whether the payment has posted correctly before assuming there is a failure.",
                ],
            ),
        ],
        next_steps=[
            "Ask how to file VAT if the return has not been submitted yet.",
            "Ask what records should support the VAT amount paid.",
            "Ask what to do if the portal still shows unpaid after payment.",
        ],
        source_line=VAT_SOURCE_LINE,
        intent_type="vat_payment_process",
        source_label="VAT Payment Process",
    )


def compose_paye_remittance_process() -> Dict:
    return _compose_answer(
        direct_answer="Handle PAYE remittance through the official State Internal Revenue Service channel for the employees and payroll period involved.",
        sections=[
            _section(
                "Before remittance:",
                [
                    "Confirm the employees and payroll period involved.",
                    "Compute PAYE correctly for each employee under the applicable rules.",
                    "Prepare the payroll schedule and supporting deduction records.",
                ],
            ),
            _section(
                "Remittance steps:",
                [
                    "Submit the required PAYE schedule or return where required.",
                    "Remit the PAYE amount through the approved payment channel.",
                    "Keep proof of filing, proof of remittance, and payroll deduction records.",
                ],
                numbered=True,
            ),
        ],
        next_steps=[
            "Ask who must deduct PAYE in your situation.",
            "Ask when PAYE should be filed or paid.",
            "Ask what payroll records should be kept for PAYE compliance.",
        ],
        source_line=PAYE_SOURCE_LINE,
        intent_type="paye_remittance_process",
        source_label="PAYE Remittance Process",
    )


def compose_tcc_application() -> Dict:
    return _compose_answer(
        direct_answer="Apply for a Tax Clearance Certificate through the official portal or eServices channel of the tax authority that manages your tax record.",
        sections=[
            _section(
                "Where to apply:",
                [
                    "For many personal income tax cases, the application is usually handled by the relevant State Internal Revenue Service.",
                    "For relevant federal cases, use the appropriate FIRS/NRS channel.",
                ],
            ),
            _section(
                "Before you apply:",
                [
                    "Make sure the TIN is active.",
                    "File any outstanding returns that should already have been submitted.",
                    "Settle or regularize unpaid liabilities where due.",
                    "Make sure the taxpayer profile details match the correct person or business.",
                ],
            ),
            _section(
                "Application steps:",
                [
                    "Sign in to the official portal or eServices platform used for TCC requests.",
                    "Open the TCC application option and complete the request with the correct taxpayer details.",
                    "Upload or provide any supporting records required by the authority.",
                    "Submit the request and keep the acknowledgement or reference number.",
                    "Track the application and download or collect the TCC once approved.",
                ],
                numbered=True,
            ),
            _section(
                "If the request is delayed or rejected:",
                [
                    "Check for missing filings, unpaid liabilities, profile mismatches, or missing supporting records.",
                ],
            ),
        ],
        next_steps=[
            "Verify the issued TCC on the same authority's portal before using it.",
            "Confirm which tax authority should issue your TCC in your case.",
            "Check what a TCC is commonly used for in practice.",
        ],
        source_line=TCC_SOURCE_LINE,
        intent_type="tcc_application",
        source_label="TCC Application Process",
    )


def compose_tcc_verification() -> Dict:
    return _compose_answer(
        direct_answer="Verify the TCC on the official portal or eServices channel of the tax authority that issued it.",
        sections=[
            _section(
                "Where to verify:",
                [
                    "Use the TCC verification page, receipt verification page, or taxpayer verification page provided by that authority.",
                ],
            ),
            _section(
                "Verification steps:",
                [
                    "Enter the TCC number, reference number, or other identifier requested by the portal.",
                    "Check that the returned details match the taxpayer correctly.",
                ],
                numbered=True,
            ),
            _section(
                "Check these details carefully:",
                [
                    "taxpayer name",
                    "TIN where shown",
                    "certificate or receipt reference",
                    "status or validity information where shown",
                ],
            ),
            _section(
                "If verification fails:",
                [
                    "Do not rely on the certificate for compliance, contracts, banking, or clearance purposes until the issuing authority confirms it.",
                    "Keep a screenshot or confirmation page where available for your records.",
                ],
            ),
        ],
        next_steps=[
            "Confirm that you are using the portal of the correct issuing authority.",
            "Check what a TCC is commonly used for in practice.",
            "Ask what to do when a portal shows no match or invalid status.",
        ],
        source_line=TCC_SOURCE_LINE,
        intent_type="tcc_verification",
        source_label="TCC Verification Process",
    )


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "tax_filing_process": compose_tax_filing_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_registration_process": compose_vat_registration_process,
    "vat_payment_process": compose_vat_payment_process,
    "paye_remittance_process": compose_paye_remittance_process,
    "tcc_application": compose_tcc_application,
    "tcc_verification": compose_tcc_verification,
}


def try_compose(
    intent: Optional[str] = None,
    *,
    question: Optional[str] = None,
    topic: Optional[str] = None,
    intent_type: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
):
    del lang, channel

    if intent and not question and not topic and not intent_type:
        fn = PROCESS_MAP.get(_normalize(intent).replace(" ", "_"))
        return fn() if fn else None

    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)
    action = _detect_action(question)

    if _topic_in(topic_key, "tax_clearance_certificate", "tax clearance certificate", "tcc"):
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if _topic_in(topic_key, "tin", "tax identification number"):
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()

    if _topic_in(topic_key, "vat", "value added tax"):
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()
        if action == "pay":
            return compose_vat_payment_process()

    if _topic_in(topic_key, "paye", "pay as you earn") and action == "pay":
        return compose_paye_remittance_process()

    if intent_key in {"vat payment process", "vat_payment_process"}:
        return compose_vat_payment_process()

    if intent_key in {"tax payment process", "tax_payment_process"} or action == "pay":
        return compose_tax_payment_process()

    if intent_key in {"tax filing process", "tax_filing_process"} or action == "file":
        return compose_tax_filing_process()

    return None
