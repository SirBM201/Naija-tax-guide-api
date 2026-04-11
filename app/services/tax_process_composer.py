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
    ],
    "apply": [
        r"\bapply\b",
        r"\bapplication\b",
        r"\bobtain\b",
        r"\brequest\b",
    ],
    "register": [
        r"\bregister\b",
        r"\bregistration\b",
        r"\benrol\b",
        r"\benroll\b",
        r"\benrollment\b",
        r"\bsign\s*up\b",
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
    "deduct": [
        r"\bdeduct\b",
        r"\bdeduction\b",
        r"\bwithhold\b",
        r"\bwithholding\b",
    ],
}


TOPIC_ALIASES = {
    "tcc": ["tax clearance certificate", "tax_clearance_certificate", "tcc"],
    "tin": ["tin", "tax identification number", "tax id", "tax identification"],
    "vat": ["vat", "value added tax"],
    "paye": ["paye", "pay as you earn", "paye tax"],
    "wht": [
        "withholding tax",
        "withholding",
        "wht",
        "wht tax",
    ],
    "cit": [
        "company income tax",
        "company income",
        "cit",
        "corporate income tax",
    ],
}


PROCESS_MAP = {}


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



def _question_mentions(question: Optional[str], aliases: list[str]) -> bool:
    q = _normalize(question)
    if not q:
        return False
    return any(_normalize(alias) in q for alias in aliases)



def _make_response(intent_type: str, source_label: str, answer: str) -> Dict:
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



def compose_tax_payment_process() -> Dict:
    return _make_response(
        "tax_payment_process",
        "General Nigerian Tax Payment Process",
        """
To pay tax in Nigeria, use this general flow:

1. Identify the exact tax type involved.
   - Personal Income Tax
   - Company Income Tax
   - Value Added Tax
   - Withholding Tax
   - PAYE

2. Confirm the correct tax authority.
   - The federal channel is used for taxes such as VAT and Company Income Tax.
   - The relevant State Internal Revenue Service usually handles Personal Income Tax and PAYE for employees in the state.

3. Make sure your registration details are in place, especially your TIN where required.

4. Confirm the payment basis:
   - self assessment
   - official assessment
   - deducted tax such as PAYE or Withholding Tax

5. Generate or confirm the payment reference where the authority requires one.

6. Pay through the approved portal, bank channel, or payment method accepted for that tax.

7. Keep the payment receipt together with the filing or assessment evidence for your records.

The exact payment process differs by tax type and authority, so confirm the correct portal before paying.
""",
    )



def compose_tax_filing_process() -> Dict:
    return _make_response(
        "tax_filing_process",
        "General Nigerian Tax Filing Process",
        """
To file tax in Nigeria, use this general flow:

1. Identify the exact tax type and filing period involved.

2. Confirm the correct tax authority and filing channel for that tax.

3. Gather the figures, schedules, and supporting records for the filing period.

4. Reconcile the numbers so the return matches the underlying records.

5. Complete the approved return form or online filing step carefully.

6. Submit the return within the applicable deadline.

7. Keep the acknowledgement, portal confirmation, and any related payment evidence for your records.

The exact filing process differs by tax type, so confirm the approved channel before submission.
""",
    )



def compose_tin_registration() -> Dict:
    return _make_response(
        "tin_registration",
        "TIN Registration Process",
        """
To register for a TIN in Nigeria:

1. Confirm whether you need a personal or business taxpayer registration path.

2. Gather the core identity or business details required for the registration.

3. Use the relevant official tax authority registration channel.

4. Complete the registration carefully and make sure the details match your identity or business records.

5. Submit the registration and keep the acknowledgement.

6. Once processed, confirm that the TIN has been issued correctly and keep the number safely for filing, payment, and compliance use.

If you already registered but do not know your TIN, use the authority's recovery or verification process instead of creating a duplicate record.
""",
    )



def compose_tin_verification() -> Dict:
    return _make_response(
        "tin_verification",
        "TIN Verification Process",
        """
To verify a TIN in Nigeria:

1. Use the official tax authority channel that issued or manages the TIN.

2. Open the TIN verification or taxpayer search option where available.

3. Enter the TIN exactly as issued.

4. Confirm that the returned taxpayer details match the correct person or business.

5. If the details do not match, resolve the issue with the issuing authority before relying on the number.

Keep a screenshot or confirmation page where available.
""",
    )



def compose_vat_registration_process() -> Dict:
    return _make_response(
        "vat_registration_process",
        "VAT Registration Process",
        """
Register for VAT through the approved federal VAT registration channel once the business falls within the applicable VAT rules.

Before registration:
- Confirm that the business activity falls within the applicable VAT registration framework.
- Prepare the business details and TIN needed for registration.

Registration steps:
1. Provide the required taxpayer and business information accurately.
2. Complete any activation or confirmation step required by the authority.
3. Keep the acknowledgement and any confirmation notice issued.

After registration:
- Make sure invoicing, record keeping, filing, and payment processes are aligned with VAT compliance.
""",
    )



def compose_vat_filing_process() -> Dict:
    return _make_response(
        "vat_filing_process",
        "VAT Filing Process",
        """
File VAT through the approved VAT filing channel for the relevant tax period.

Before filing:
- Confirm the VAT period involved.
- Gather the records for taxable sales, output VAT, input VAT where relevant, invoices, and supporting schedules.
- Reconcile the figures so the return matches your records.

Filing steps:
1. Submit the VAT return through the approved channel within the applicable deadline.
2. Where VAT is payable, complete payment through the approved payment channel.
3. Keep both the return evidence and payment evidence for your records.
""",
    )



def compose_vat_payment_process() -> Dict:
    return _make_response(
        "vat_payment_process",
        "VAT Payment Process",
        """
Pay VAT through the approved VAT payment channel of the federal tax authority that receives the return.

Before payment:
- Confirm the VAT period and amount due.
- Make sure the taxpayer profile, TIN, and VAT return details match the correct business.
- Generate or confirm the payment reference required by the official portal or payment channel.

Payment steps:
1. Use the approved VAT payment channel accepted by the relevant authority.
2. Pay the exact VAT amount due for the relevant period.
3. Keep the receipt, acknowledgement, or payment confirmation.

After payment:
- Keep the payment evidence together with the VAT return evidence for that period.
""",
    )



def compose_paye_remittance_process() -> Dict:
    return _make_response(
        "paye_remittance_process",
        "PAYE Remittance Process",
        """
Remit PAYE through the approved State Internal Revenue Service channel for the state that has the right to receive the payroll deduction.

Before remittance:
- Confirm the payroll period and the amount deducted.
- Make sure the employer and employee records match the PAYE schedule being remitted.
- Generate or confirm any payment or remittance reference required by the approved state channel.

Remittance steps:
1. Use the approved state PAYE remittance channel.
2. Remit the deducted PAYE within the applicable compliance window.
3. Keep the remittance receipt, acknowledgement, or portal confirmation.
4. Match the payment evidence to the payroll schedule and return for the same period.
""",
    )



def compose_tcc_application() -> Dict:
    return _make_response(
        "tcc_application",
        "TCC Application Process",
        """
To apply for a Tax Clearance Certificate in Nigeria:

1. Confirm the correct issuing tax authority for the taxpayer's case.

2. Make sure the taxpayer profile is up to date.
   - the TIN should be active where applicable
   - outstanding returns should be filed
   - outstanding liabilities, where due, should be settled or regularized

3. Log in to the official portal or eServices channel used by that authority for TCC requests.

4. Open the TCC application option and complete the request with the correct taxpayer details.

5. Upload or provide any supporting records required by the authority.

6. Submit the request and keep the acknowledgement or reference number.

7. Monitor the application status and download or collect the TCC once approved.
""",
    )



def compose_tcc_verification() -> Dict:
    return _make_response(
        "tcc_verification",
        "TCC Verification Process",
        """
To verify a Tax Clearance Certificate in Nigeria, use this practical flow:

1. Go to the official tax authority portal or eServices platform that issued the certificate.

2. Open the TCC verification option, receipt verification option, or taxpayer verification page where that authority provides one.

3. Enter the TCC number, reference number, or other identifier requested by the portal.

4. Confirm that the returned details match the taxpayer correctly, especially:
   - taxpayer name
   - TIN where shown
   - certificate or receipt reference
   - status or validity information where shown

5. If the portal cannot validate the TCC, or the details do not match, contact the issuing authority before relying on the certificate.

6. Keep a screenshot or confirmation page where available for your records.
""",
    )



def compose_withholding_tax_deduction_process() -> Dict:
    return _make_response(
        "withholding_tax_deduction_process",
        "Withholding Tax Deduction Process",
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
""",
    )



def compose_withholding_tax_remittance_process() -> Dict:
    return _make_response(
        "withholding_tax_remittance_process",
        "Withholding Tax Remittance Process",
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
""",
    )



def compose_company_income_tax_filing_process() -> Dict:
    return _make_response(
        "company_income_tax_filing_process",
        "Company Income Tax Filing Process",
        """
File Company Income Tax through the approved federal filing channel for the relevant accounting period.

Before filing:
- Confirm the accounting period involved and the return being prepared.
- Prepare the financial statements, tax computation schedules, and supporting records for the period.
- Confirm the company's taxable-profit position and any tax adjustments being applied.

Filing steps:
1. Complete the Company Income Tax return through the approved filing channel.
2. Upload or provide the computation and supporting documents required for the filing.
3. Submit the return within the applicable deadline and keep the acknowledgement or portal confirmation.
4. Where tax is payable, keep the filed return evidence together with the payment evidence for the same period.
""",
    )



def compose_company_income_tax_payment_process() -> Dict:
    return _make_response(
        "company_income_tax_payment_process",
        "Company Income Tax Payment Process",
        """
Pay Company Income Tax through the approved federal payment channel for the relevant assessment or self-computed liability.

Before payment:
- Confirm the accounting period, tax amount due, and the correct taxpayer details.
- Make sure the company's return, assessment, or computation supports the amount being paid.
- Generate or confirm any payment reference required by the official channel.

Payment steps:
1. Use the approved portal, bank channel, or payment method accepted for Company Income Tax.
2. Pay the exact CIT amount due for the relevant period or assessment.
3. Keep the receipt, acknowledgement, or portal confirmation as payment evidence.
4. Match the payment evidence to the corresponding return or assessment for the same period.
""",
    )


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tax_filing_process": compose_tax_filing_process,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "vat_registration_process": compose_vat_registration_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_payment_process": compose_vat_payment_process,
    "paye_remittance_process": compose_paye_remittance_process,
    "tcc_application": compose_tcc_application,
    "tcc_verification": compose_tcc_verification,
    "withholding_tax_deduction_process": compose_withholding_tax_deduction_process,
    "withholding_tax_remittance_process": compose_withholding_tax_remittance_process,
    "company_income_tax_filing_process": compose_company_income_tax_filing_process,
    "company_income_tax_payment_process": compose_company_income_tax_payment_process,
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
    q = _normalize(question)

    def topic_match(name: str) -> bool:
        aliases = TOPIC_ALIASES[name]
        return _topic_in(topic_key, *aliases) or _question_mentions(q, aliases)

    if topic_match("tcc"):
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if topic_match("tin"):
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()

    if topic_match("vat"):
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()
        if action == "pay":
            return compose_vat_payment_process()

    if topic_match("paye"):
        if action == "pay":
            return compose_paye_remittance_process()

    if topic_match("wht"):
        if action == "deduct":
            return compose_withholding_tax_deduction_process()
        if action == "pay":
            return compose_withholding_tax_remittance_process()

    if topic_match("cit"):
        if action == "file":
            return compose_company_income_tax_filing_process()
        if action == "pay":
            return compose_company_income_tax_payment_process()

    if intent_key in {"tax payment process", "tax_payment_process"}:
        return compose_tax_payment_process()
    if intent_key in {"tax filing process", "tax_filing_process"}:
        return compose_tax_filing_process()

    if action == "pay":
        return compose_tax_payment_process()
    if action == "file":
        return compose_tax_filing_process()

    return None


__all__ = [
    "compose_tax_payment_process",
    "compose_tax_filing_process",
    "compose_tin_registration",
    "compose_tin_verification",
    "compose_vat_registration_process",
    "compose_vat_filing_process",
    "compose_vat_payment_process",
    "compose_paye_remittance_process",
    "compose_tcc_application",
    "compose_tcc_verification",
    "compose_withholding_tax_deduction_process",
    "compose_withholding_tax_remittance_process",
    "compose_company_income_tax_filing_process",
    "compose_company_income_tax_payment_process",
    "PROCESS_MAP",
    "try_compose",
]
