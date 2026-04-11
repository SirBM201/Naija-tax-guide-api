from __future__ import annotations

import re
from typing import Dict, Optional


ACTION_PATTERNS = {
    "verify": [
        r"\bverify\b",
        r"\bverification\b",
        r"\bvalidate\b",
        r"\bvalidation\b",
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
    ],
    "deduct": [
        r"\bdeduct\b",
        r"\bdeduction\b",
        r"\bwithhold\b",
        r"\bwithholding\b",
    ],
    "rate": [
        r"\brate\b",
        r"\bpercentage\b",
    ],
    "records": [
        r"\brecords\b",
        r"\brecord\b",
        r"\bpayroll records\b",
        r"\bkeep\b.*\brecord",
    ],
}


def _normalize(text: Optional[str]) -> str:
    raw = str(text or "").strip().lower()
    raw = raw.replace("_", " ")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _mentions_any(text: str, *terms: str) -> bool:
    value = _normalize(text)
    return any(_normalize(term) in value for term in terms if term)


def _detect_action(question: Optional[str]) -> Optional[str]:
    q = _normalize(question)
    if not q:
        return None

    priority_order = [
        "verify",
        "apply",
        "register",
        "file",
        "pay",
        "records",
        "rate",
        "deduct",
    ]

    for action in priority_order:
        for pattern in ACTION_PATTERNS[action]:
            if re.search(pattern, q):
                return action
    return None


def _topic_in(topic: Optional[str], *aliases: str) -> bool:
    value = _normalize(topic)
    alias_set = {_normalize(a) for a in aliases if a}
    return bool(value and value in alias_set)


def _answer(answer: str, intent_type: str, source_label: str) -> Dict:
    return {
        "ok": True,
        "answer": answer.strip(),
        "meta": {
            "intent_type": intent_type,
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": source_label,
            "grounded": True,
        },
    }


# ------------------------
# Authority routing answers
# ------------------------


def compose_personal_income_tax_authority() -> Dict:
    return _answer(
        """
The relevant State Internal Revenue Service usually handles Personal Income Tax for individuals in the state that has the taxing right in the case.

What this usually means:
- Personal Income Tax is generally handled on the state side.
- The correct state authority should be confirmed before filing, paying, or asking for a personal-income-tax compliance document.

Practical rule:
- Do not route a personal-income-tax question to the federal company-income-tax channel just because the taxpayer works for a company.
- First confirm that the issue is about an individual's income tax, then use the relevant state tax authority channel.

What to do next:
1. Ask which state authority should receive the personal income tax in your case.
2. Ask whether the issue is about PAYE or another personal-income-tax matter.
3. Ask which portal or filing channel that state authority uses.

Source: current official Nigerian tax-administration structure for personal income tax at state level and the relevant State Internal Revenue Service channel for the taxpayer's case.
""",
        "personal_income_tax_authority",
        "Personal Income Tax Authority Routing",
    )


def compose_paye_authority() -> Dict:
    return _answer(
        """
PAYE is usually handled by the relevant State Internal Revenue Service, not by the federal company-income-tax channel.

What this usually means:
- PAYE is part of personal income tax administered through payroll deduction.
- The employer should use the state tax authority that has the right to receive the employee-related PAYE filing and remittance.

Practical rule:
- If the question is whether FIRS/NRS or a State Internal Revenue Service handles PAYE, start from the state personal-income-tax side.
- Then confirm the exact state authority that should receive the PAYE return and remittance in the case.

What to do next:
1. Ask which state authority should receive the PAYE return in your case.
2. Ask who must deduct PAYE on the payroll involved.
3. Ask which state portal or remittance channel should be used.

Source: current official state-level PAYE administration structure and the relevant State Internal Revenue Service filing and remittance channel.
""",
        "paye_authority",
        "PAYE Authority Routing",
    )


def compose_vat_authority() -> Dict:
    return _answer(
        """
VAT is handled through the federal tax authority channel, currently the Nigeria Revenue Service / former FIRS VAT administration channel and its approved service portals.

What this usually means:
- VAT registration, filing, payment, and federal VAT administration should be routed through the approved federal VAT channel.
- The taxpayer should use the official federal portal or self-service channel that supports the VAT profile and transaction involved.

Practical rule:
- Do not route a VAT question to a state personal-income-tax portal just because the same business also deals with state taxes.
- First confirm that the question is about VAT, then use the federal VAT administration channel.

What to do next:
1. Ask which federal portal should be used for VAT in your case.
2. Ask how to register, file, or pay VAT after confirming the channel.
3. Ask whether the exact supply is taxable, exempt, or zero-rated before charging VAT.

Source: current official Nigeria Revenue Service / former FIRS VAT administration structure and approved federal VAT portal channels.
""",
        "vat_authority",
        "VAT Authority Routing",
    )


def compose_tcc_authority() -> Dict:
    return _answer(
        """
The Tax Clearance Certificate should be issued by the tax authority that administers the taxpayer's relevant tax record for the case.

What this usually means:
- For many personal income tax cases, the relevant State Internal Revenue Service is the issuing authority.
- For relevant federal cases, the approved Nigeria Revenue Service / former FIRS TCC eServices channel is used.

Practical rule:
- Do not assume every TCC must come from only one authority.
- First confirm whether the taxpayer's case is being handled on the state personal-income-tax side or on the relevant federal side, then use the issuing authority's TCC portal or eServices channel.

What to do next:
1. Ask which authority should issue the TCC in your case.
2. Ask how to apply for the TCC on that authority's approved portal.
3. Ask how to verify the issued TCC before using it.

Source: current official TCC administration structure, including the approved NRS/FIRS TCC eServices channel and the relevant State Internal Revenue Service route for state-side cases.
""",
        "tcc_authority",
        "TCC Authority Routing",
    )


def compose_withholding_tax_authority() -> Dict:
    return _answer(
        """
The tax authority that should receive Withholding Tax depends on the payment category and the authority that administers that withholding obligation in the case.

What this usually means:
- You should not assume that every WHT remittance goes to one universal channel.
- First identify the exact payment type, the payer/recipient context, and the current rule that applies to that withholding category.

Practical rule:
- Confirm the exact withholding category first.
- Then use the approved channel of the tax authority that receives that specific WHT deduction for the payment involved.

What to do next:
1. Ask whether the exact payment in your case should attract WHT at all.
2. Ask what rate applies to that payment category.
3. Ask how to remit the WHT once the receiving authority is confirmed.

Source: current official withholding-tax administration guidance and the approved remittance channel of the authority that receives the deduction for the payment category involved.
""",
        "withholding_tax_authority",
        "Withholding Tax Authority Routing",
    )


def compose_company_income_tax_authority() -> Dict:
    return _answer(
        """
Company Income Tax is handled through the federal tax authority channel, currently the Nigeria Revenue Service / former FIRS company-income-tax administration channel.

What this usually means:
- CIT filing, payment, and core company-income-tax administration are handled on the federal side.
- The company should use the approved federal filing and payment channel for the relevant accounting period.

Practical rule:
- Do not route a Company Income Tax question to a state personal-income-tax or PAYE channel.
- First confirm that the taxpayer is a company and the issue is about company profits, then use the approved federal CIT channel.

What to do next:
1. Ask what Company Income Tax rate rule applies to the company category in your case.
2. Ask how to file Company Income Tax for the relevant period.
3. Ask how to pay Company Income Tax through the approved federal channel.

Source: current official Nigeria Revenue Service / former FIRS company-income-tax administration structure and approved federal CIT filing and payment channels.
""",
        "company_income_tax_authority",
        "Company Income Tax Authority Routing",
    )




def compose_tin_basic() -> Dict:
    return _answer(
        """
A TIN in Nigeria is a Tax Identification Number used to identify a taxpayer for registration, filing, payment, verification, and general tax-compliance purposes.

What it is:
- A TIN is the taxpayer identifier used across the relevant tax authority's records.
- It may be used for an individual, a business, or another qualifying taxpayer record under the applicable system.
- It should be kept accurately because the same TIN is often needed for registration, tax filing, payment, verification, and compliance requests.

Practical rule:
- Do not create duplicate taxpayer records just because the TIN is not immediately available.
- First check whether the taxpayer already has a TIN or needs a fresh registration under the correct authority channel.

What to do next:
1. Ask who should issue or manage the TIN in your case.
2. Ask how to register for a TIN if one has not yet been issued.
3. Ask how to verify the TIN before using it for filing or payment.

Source: current official Nigeria Revenue Service / Joint Tax Board TIN registration and TIN verification channels.
""",
        "tin_basic",
        "TIN Definition",
    )


def compose_tin_authority() -> Dict:
    return _answer(
        """
The tax authority that issues or manages a TIN depends on the taxpayer's registration path and the authority channel being used for that taxpayer record.

What this usually means:
- You should use the approved TIN registration or TIN verification channel that matches the taxpayer's case.
- The Joint Tax Board / Joint Revenue Board TIN infrastructure and the Nigeria Revenue Service channels are commonly part of the TIN administration path.
- The correct route should be confirmed before starting a fresh registration or relying on an existing TIN.

Practical rule:
- Do not assume every TIN question belongs only to one portal without checking the taxpayer type and the registration context.
- First confirm whether you are asking about TIN registration, TIN verification, or recovery of an already-issued TIN, then use the matching official channel.

What to do next:
1. Ask how to register for a TIN in your case.
2. Ask how to verify an issued TIN before using it.
3. Ask what documents should support the TIN registration request.

Source: current official Nigeria Revenue Service / Joint Tax Board TIN registration and TIN verification structure.
""",
        "tin_authority",
        "TIN Authority Routing",
    )


def compose_tin_documents() -> Dict:
    return _answer(
        """
Prepare the identity, business, and supporting registration details required by the approved TIN registration channel for the taxpayer involved.

Documents or details you should normally be ready with:
- taxpayer name and other identifying details exactly as they should appear on the tax record
- business registration or incorporation details where the registration is for a business
- address, contact details, and any other profile information required by the authority
- any identity or supporting document the approved registration channel asks for in that case

Practical rule:
- The exact document set can differ depending on whether the registration is for an individual or a business and on the authority channel being used.
- First confirm the taxpayer type, then prepare the details and documents requested by the official TIN registration process for that case.

What to do next:
1. Ask how to register for a TIN after preparing the required details.
2. Ask which authority channel should handle the TIN registration in your case.
3. Ask how to verify the issued TIN after registration.

Source: current official Nigeria Revenue Service / Joint Tax Board TIN registration channels and taxpayer-profile requirements.
""",
        "tin_documents",
        "TIN Registration Documents",
    )


def compose_tax_filing_process() -> Dict:
    return _answer(
        """
To file tax in Nigeria, use this general process:

1. Confirm the exact tax type and filing period involved.
2. Confirm whether the filing is for an individual, an employer, or a company.
3. Confirm the correct tax authority.
4. Gather the records needed for the filing period.
5. Compute the figures correctly before submitting.
6. Use the official filing portal or approved filing channel.
7. Submit the return and keep proof of filing.
8. Where tax is payable, complete payment and keep the receipt together with the filed return evidence.

If the tax type is specific, such as VAT, PAYE, WHT, or Company Income Tax, the filing process should be tailored to that tax rather than treated as a generic filing question.
""",
        "tax_filing_process",
        "General Tax Filing Process",
    )


def compose_vat_registration_process() -> Dict:
    return _answer(
        """
Register for VAT through the approved registration channel of the relevant federal tax authority once your business falls within the scope of VAT registration.

Before registration:
- Confirm that the business activity falls within the applicable VAT registration rules.
- Prepare the business details and TIN required for registration.

Registration steps:
1. Provide the required taxpayer and business information accurately.
2. Complete any activation or confirmation step required by the authority.
3. Keep the acknowledgement and any confirmation notice or certificate issued.

After registration:
- Make sure your invoicing, record-keeping, filing, and payment process are aligned with VAT compliance.

What to do next:
1. Ask whether your business must charge VAT.
2. Ask how to file VAT after registration.
3. Ask what invoices and records should support VAT compliance.

Source: official federal VAT registration and compliance channel of the relevant tax authority.
""",
        "vat_registration_process",
        "VAT Registration Process",
    )


def compose_vat_filing_process() -> Dict:
    return _answer(
        """
File VAT through the approved VAT filing channel for the relevant tax authority and filing period.

Before filing:
- Confirm the VAT period involved.
- Gather the records for taxable sales, output VAT, input VAT where relevant, invoices, and supporting schedules.
- Reconcile the figures so the return matches your records.

Filing steps:
1. Submit the VAT return through the approved channel within the applicable deadline.
2. Where VAT is payable, complete payment through the approved payment channel.
3. Keep both the return evidence and payment evidence for your records.

What to do next:
1. Ask whether VAT applies to your business or transaction first.
2. Ask how to register for VAT if you are not yet registered.
3. Ask what records you should keep for VAT compliance.

Source: official VAT registration, filing, payment, and compliance channel of the relevant tax authority.
""",
        "vat_filing_process",
        "VAT Filing Process",
    )


def compose_paye_remittance_process() -> Dict:
    return _answer(
        """
Handle PAYE remittance through the relevant State Internal Revenue Service channel for the payroll period involved.

Before remittance:
- Confirm the employees and payroll period involved.
- Compute PAYE correctly for each employee based on the applicable rules.
- Prepare the payroll schedule and supporting deduction records.

Remittance steps:
1. Use the correct state tax authority channel for PAYE filing and remittance.
2. Submit the required PAYE schedule or return where required.
3. Remit the PAYE amount through the approved payment channel.
4. Keep proof of filing, proof of remittance, and payroll deduction records.

What to do next:
1. Ask who should deduct PAYE in your case.
2. Ask what records should be kept for PAYE.
3. Ask which state authority should receive the PAYE return.

Source: official PAYE filing and remittance channel of the relevant State Internal Revenue Service.
""",
        "paye_remittance_process",
        "PAYE Remittance Process",
    )


def compose_tcc_application() -> Dict:
    return _answer(
        """
Apply for a Tax Clearance Certificate through the official portal or eServices channel of the tax authority that manages your tax record.

Where to apply:
- For many personal income tax cases, the application is usually handled by the relevant State Internal Revenue Service.
- For relevant federal cases, use the appropriate federal TCC channel.

Before you apply:
- Make sure the TIN or profile is active.
- File any outstanding returns that should already have been submitted.
- Settle or regularize unpaid liabilities where due.
- Make sure the taxpayer profile details match the correct person or business.

Application steps:
1. Sign in to the official portal or eServices platform used for TCC requests.
2. Open the TCC application option and complete the request with the correct taxpayer details.
3. Upload or provide any supporting records required by the authority.
4. Submit the request and keep the acknowledgement or reference number.
5. Track the application and download or collect the TCC once approved.

What to do next:
1. Verify the issued TCC on the same authority's portal before using it.
2. Confirm which authority should issue your TCC in your case.
3. Check what a TCC is commonly used for in practice.

Source: official State Internal Revenue Service or federal TCC portal/eServices channel that handles TCC issuance or verification.
""",
        "tcc_application",
        "TCC Application Process",
    )


def compose_tcc_verification() -> Dict:
    return _answer(
        """
Verify the TCC on the official portal or eServices channel of the tax authority that issued it.

Where to verify:
- Use the TCC verification page, receipt verification page, or taxpayer verification page provided by that authority.

Verification steps:
1. Enter the TCC number, reference number, or other identifier requested by the portal.
2. Check that the returned details match the taxpayer correctly.

Check these details carefully:
- taxpayer name
- TIN where shown
- certificate or receipt reference
- status or validity information where shown

If verification fails:
- Do not rely on the certificate for compliance, contracts, banking, or clearance purposes until the issuing authority confirms it.
- Keep a screenshot or confirmation page where available for your records.

What to do next:
1. Confirm that you are using the portal of the correct issuing authority.
2. Check what a TCC is commonly used for in practice.
3. Ask what to do when a portal shows no match or invalid status.

Source: official State Internal Revenue Service or federal TCC portal/eServices channel that handles TCC issuance or verification.
""",
        "tcc_verification",
        "TCC Verification Process",
    )


def compose_withholding_tax_deduction() -> Dict:
    return _answer(
        """
Deduct Withholding Tax only after confirming that the exact payment you are making falls within a withholding category under the applicable rule.

Before deduction:
- Identify the exact payment type involved.
- Confirm that the payment is one that attracts WHT.
- Confirm the applicable rate for that exact payment category.
- Confirm the correct tax authority that should receive the remittance.

Deduction steps:
1. Compute WHT on the correct gross payment base where the rule requires it.
2. Deduct the WHT amount before paying the net sum to the recipient.
3. Keep the deduction computation, payment instruction, and source records.

What to do next:
1. Ask how to remit WHT after deduction.
2. Ask what rate applies to the exact payment type.
3. Ask what records and credit-support evidence should be kept.

Source: current official withholding-tax deduction and remittance guidance for qualifying payments.
""",
        "withholding_tax_deduction",
        "Withholding Tax Deduction Process",
    )


def compose_withholding_tax_remittance() -> Dict:
    return _answer(
        """
Remit Withholding Tax through the approved channel of the tax authority that receives the deduction for the payment category involved.

Before remittance:
- Confirm the exact payment, WHT amount deducted, and the correct authority.
- Make sure the payer and recipient details match the transaction records.
- Generate or confirm any remittance reference required by the official channel.

Remittance steps:
1. Use the approved portal, bank channel, or remittance method accepted by the authority.
2. Remit the deducted WHT within the applicable compliance window.
3. Keep the remittance receipt, acknowledgement, or portal confirmation.
4. Issue or retain the evidence needed to support the recipient's tax-credit claim where applicable.

What to do next:
1. Ask what records should be kept for WHT.
2. Ask what evidence the recipient should receive for tax-credit purposes.
3. Ask whether the exact payment in your case should attract WHT at all.

Source: official withholding-tax remittance and credit-support channel of the relevant tax authority.
""",
        "withholding_tax_remittance",
        "Withholding Tax Remittance Process",
    )


def compose_company_income_tax_filing() -> Dict:
    return _answer(
        """
File Company Income Tax through the approved Federal Inland Revenue Service / Nigeria Revenue Service channel for the relevant accounting period.

Before filing:
- Confirm the accounting period involved and the return being prepared.
- Prepare the financial statements, tax computation schedules, and supporting records for the period.
- Confirm the company's taxable-profit position and any tax adjustments being applied.

Filing steps:
1. Complete the Company Income Tax return through the approved filing channel.
2. Upload or provide the computation and supporting documents required for the filing.
3. Submit the return within the applicable deadline and keep the acknowledgement or portal confirmation.
4. Where tax is payable, keep the filed return evidence together with the payment evidence for the same period.

What to do next:
1. Ask how to pay Company Income Tax once the liability is confirmed.
2. Ask what records should support the Company Income Tax computation.
3. Ask what rate rule applies to the company category in your case.

Source: official federal Company Income Tax filing channel of the relevant tax authority.
""",
        "company_income_tax_filing",
        "Company Income Tax Filing Process",
    )


def compose_company_income_tax_payment() -> Dict:
    return _answer(
        """
Pay Company Income Tax through the approved Federal Inland Revenue Service / Nigeria Revenue Service payment channel for the relevant assessment or self-computed liability.

Before payment:
- Confirm the accounting period, tax amount due, and the correct taxpayer details.
- Make sure the company's return, assessment, or computation supports the amount being paid.
- Generate or confirm any payment reference required by the official channel.

Payment steps:
1. Use the approved portal, bank channel, or payment method accepted for the Company Income Tax payment.
2. Pay the exact CIT amount due for the relevant period or assessment.
3. Keep the receipt, acknowledgement, or portal confirmation as payment evidence.
4. Match the payment evidence to the corresponding return or assessment for the same period.

What to do next:
1. Ask how to file Company Income Tax if the return has not yet been submitted.
2. Ask what records should support the Company Income Tax computation and payment.
3. Ask what Company Income Tax rate rule applies to the company category in your case.

Source: official federal Company Income Tax payment channel of the relevant tax authority.
""",
        "company_income_tax_payment",
        "Company Income Tax Payment Process",
    )


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tin_basic": compose_tin_basic,
    "tin_authority": compose_tin_authority,
    "tin_documents": compose_tin_documents,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "tax_filing_process": compose_tax_filing_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_registration_process": compose_vat_registration_process,
    "paye_remittance_process": compose_paye_remittance_process,
    "tcc_application": compose_tcc_application,
    "tcc_verification": compose_tcc_verification,
    "withholding_tax_deduction": compose_withholding_tax_deduction,
    "withholding_tax_remittance": compose_withholding_tax_remittance,
    "company_income_tax_filing": compose_company_income_tax_filing,
    "company_income_tax_payment": compose_company_income_tax_payment,
    "personal_income_tax_authority": compose_personal_income_tax_authority,
    "paye_authority": compose_paye_authority,
    "vat_authority": compose_vat_authority,
    "tcc_authority": compose_tcc_authority,
    "withholding_tax_authority": compose_withholding_tax_authority,
    "company_income_tax_authority": compose_company_income_tax_authority,
}


def _is_authority_question(q: str) -> bool:
    return any(
        phrase in q
        for phrase in [
            "which tax authority",
            "what tax authority",
            "who handles",
            "which authority",
            "does firs or state",
            "does nrs or state",
            "who issues",
            "who receives",
            "who issues a tin",
            "which tax authority handles tin registration",
            "which authority handles tin registration",
            "which portal should i use",
            "which portal do i use",
        ]
    )


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

    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)
    action = _detect_action(question)

    # Authority routing questions first
    if q and (_is_authority_question(q) or "which tax authority handles personal income tax" in q):
        if _mentions_any(q, "personal income tax", "pit"):
            return compose_personal_income_tax_authority()
        if _mentions_any(q, "paye", "pay as you earn"):
            return compose_paye_authority()
        if _mentions_any(q, "vat", "value added tax"):
            return compose_vat_authority()
        if _mentions_any(q, "tcc", "tax clearance certificate"):
            return compose_tcc_authority()
        if _mentions_any(q, "tin", "tax identification number", "tax id"):
            return compose_tin_authority()
        if _mentions_any(q, "withholding tax", "wht"):
            return compose_withholding_tax_authority()
        if _mentions_any(q, "company income tax", "cit"):
            return compose_company_income_tax_authority()

    if "who issues a tcc" in q or "who issues tcc" in q:
        return compose_tcc_authority()
    if "who issues a tin" in q or "who issues tin" in q or "which tax authority handles tin registration" in q or "which authority handles tin registration" in q:
        return compose_tin_authority()
    if "which tax authority handles vat" in q:
        return compose_vat_authority()
    if "which tax authority receives withholding tax" in q or "which tax authority receives wht" in q:
        return compose_withholding_tax_authority()
    if "which tax authority handles company income tax" in q or "which tax authority handles cit" in q:
        return compose_company_income_tax_authority()
    if "does firs or state internal revenue handle paye" in q or "does nrs or state internal revenue handle paye" in q:
        return compose_paye_authority()
    if "what is a tin" in q or "what is tin" in q or "meaning of tin" in q or "define tin" in q or "what does tin mean" in q:
        return compose_tin_basic()
    if "what documents are needed for tin registration" in q or "what documents are required for tin registration" in q or "documents needed for tin registration" in q or "documents for tin registration" in q:
        return compose_tin_documents()

    # Specific topical process routing
    if _topic_in(topic_key, "tax_clearance_certificate", "tax clearance certificate", "tcc") or _mentions_any(q, "tcc", "tax clearance certificate"):
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if _topic_in(topic_key, "tin", "tax identification number", "tax id") or _mentions_any(q, "tin", "tax identification number", "tax id"):
        if "what is a tin" in q or "what is tin" in q or "meaning of tin" in q or "define tin" in q or "what does tin mean" in q:
            return compose_tin_basic()
        if "who issues a tin" in q or "who issues tin" in q or "which tax authority handles tin registration" in q or "which authority handles tin registration" in q:
            return compose_tin_authority()
        if "what documents are needed for tin registration" in q or "what documents are required for tin registration" in q or "documents needed for tin registration" in q or "documents for tin registration" in q:
            return compose_tin_documents()
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()

    if _topic_in(topic_key, "vat", "value added tax") or _mentions_any(q, "vat", "value added tax"):
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()

    if _topic_in(topic_key, "paye", "pay as you earn") or _mentions_any(q, "paye", "pay as you earn"):
        if action in {"file", "pay"}:
            return compose_paye_remittance_process()

    if _topic_in(topic_key, "withholding tax", "wht") or _mentions_any(q, "withholding tax", "wht"):
        if action == "deduct":
            return compose_withholding_tax_deduction()
        if action == "pay":
            return compose_withholding_tax_remittance()

    if _topic_in(topic_key, "company income tax", "cit") or _mentions_any(q, "company income tax", "cit"):
        if action == "file":
            return compose_company_income_tax_filing()
        if action == "pay":
            return compose_company_income_tax_payment()

    if intent_key in {"tax payment process", "tax_payment_process"} or action == "pay":
        return compose_tax_payment_process()

    if intent_key in {"tax filing process", "tax_filing_process"} or action == "file":
        return compose_tax_filing_process()

    return None
