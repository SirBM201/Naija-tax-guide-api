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
        r"\brequest\b",
    ],
    "register": [
        r"\bregister\b",
        r"\bregistration\b",
        r"\benrol\b",
        r"\benroll\b",
        r"\benrollment\b",
        r"\bget\b",
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


def compose_tax_payment_process() -> Dict:
    answer = """
To pay tax in Nigeria, use this general flow:

1. Identify the exact tax type involved.
   - Personal Income Tax
   - Company Income Tax
   - Value Added Tax
   - Withholding Tax
   - PAYE

2. Confirm the correct tax authority.
   - State Internal Revenue Services usually handle Personal Income Tax and PAYE matters for employees in the state.
   - Federal channels usually handle VAT, Company Income Tax, and many federal-side obligations.

3. Make sure your registration details are in place, especially your TIN where required.

4. Confirm the payment basis:
   - self-assessment
   - official assessment
   - deducted tax such as PAYE or withholding

5. Generate or confirm the payment reference where the authority requires one.

6. Pay through an approved method such as:
   - official tax portal
   - approved bank channel
   - approved payment platform where applicable

7. Keep the payment receipt and filing evidence for your records.

The exact payment process can differ by tax type and tax authority, so verify the applicable portal or payment channel before making payment.

Source: current official Nigerian tax payment guidance, including approved federal and state tax payment channels for the tax type involved.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tax_payment_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "General Nigerian Tax Payment Process",
            "grounded": True,
        },
    }


def compose_tax_filing_process() -> Dict:
    answer = """
To file tax in Nigeria, use this general process:

1. Confirm the exact tax type and filing period involved.

2. Confirm whether the filing is for:
   - an individual
   - an employer
   - a company

3. Confirm the correct tax authority.
   - Personal Income Tax is often handled at state level.
   - Many federal taxes are handled through the approved federal filing channel.

4. Gather the records needed for the filing period, such as:
   - income records
   - invoices
   - payroll schedules
   - prior payments
   - deductions or relief records where relevant

5. Compute the figures correctly before submitting.

6. Use the official filing portal or approved filing channel.

7. Submit the return and keep proof of filing.

8. Where tax is payable, complete payment and keep the receipt together with the filed return evidence.

If the tax type is specific, such as VAT, PAYE, Personal Income Tax, or Company Income Tax, the filing process should be tailored to that tax rather than treated as a generic filing question.

Source: current official Nigerian tax filing guidance and the approved filing channels for the tax type involved.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tax_filing_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "General Tax Filing Process",
            "grounded": True,
        },
    }


def compose_tin_registration() -> Dict:
    answer = """
To get or register for a TIN in Nigeria:

1. Confirm whether you need a personal or business tax registration path.

2. Gather the core details normally required for registration, such as:
   - legal name
   - phone number
   - address
   - business registration details where applicable

3. Use the relevant official tax authority registration channel.

4. Complete the taxpayer registration form carefully and make sure the details match your identity or business records.

5. Submit the registration and keep the acknowledgement.

6. Once processed, confirm that the TIN has been issued correctly and keep the number safely for filing, payment, and compliance use.

If you already registered but do not know your TIN, use the authority's recovery or verification process instead of creating a duplicate record.

Source: current official TIN registration guidance and the approved taxpayer-registration channel for the case involved.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tin_registration",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "TIN Registration Process",
            "grounded": True,
        },
    }


def compose_tin_verification() -> Dict:
    answer = """
To verify a TIN in Nigeria:

1. Use the official tax authority channel that issued or manages the TIN.

2. Open the TIN verification or taxpayer search option where available.

3. Enter the TIN exactly as issued. If the channel allows it, you may also confirm using the taxpayer or business name.

4. Check that the returned taxpayer details match the correct person or business.

5. If the TIN does not validate or the details do not match, contact the issuing tax authority before using it for filing, payment, or compliance work.

Keep a screenshot or confirmation page where available for your records.

Source: current official TIN verification guidance and the approved taxpayer-search or validation channel.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tin_verification",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "TIN Verification Process",
            "grounded": True,
        },
    }


def compose_vat_registration_process() -> Dict:
    answer = """
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

Source: current official Nigeria Revenue Service guidance, the Nigeria Tax Act 2025 framework, and the official VAT registration channel of the relevant tax authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "vat_registration_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "VAT Registration Process",
            "grounded": True,
        },
    }


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

Source: current official Nigeria Revenue Service guidance, the Nigeria Tax Act 2025 framework, and the official VAT filing channel of the relevant tax authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "vat_filing_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "VAT Filing Process",
            "grounded": True,
        },
    }


def compose_vat_payment_process() -> Dict:
    answer = """
Pay VAT through the approved VAT payment channel of the federal tax authority that receives the return.

Before payment:
- Confirm the VAT period and amount due from the return or assessment.
- Make sure the taxpayer profile, TIN, and VAT return details match the correct business.
- Generate or confirm the payment reference required by the official portal or payment channel.

Payment steps:
1. Use the approved VAT payment channel accepted by the relevant authority.
2. Pay the exact VAT amount due for the relevant period.
3. Keep the receipt, acknowledgement, or payment confirmation.

After payment:
- Keep the payment evidence together with the VAT return evidence for that period.
- If the portal still shows unpaid status, confirm whether the payment has posted correctly before assuming there is a failure.

What to do next:
1. Ask how to file VAT if the return has not yet been submitted.
2. Ask what records should support the VAT payment and return.
3. Ask whether the exact supply is taxable, exempt, or zero-rated before charging VAT next time.

Source: current official Nigeria Revenue Service guidance, the Nigeria Tax Act 2025 framework, and the official VAT payment channel of the relevant tax authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "vat_payment_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "VAT Payment Process",
            "grounded": True,
        },
    }


def compose_paye_remittance_process() -> Dict:
    answer = """
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

Source: current official State Internal Revenue Service PAYE guidance, employer payroll compliance rules, and the official PAYE filing and remittance channel of the relevant state tax authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "paye_remittance_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "PAYE Remittance Process",
            "grounded": True,
        },
    }


def compose_withholding_tax_remittance_process() -> Dict:
    answer = """
Remit Withholding Tax through the approved channel of the tax authority that receives the deduction for the payment category involved.

Before remittance:
- Confirm the exact payment type, gross amount, WHT rate used, and amount deducted.
- Make sure the payer and recipient details match the transaction records.
- Prepare the deduction schedule and any supporting payment documents.

Remittance steps:
1. Use the approved tax-authority channel for the relevant WHT category.
2. Submit any required schedule or transaction details together with the remittance.
3. Keep the receipt, acknowledgement, or portal confirmation after payment.
4. Issue or retain the evidence needed to support the recipient's tax-credit claim where applicable.

What to do next:
1. Ask who should deduct WHT in your case.
2. Ask what records should support the WHT deduction and remittance.
3. Ask what rate applies to the exact payment type involved.

Source: current official withholding-tax remittance guidance and the approved remittance channel for the relevant payment category.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "withholding_tax_remittance_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "Withholding Tax Remittance Process",
            "grounded": True,
        },
    }


def compose_company_income_tax_filing_process() -> Dict:
    answer = """
File Company Income Tax through the approved federal company-tax channel for the relevant accounting period.

Before filing:
- Confirm that the issue is a Company Income Tax matter and that the correct accounting period is being used.
- Prepare the taxable-profit computation, supporting schedules, and company details required for the return.
- Confirm what rate rule applies to the company category before finalizing the figures.

Filing steps:
1. Use the approved federal CIT filing portal or channel for the company.
2. Complete the return with the correct company details, computation figures, and supporting schedules.
3. Submit the filing within the applicable deadline.
4. Keep the acknowledgement, filing receipt, or portal confirmation.

What to do next:
1. Ask how to pay Company Income Tax after filing.
2. Ask what records should support the Company Income Tax return.
3. Ask what rate rule applies to the company category in your case.

Source: current official Federal Inland Revenue Service company-income-tax filing guidance and the approved CIT filing channel.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_filing_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "Company Income Tax Filing Process",
            "grounded": True,
        },
    }


def compose_company_income_tax_payment_process() -> Dict:
    answer = """
Pay Company Income Tax through the approved federal payment channel for the company's return or assessment.

Before payment:
- Confirm the accounting period, company details, and amount due from the return or assessment.
- Make sure the payment reference and taxpayer details match the company profile.
- Keep the computation and filing details ready in case the payment must be tied back to the submitted return.

Payment steps:
1. Use the approved federal CIT payment channel or portal accepted by the authority.
2. Pay the exact amount due for the relevant period.
3. Keep the receipt, acknowledgement, or payment confirmation.
4. Match the payment evidence with the related filing or assessment for that same period.

What to do next:
1. Ask how to file Company Income Tax if the return has not yet been submitted.
2. Ask what records should support the Company Income Tax payment.
3. Ask what rate rule applies to the company category in your case.

Source: current official Federal Inland Revenue Service company-income-tax payment guidance and the approved CIT payment channel.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_payment_process",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "Company Income Tax Payment Process",
            "grounded": True,
        },
    }


def compose_tcc_application() -> Dict:
    answer = """
To apply for a Tax Clearance Certificate (TCC) in Nigeria, use this practical flow:

1. Confirm the correct issuing tax authority.
   - For many personal income tax matters, this is usually the relevant State Internal Revenue Service.
   - For relevant federal matters, use the appropriate federal channel.

2. Make sure your tax profile is up to date.
   - your TIN should be active
   - outstanding returns should be filed
   - outstanding tax liabilities, where due, should be settled or regularized

3. Log in to the official tax authority portal or eServices platform where TCC requests are handled.

4. Open the TCC application option and complete the request with the correct taxpayer details.

5. Upload or provide any supporting records required by the authority.

6. Submit the request and keep the acknowledgement or reference number.

7. Monitor the application status and download or collect the TCC once approved.

If the portal does not approve the request immediately, check whether there are missing filings, unpaid liabilities, or profile mismatches that need to be resolved first.

Source: current official TCC application guidance and the approved portal or eServices channel of the issuing tax authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tcc_application",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "TCC Application Process",
            "grounded": True,
        },
    }


def compose_tcc_verification() -> Dict:
    answer = """
To verify a Tax Clearance Certificate (TCC) in Nigeria, use this practical flow:

1. Go to the official tax authority portal or eServices platform that issued the certificate.

2. Open the TCC verification option, receipt verification option, or taxpayer verification page where that authority provides one.

3. Enter the TCC number, reference number, or other identifier requested by the portal.

4. Confirm that the returned details match the taxpayer correctly, especially:
   - taxpayer name
   - TIN where shown
   - certificate or receipt reference
   - status or validity information where shown

5. If the portal cannot validate the TCC, or the details do not match, contact the issuing tax authority before relying on the certificate for compliance, contracts, banking, or clearance purposes.

6. Keep a screenshot or confirmation page where available for your records.

Source: current official TCC verification guidance and the approved portal or eServices validation channel of the issuing authority.
""".strip()

    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "tcc_verification",
            "answer_mode": "process",
            "source_type": "process_composer",
            "source_label": "TCC Verification Process",
            "grounded": True,
        },
    }


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tax_filing_process": compose_tax_filing_process,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "vat_registration_process": compose_vat_registration_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_payment_process": compose_vat_payment_process,
    "paye_remittance_process": compose_paye_remittance_process,
    "withholding_tax_remittance_process": compose_withholding_tax_remittance_process,
    "company_income_tax_filing_process": compose_company_income_tax_filing_process,
    "company_income_tax_payment_process": compose_company_income_tax_payment_process,
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

    if _topic_in(topic_key, "tax clearance certificate", "tax_clearance_certificate", "tcc"):
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if _topic_in(topic_key, "tin", "tax identification number", "tax id"):
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()

    if _topic_in(topic_key, "vat", "value added tax", "value_added_tax"):
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()
        if action == "pay":
            return compose_vat_payment_process()

    if _topic_in(topic_key, "paye", "pay as you earn", "payroll tax"):
        if action in {"pay", "file"}:
            return compose_paye_remittance_process()

    if _topic_in(topic_key, "withholding tax", "withholding_tax", "wht"):
        if action == "pay":
            return compose_withholding_tax_remittance_process()

    if _topic_in(topic_key, "company income tax", "company_income_tax", "companies income tax", "cit"):
        if action == "file":
            return compose_company_income_tax_filing_process()
        if action == "pay":
            return compose_company_income_tax_payment_process()

    if _topic_in(topic_key, "personal income tax", "personal_income_tax", "pit"):
        if action == "file":
            return compose_tax_filing_process()
        if action == "pay":
            return compose_tax_payment_process()

    if intent_key in {"tax payment process", "tax_payment_process"} or action == "pay":
        return compose_tax_payment_process()

    if intent_key in {"tax filing process", "tax_filing_process"} or action == "file":
        return compose_tax_filing_process()

    return None
