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
   - FIRS usually handles many federal taxes.
   - State Internal Revenue Services usually handle Personal Income Tax and PAYE matters for employees in the state.

3. Make sure your registration details are in place, especially your TIN.

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

1. Confirm the exact tax type and filing period involved.

2. Confirm whether the filing is for:
   - an individual
   - an employer
   - a company

3. Confirm the correct tax authority.
   - Personal Income Tax is often handled at state level.
   - Some federal taxes are handled through FIRS.

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

If the tax type is specific, such as VAT or PAYE, the filing process should be tailored to that tax rather than treated as a generic filing question.
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

1. Confirm the VAT period you are filing for.

2. Gather the relevant records for that period, especially:
   - taxable sales
   - output VAT collected
   - input VAT where relevant
   - invoices and supporting records

3. Reconcile your figures before filing so the amounts are consistent with your records.

4. Use the approved VAT filing channel of the relevant tax authority.

5. Submit the VAT return within the applicable deadline.

6. Where VAT is payable, make payment through the approved payment channel and keep the receipt.

7. Keep both the filed return evidence and the payment evidence for your records.

If your question is about whether VAT applies at all, that should be answered first before filing steps.
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

1. Confirm the employees and payroll period involved.

2. Compute PAYE correctly for each employee based on the applicable rules.

3. Prepare the payroll schedule and supporting deduction records.

4. Use the correct state tax authority channel for PAYE filing and remittance.

5. Submit the required PAYE schedule or return where required.

6. Remit the PAYE amount through the approved payment channel.

7. Keep proof of filing, proof of remittance, and payroll deduction records.

PAYE issues are usually state-based, so make sure you are using the correct state revenue authority process.
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


PROCESS_MAP = {
    "tax_payment_process": compose_tax_payment_process,
    "tin_registration": compose_tin_registration,
    "tin_verification": compose_tin_verification,
    "tax_filing_process": compose_tax_filing_process,
    "vat_filing_process": compose_vat_filing_process,
    "vat_registration_process": compose_vat_registration_process,
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

    if _topic_in(topic_key, "paye", "pay as you earn") and action == "pay":
        return compose_paye_remittance_process()

    if intent_key in {"tax payment process", "tax_payment_process"} or action == "pay":
        return compose_tax_payment_process()

    if intent_key in {"tax filing process", "tax_filing_process"} or action == "file":
        return compose_tax_filing_process()

    return None
