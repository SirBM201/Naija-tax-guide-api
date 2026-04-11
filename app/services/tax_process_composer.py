from __future__ import annotations

import re
from typing import Dict, Optional


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
    ],
}


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


def _mentions_any(text: Optional[str], *patterns: str) -> bool:
    q = _normalize(text)
    return any(re.search(pattern, q) for pattern in patterns)


def _is_records_question(question: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\brecords?\b",
        r"\bdocumentation\b",
        r"\bwhat should i keep\b",
        r"\bkeep .*record\b",
        r"\bevidence\b",
        r"\bschedule\b",
    )


def _is_payroll_records_context(question: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bpayroll\b",
        r"\bpaye\b",
    ) and _is_records_question(question)




def _is_withholding_definition_question(question: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bwhat is withholding tax\b",
        r"\bwhat is wht\b",
        r"\bmeaning of withholding tax\b",
        r"\bdefine withholding tax\b",
        r"\bwhat does withholding tax mean\b",
        r"\bwhat does wht mean\b",
    )


def _is_withholding_deductor_question(question: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bwho must deduct withholding tax\b",
        r"\bwho deducts withholding tax\b",
        r"\bwho should deduct withholding tax\b",
        r"\bwho deducts wht\b",
        r"\bwho must deduct wht\b",
        r"\bwho is responsible for withholding tax\b",
    )


def _is_withholding_rate_question(question: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bwithholding tax rate\b",
        r"\bwht rate\b",
        r"\brate of withholding tax\b",
        r"\bpercentage of withholding tax\b",
        r"\bhow much is withholding tax\b",
    )


def _is_withholding_deduction_process_question(question: Optional[str], action: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bhow do i deduct withholding tax\b",
        r"\bhow to deduct withholding tax\b",
        r"\bhow do i deduct wht\b",
        r"\bhow to deduct wht\b",
        r"\bwithholding tax deduction\b",
        r"\bdeduction steps?\b",
    ) or (
        action == "deduct"
        and not _is_withholding_definition_question(question)
        and not _is_withholding_deductor_question(question)
        and not _is_withholding_rate_question(question)
    )


def _is_withholding_remittance_process_question(question: Optional[str], action: Optional[str]) -> bool:
    return _mentions_any(
        question,
        r"\bhow do i remit withholding tax\b",
        r"\bhow to remit withholding tax\b",
        r"\bhow do i remit wht\b",
        r"\bhow to remit wht\b",
        r"\bwithholding tax remittance\b",
        r"\bremit wht\b",
    ) or action in {"pay", "file"}

def compose_tax_payment_process() -> Dict:
    answer = """
To pay tax in Nigeria, use this general flow:

1. Identify the exact tax type involved.
2. Confirm the correct tax authority.
3. Make sure your registration details are in place, especially your TIN.
4. Confirm the payment basis and the amount due.
5. Generate or confirm the payment reference where the authority requires one.
6. Pay through the approved portal, bank channel, or payment platform accepted by the authority.
7. Keep the payment receipt and filing evidence for your records.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tax_payment_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "General Nigerian Tax Payment Process", "grounded": True}}


def compose_tin_registration() -> Dict:
    answer = """
To get or register for a TIN in Nigeria:

1. Confirm whether you need a personal or business registration path.
2. Gather the core identity and business details required.
3. Use the relevant official tax authority registration channel.
4. Complete the registration form carefully.
5. Submit the registration and keep the acknowledgement.
6. Confirm that the TIN has been issued correctly and keep it safely.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tin_registration", "answer_mode": "process", "source_type": "process_composer", "source_label": "TIN Registration Process", "grounded": True}}


def compose_tin_verification() -> Dict:
    answer = """
To verify a TIN in Nigeria:

1. Use the official tax authority channel that issued or manages the TIN.
2. Open the TIN verification or taxpayer search option where available.
3. Enter the TIN exactly as issued.
4. Check that the returned taxpayer details match the correct person or business.
5. Keep a screenshot or confirmation page where available.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tin_verification", "answer_mode": "process", "source_type": "process_composer", "source_label": "TIN Verification Process", "grounded": True}}


def compose_tax_filing_process() -> Dict:
    answer = """
To file tax in Nigeria, use this general process:

1. Confirm the exact tax type and filing period involved.
2. Confirm the correct tax authority.
3. Gather the records needed for the filing period.
4. Compute the figures correctly before submitting.
5. Use the official filing portal or approved filing channel.
6. Submit the return and keep proof of filing.
7. Where tax is payable, complete payment and keep the receipt.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tax_filing_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "General Tax Filing Process", "grounded": True}}


def compose_vat_filing_process() -> Dict:
    answer = """
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
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "vat_filing_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "VAT Filing Process", "grounded": True}}


def compose_vat_registration_process() -> Dict:
    answer = """
Register for VAT through the approved registration channel of the relevant tax authority once your business falls within the scope of VAT registration.

Before registration:
- Confirm that your business activity falls within the applicable VAT registration rules.
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

Source: official VAT registration and compliance channel of the relevant tax authority.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "vat_registration_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "VAT Registration Process", "grounded": True}}


def compose_paye_remittance_process() -> Dict:
    answer = """
File and remit PAYE through the approved channel of the relevant State Internal Revenue Service for the payroll period involved.

Before filing or remittance:
- Confirm the employees and payroll period involved.
- Compute PAYE correctly for each employee.
- Prepare the payroll schedule and deduction records.

Process steps:
1. Submit the required PAYE return or schedule where required.
2. Remit the PAYE amount through the approved payment channel.
3. Keep proof of filing, proof of remittance, and payroll records.

What to do next:
1. Ask who should deduct PAYE in your case.
2. Ask what payroll records should be kept for PAYE.
3. Ask what state tax authority should receive the PAYE return.

Source: current official State Internal Revenue Service PAYE filing and remittance channel.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "paye_remittance_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "PAYE Filing and Remittance Process", "grounded": True}}


def compose_paye_records() -> Dict:
    answer = """
Keep the payroll and deduction records that support PAYE computation, filing, and remittance for each payroll period.

Records you should normally keep:
- payroll register or payroll schedule for the period
- employee pay details showing gross pay, deductions, and net pay
- PAYE computation support for each employee where applicable
- PAYE return or schedule submitted to the relevant State Internal Revenue Service
- payment receipt, remittance acknowledgement, or portal confirmation

Practical rule:
- Keep records in a way that lets you trace the PAYE deducted, the return filed, and the amount remitted for the same payroll period.
- Where employee details or payroll treatment change, keep the updated records that explain the change.

What to do next:
1. Ask how to file or remit PAYE after deduction.
2. Ask who should deduct PAYE in your case.
3. Ask what to do if payroll records do not match the PAYE return.

Source: current official State Internal Revenue Service PAYE guidance, employer payroll compliance rules, and the official PAYE filing and remittance channel of the relevant state tax authority.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "paye_records", "answer_mode": "process", "source_type": "process_composer", "source_label": "PAYE Records", "grounded": True}}


def compose_tcc_application() -> Dict:
    answer = """
Apply for a Tax Clearance Certificate through the official portal or eServices channel of the tax authority that manages your tax record.

Before you apply:
- Make sure the TIN is active.
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
2. Confirm which tax authority should issue your TCC in your case.
3. Check what a TCC is commonly used for in practice.

Source: official State Internal Revenue Service or FIRS portal/eServices channel that handles TCC issuance or verification.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tcc_application", "answer_mode": "process", "source_type": "process_composer", "source_label": "TCC Application Process", "grounded": True}}


def compose_tcc_verification() -> Dict:
    answer = """
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

Source: official State Internal Revenue Service or FIRS portal/eServices channel that handles TCC issuance or verification.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "tcc_verification", "answer_mode": "process", "source_type": "process_composer", "source_label": "TCC Verification Process", "grounded": True}}




def compose_withholding_tax_definition() -> Dict:
    answer = """
Withholding Tax (WHT) in Nigeria is a tax deduction taken at source from certain qualifying payments and then remitted to the relevant tax authority on behalf of the recipient.

What it is:
- WHT usually works as a deduction-and-remittance mechanism linked to the underlying income tax system.
- It is not one single flat rule for every payment type.
- The exact treatment depends on the nature of the payment, the recipient, and the rule that applies to that payment category.

Practical rule:
- First identify the exact payment type before deciding whether WHT applies, who should deduct it, and what rate should be used.

What to do next:
1. Ask whether the exact payment in your case should attract WHT.
2. Ask who should deduct WHT for that payment.
3. Ask what rate applies to that payment category.

Source: current official withholding-tax deduction, remittance, and credit-treatment guidance of the relevant tax authority.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_definition", "answer_mode": "process", "source_type": "process_composer", "source_label": "Withholding Tax Basics", "grounded": True}}


def compose_withholding_tax_deductor_rule() -> Dict:
    answer = """
The payer is usually the party that must deduct Withholding Tax when making a payment that falls within a withholding category under the applicable rule.

Who this usually affects:
- businesses, organizations, or other payers making qualifying payments
- payers who must deduct the WHT before paying the net amount to the recipient

Practical rule:
- Do not deduct WHT only because a payment is business-related.
- First confirm that the exact payment category is one that attracts WHT.
- Then confirm the correct rate and the correct authority that should receive the remittance.

What to do next:
1. Ask whether the exact payment in your case attracts WHT.
2. Ask what rate applies to that payment category.
3. Ask how to remit WHT after deduction.

Source: current official withholding-tax deduction and remittance guidance for qualifying payments.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_deductor_rule", "answer_mode": "process", "source_type": "process_composer", "source_label": "Who Deducts Withholding Tax", "grounded": True}}


def compose_withholding_tax_rate_rule() -> Dict:
    answer = """
There is no single universal Withholding Tax rate for every payment in Nigeria. The applicable rate depends on the exact payment category, the recipient, and the current rule in force.

Important note:
- Do not apply one general WHT rate across all contracts, services, rents, interest, dividends, commissions, or similar payments.
- The correct rate must be confirmed against the exact payment type and the current applicable guidance.

Practical rule:
- Identify the exact nature of the payment first, then confirm the current WHT rate for that specific category before deduction.

What to do next:
1. Ask what rate applies to your exact payment type.
2. Ask whether that payment should attract WHT at all.
3. Ask how to remit WHT once the deduction is made.

Source: current official withholding-tax schedules and payment-category guidance of the relevant tax authority.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_rate_rule", "answer_mode": "process", "source_type": "process_composer", "source_label": "Withholding Tax Rate Basics", "grounded": True}}

def compose_withholding_tax_deduction_process() -> Dict:
    answer = """
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

Source: current official withholding-tax deduction and payment-category guidance.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_deduction_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "Withholding Tax Deduction Process", "grounded": True}}


def compose_withholding_tax_remittance_process() -> Dict:
    answer = """
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

Source: current official withholding-tax remittance and credit-support guidance.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_remittance_process", "answer_mode": "process", "source_type": "process_composer", "source_label": "Withholding Tax Remittance Process", "grounded": True}}


def compose_withholding_tax_records() -> Dict:
    answer = """
Keep the payment and deduction records that support Withholding Tax computation, remittance, and credit support for the transaction involved.

Records you should normally keep:
- contract, invoice, payment instruction, or source document for the transaction
- gross amount, WHT amount deducted, and net amount paid
- WHT computation support showing how the deduction was calculated
- remittance receipt, acknowledgement, or portal confirmation
- credit note, receipt, or other support given to the recipient where applicable

Practical rule:
- Keep records in a way that lets you trace the original payment, the WHT deducted, the remittance made, and the evidence that supports the recipient's tax-credit position.

What to do next:
1. Ask how to remit WHT after deduction.
2. Ask who should deduct WHT for the payment in your case.
3. Ask what rate applies to that payment category.

Source: current official withholding-tax deduction, remittance, and tax-credit support guidance.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_records", "answer_mode": "process", "source_type": "process_composer", "source_label": "Withholding Tax Records", "grounded": True}}


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "tax_filing_process": compose_tax_filing_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_registration_process": compose_vat_registration_process,
    "paye_remittance_process": compose_paye_remittance_process,
    "paye_records": compose_paye_records,
    "tcc_application": compose_tcc_application,
    "tcc_verification": compose_tcc_verification,
    "withholding_tax_definition": compose_withholding_tax_definition,
    "withholding_tax_deductor_rule": compose_withholding_tax_deductor_rule,
    "withholding_tax_rate_rule": compose_withholding_tax_rate_rule,
    "withholding_tax_deduction_process": compose_withholding_tax_deduction_process,
    "withholding_tax_remittance_process": compose_withholding_tax_remittance_process,
    "withholding_tax_records": compose_withholding_tax_records,
}


def try_compose(intent: Optional[str] = None, *, question: Optional[str] = None, topic: Optional[str] = None, intent_type: Optional[str] = None, lang: str = "en", channel: str = "web"):
    del lang, channel

    if intent and not question and not topic and not intent_type:
        fn = PROCESS_MAP.get(_normalize(intent).replace(" ", "_"))
        return fn() if fn else None

    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)
    action = _detect_action(question)

    if _topic_in(topic_key, "tax clearance certificate", "tax_clearance_certificate", "tcc"):
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
            return compose_tax_payment_process()

    if _topic_in(topic_key, "paye", "pay as you earn"):
        if _is_records_question(question):
            return compose_paye_records()
        if action in {"pay", "file"}:
            return compose_paye_remittance_process()

    if _is_payroll_records_context(question):
        return compose_paye_records()

    if _topic_in(topic_key, "withholding tax", "withholding_tax", "wht", "withholding"):
        if _is_records_question(question):
            return compose_withholding_tax_records()
        if _is_withholding_rate_question(question):
            return compose_withholding_tax_rate_rule()
        if _is_withholding_deductor_question(question):
            return compose_withholding_tax_deductor_rule()
        if _is_withholding_remittance_process_question(question, action):
            return compose_withholding_tax_remittance_process()
        if _is_withholding_deduction_process_question(question, action):
            return compose_withholding_tax_deduction_process()
        if _is_withholding_definition_question(question):
            return compose_withholding_tax_definition()

    if intent_key in {"tax payment process", "tax_payment_process"} or action == "pay":
        return compose_tax_payment_process()

    if intent_key in {"tax filing process", "tax_filing_process"} or action == "file":
        return compose_tax_filing_process()

    return None
