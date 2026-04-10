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
}


def _normalize(text: Optional[str]) -> str:
    raw = str(text or "").strip().lower()
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


def compose_tax_payment_process() -> Dict:
    answer = """
To pay tax in Nigeria, use this general flow:

1. Identify the exact tax type involved.
   - Personal Income Tax
   - Company Income Tax
   - VAT
   - Withholding Tax
   - PAYE

2. Confirm the correct tax authority.
   - FIRS usually handles many federal taxes.
   - State Internal Revenue Services usually handle Personal Income Tax and PAYE matters for employees in the state.

3. Make sure your registration details are in place, especially your TIN.

4. Prepare and file the relevant return if that tax type requires a return before payment.

5. Generate the payment reference through the official portal or approved payment channel.

6. Pay through an approved method such as:
   - official tax portal
   - approved bank channel
   - approved payment platform where applicable

7. Keep the payment receipt and filing evidence for your records.

If you tell me the exact tax type, I can guide you more precisely.
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


def compose_tin_registration() -> Dict:
    answer = """
To obtain a Tax Identification Number (TIN) in Nigeria:

1. Identify whether you are registering as:
   - an individual
   - a business name
   - a company

2. Go through the appropriate tax authority or approved registration channel.

3. Prepare the common details usually required:
   - full name or business name
   - address
   - phone number
   - email where applicable
   - business registration details for a company or registered business

4. Submit the required identification or registration documents.

5. After successful registration, the TIN is issued and linked to your tax profile.

TIN is commonly required for filing returns, paying taxes, and dealing with official tax records.
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

3. Enter the TIN exactly as issued. If the portal allows it, you can also confirm using the taxpayer or business name.

4. Check that the returned taxpayer details match the correct person or business.

5. If the TIN does not validate or the details do not match, contact the issuing tax authority before using it for filing, payment, or compliance work.

Keep a screenshot or confirmation page where available for your records.
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


def compose_tax_filing_process() -> Dict:
    answer = """
To file tax in Nigeria, use this general process:

1. Determine the taxpayer type.
   - employee
   - self-employed individual
   - registered business
   - company

2. Confirm the correct tax authority.
   - Personal Income Tax is often handled at state level.
   - Some federal taxes are handled through FIRS.

3. Gather the records needed for the filing period, such as:
   - income records
   - expense records where relevant
   - payroll records where relevant
   - prior payments or credits
   - TIN and registration details

4. Identify the exact return to be filed.

5. Complete the return with the correct figures for the relevant period.

6. Submit the return through the approved channel.

7. Pay any amount due if payment is required.

8. Keep evidence of filing and payment.

If you tell me whether you are filing as an employee, freelancer, business owner, or company, I can narrow the steps further.
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


def compose_vat_filing_process() -> Dict:
    answer = """
To file VAT in Nigeria, use this general flow:

1. Confirm that VAT applies to your business activity.

2. Gather the records for the filing period:
   - sales subject to VAT
   - VAT charged to customers
   - input VAT where applicable
   - invoices and supporting records

3. Prepare the VAT return for the relevant period.

4. Submit the VAT return through the approved tax filing channel.

5. Pay any VAT due through the approved payment channel.

6. Keep the return confirmation, payment receipt, and supporting records.

If you want, I can also explain VAT in simpler terms or help you understand what records should be prepared before filing.
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


def compose_vat_registration_process() -> Dict:
    answer = """
To register for VAT in Nigeria, use this general flow:

1. Confirm that your business activity falls within the scope of VAT registration under the applicable rules.

2. Make sure your core registration details are ready, especially your business details and TIN.

3. Use the approved registration channel of the relevant tax authority.

4. Provide the required taxpayer and business information accurately.

5. Complete any activation or confirmation steps required by the authority.

6. Keep the registration acknowledgement and any confirmation message or certificate issued.

After registration, make sure your invoicing, record-keeping, filing, and payment process are aligned with VAT compliance.
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


def compose_paye_remittance_process() -> Dict:
    answer = """
To handle PAYE remittance in Nigeria, use this general process:

1. Confirm that you are acting as an employer.

2. Calculate the PAYE to be withheld from employee income for the period.

3. Prepare the employee payroll and deduction schedule.

4. Complete the required PAYE return or remittance schedule for the relevant authority.

5. Pay or remit the PAYE through the approved state tax authority channel.

6. Keep evidence of deduction, remittance, and filing for your records.

7. Make sure staff records and payroll records remain consistent with what was remitted.

If you want, I can explain PAYE step by step for a small business employer.
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


def compose_tcc_application() -> Dict:
    answer = """
To apply for a Tax Clearance Certificate (TCC) in Nigeria, use this practical flow:

1. Confirm the correct issuing tax authority.
   - For many personal income tax matters, this is usually the relevant State Internal Revenue Service.
   - For relevant federal matters, use the appropriate FIRS channel.

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

If you are using a specific state portal, I can also help you phrase a more portal-specific verification answer later.
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


def try_compose(
    *,
    question: Optional[str] = None,
    topic: Optional[str] = None,
    intent_type: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
):
    del lang, channel

    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)
    action = _detect_action(question)

    if topic_key == "tax_clearance_certificate":
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if topic_key == "tin":
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()

    if topic_key == "vat":
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()

    if topic_key == "paye" and action == "pay":
        return compose_paye_remittance_process()

    if intent_key == "tax_payment_process" or action == "pay":
        return compose_tax_payment_process()

    if intent_key == "tax_filing_process" or action == "file":
        return compose_tax_filing_process()

    return None
