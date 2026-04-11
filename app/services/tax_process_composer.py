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
        r"\bconfirm\b",
        r"\bcheck\b",
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

TOPIC_ALIASES = {
    "tcc": ["tax clearance certificate", "tax_clearance_certificate", "tcc"],
    "tin": ["tin", "tax identification number", "tax id", "tax identification"],
    "vat": ["vat", "value added tax"],
    "paye": ["paye", "pay as you earn", "personal income tax payroll"],
    "wht": ["withholding tax", "withholding", "wht", "wht tax"],
    "cit": ["company income tax", "company income", "cit", "corporate income tax"],
    "pit": ["personal income tax", "pit"],
}

PROCESS_MAP = {}


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


def _is_authority_question(q: str) -> bool:
    qn = _normalize(q)
    return any(
        phrase in qn
        for phrase in [
            "which tax authority",
            "what tax authority",
            "who handles",
            "which authority",
            "does firs or state",
            "does nrs or state",
            "who issues",
            "who receives",
            "who should receive",
            "which portal should i use",
            "which portal do i use",
            "who should i pay to",
            "which authority handles",
        ]
    )


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


# -------------------------------------------------
# Authority routing
# -------------------------------------------------


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
- If the question is whether NRS/FIRS or a State Internal Revenue Service handles PAYE, start from the state personal-income-tax side.
- Then confirm the exact state authority that should receive the PAYE return and remittance in the case.

What to do next:
1. Ask which state authority should receive the PAYE return in your case.
2. Ask who must deduct PAYE on the payroll involved.
3. Ask which state portal or remittance channel should be used.
""",
        "paye_authority",
        "PAYE Authority Routing",
    )


def compose_vat_authority() -> Dict:
    return _answer(
        """
VAT is handled through the federal tax authority channel, currently the Nigeria Revenue Service VAT administration channel and its approved service portals.

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
- For relevant federal cases, the approved Nigeria Revenue Service TCC eServices channel is used.

Practical rule:
- Do not assume every TCC must come from only one authority.
- First confirm whether the taxpayer's case is being handled on the state personal-income-tax side or on the relevant federal side, then use the issuing authority's TCC portal or eServices channel.

What to do next:
1. Ask which authority should issue the TCC in your case.
2. Ask how to apply for the TCC on that authority's approved portal.
3. Ask how to verify the issued TCC before using it.
""",
        "tcc_authority",
        "TCC Authority Routing",
    )


def compose_tin_authority() -> Dict:
    return _answer(
        """
The tax authority or official channel that issues or manages a TIN depends on the taxpayer's registration path and the authority channel being used for that taxpayer record.

What this usually means:
- You should use the approved TIN registration or TIN verification channel that matches the taxpayer's case.
- The Nigeria Revenue Service and Joint Revenue Board infrastructure are commonly part of the TIN administration path.
- The correct route should be confirmed before starting a fresh registration or relying on an existing TIN.

Practical rule:
- Do not assume every TIN question belongs only to one portal without checking the taxpayer type and the registration context.
- First confirm whether you are asking about TIN registration, TIN verification, or recovery of an already-issued TIN, then use the matching official channel.

What to do next:
1. Ask how to register for a TIN in your case.
2. Ask how to verify an issued TIN before using it.
3. Ask what documents should support the TIN registration request.
""",
        "tin_authority",
        "TIN Authority Routing",
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
""",
        "withholding_tax_authority",
        "Withholding Tax Authority Routing",
    )


def compose_company_income_tax_authority() -> Dict:
    return _answer(
        """
Company Income Tax is handled through the federal tax authority channel, currently the Nigeria Revenue Service company-income-tax administration channel.

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
""",
        "company_income_tax_authority",
        "Company Income Tax Authority Routing",
    )


# -------------------------------------------------
# TIN
# -------------------------------------------------


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
""",
        "tin_basic",
        "TIN Definition",
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
""",
        "tin_documents",
        "TIN Registration Documents",
    )


def compose_tin_registration() -> Dict:
    return _answer(
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
        "tin_registration",
        "TIN Registration Process",
    )


def compose_tin_verification() -> Dict:
    return _answer(
        """
To verify a TIN in Nigeria:

1. Use the official tax authority channel that issued or manages the TIN.
2. Open the TIN verification or taxpayer search option where available.
3. Enter the TIN exactly as issued.
4. Confirm that the returned taxpayer details match the correct person or business.
5. If the details do not match, resolve the issue with the issuing authority before relying on the number.

Keep a screenshot or confirmation page where available.

What to do next:
1. Ask how to get a TIN as an individual or business.
2. Ask who issues or manages the TIN in your case.
3. Ask what registration details may be required.
""",
        "tin_verification",
        "TIN Verification Process",
    )


# -------------------------------------------------
# VAT
# -------------------------------------------------


def compose_vat_basic() -> Dict:
    return _answer(
        """
VAT in Nigeria means Value Added Tax.

What it is:
- VAT is a consumption tax that generally applies to taxable supplies of goods and services under the applicable VAT rules.
- It is not the same thing as Company Income Tax, PAYE, or Withholding Tax.
- The correct VAT treatment depends on the exact supply, the taxpayer profile, and the rule that applies to that transaction.

Practical rule:
- Do not assume every business receipt automatically carries VAT.
- First confirm whether the exact supply is taxable, exempt, or zero-rated before charging or filing VAT on it.

What to do next:
1. Ask whether your business or transaction should comply with VAT.
2. Ask how to register for VAT if the registration rule applies.
3. Ask how to file or pay VAT for the relevant period.
""",
        "vat_basic",
        "VAT Definition",
    )


def compose_vat_obligation() -> Dict:
    return _answer(
        """
A person or business generally has to comply with VAT when it makes taxable supplies that fall within the current Nigerian VAT rules.

Who this usually affects:
- businesses making taxable supplies of goods or services
- businesses that should register, charge VAT where applicable, file returns, and remit VAT through the official channel

Important limits:
- Not every supply should be charged at the standard VAT rate.
- Some supplies may be exempt or zero-rated under the current law.
- The correct answer depends on the exact supply, the nature of the taxpayer, and the current legal treatment of that transaction.

Practical rule:
- First confirm whether the exact good or service is taxable. If it is taxable, move to registration, invoicing, filing, and remittance compliance.

What to do next:
1. Ask whether your exact business activity or transaction is taxable for VAT.
2. Ask how to register for VAT if the registration rule applies.
3. Ask what records should support your VAT filing and payment.
""",
        "vat_obligation",
        "VAT Obligation",
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
""",
        "vat_filing_process",
        "VAT Filing Process",
    )


def compose_vat_payment_process() -> Dict:
    return _answer(
        """
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
1. Ask what VAT means in Nigeria.
2. Ask how to file VAT for the same period.
3. Ask what records you should keep for VAT compliance.
""",
        "vat_payment_process",
        "VAT Payment Process",
    )


def compose_vat_records() -> Dict:
    return _answer(
        """
Keep the sales, invoice, tax-computation, filing, and payment records that support your VAT position for each relevant period.

Records you should normally keep:
- sales records and transaction schedules for taxable, exempt, or zero-rated supplies
- tax invoices and any supporting commercial documents tied to the supply
- VAT computation schedules showing output VAT, input VAT where relevant, and the amount reported
- filed VAT return, acknowledgement, or portal confirmation
- payment receipt or other official evidence supporting the VAT settlement for the same period

Practical rule:
- Keep records in a form that lets you trace the underlying transaction, the VAT treatment applied, the return filed, and any payment made for that same period.
- Where the taxpayer treats a supply as exempt or zero-rated, keep the records that support that treatment.

What to do next:
1. Ask how to file VAT for the relevant period.
2. Ask how to pay VAT once the amount due is confirmed.
3. Ask whether the exact supply is taxable, exempt, or zero-rated.
""",
        "vat_records",
        "VAT Records",
    )


def compose_vat_zero_rated() -> Dict:
    return _answer(
        """
A zero-rated VAT treatment means the supply falls within a VAT category that is charged at a zero rate under the applicable rule.

What this means:
- A zero-rated supply is not treated the same way as a standard-rated taxable supply.
- It is also not automatically the same thing as an exempt supply.
- You should confirm that the exact good or service falls into a zero-rated category before using that treatment.

Practical rule:
- Do not label a supply zero-rated just because no VAT is visibly charged in practice.
- First confirm that the law and current guidance place that exact supply in a zero-rated category, then keep the records supporting that treatment.

What to do next:
1. Ask whether your exact supply is zero-rated or exempt.
2. Ask what records should support a zero-rated VAT treatment.
3. Ask how the supply should be shown in the VAT return.
""",
        "vat_zero_rated",
        "Zero-Rated VAT",
    )


def compose_vat_exemption() -> Dict:
    return _answer(
        """
An exempt supply is different from a zero-rated supply for VAT, so you should confirm the exact category before charging VAT or filing the return.

What this means:
- If a supply is exempt, the VAT treatment is different from a supply that is zero-rated.
- You should not assume that a supply is exempt or zero-rated just because VAT is not visibly charged in practice.

Practical rule:
- Check the current official schedule or legal list for the exact good or service involved.
- If the supply is not clearly listed under the current exemption or zero-rating treatment, do not guess. Confirm with the current official authority guidance before invoicing or filing.

What to do next:
1. Ask about the exact good or service you want to classify for VAT.
2. Ask whether the standard VAT rate should be charged on that supply.
3. Ask how the supply should be treated in VAT filing after classification.
""",
        "vat_exemption",
        "VAT Exemption",
    )


# -------------------------------------------------
# PIT
# -------------------------------------------------


def compose_personal_income_tax_basic() -> Dict:
    return _answer(
        """
Personal Income Tax in Nigeria is the tax charged on the income of an individual under the applicable personal-income-tax rules.

What it is:
- Personal Income Tax is an individual's income tax, not a company profit tax.
- It may be handled through PAYE where the income is employment income, or through the individual's direct filing path where that is the applicable route.
- The correct treatment depends on the income type, the taxpayer's status, and the current rule that applies.

Practical rule:
- First confirm that the issue is about an individual's income and not VAT, Company Income Tax, or Withholding Tax.
- Then confirm whether the case should be handled under PAYE or another personal-income-tax path.

What to do next:
1. Ask who pays Personal Income Tax in your case.
2. Ask what authority handles Personal Income Tax for the taxpayer involved.
3. Ask how to file or pay Personal Income Tax where applicable.
""",
        "personal_income_tax_basic",
        "Personal Income Tax Definition",
    )


def compose_personal_income_tax_obligation() -> Dict:
    return _answer(
        """
Individuals with income that falls within the applicable Personal Income Tax rules are the ones expected to comply with Personal Income Tax in Nigeria.

Who this usually affects:
- individuals earning taxable income under the applicable personal-income-tax rules
- employers where the income is handled through PAYE deduction for employees
- individuals who may need to file directly where their tax position is not handled only through payroll deduction

Practical rule:
- Do not assume every income question should be treated as PAYE.
- First identify the income type and the taxpayer context, then confirm whether the compliance route is PAYE, direct personal-income-tax filing, or another lawful path.

What to do next:
1. Ask whether the issue is about PAYE or direct personal-income-tax filing.
2. Ask which state tax authority should handle the case.
3. Ask what records should support the Personal Income Tax position.
""",
        "personal_income_tax_obligation",
        "Personal Income Tax Obligation",
    )


def compose_personal_income_tax_rate() -> Dict:
    return _answer(
        """
There is no one-line shortcut that should be used blindly for every Personal Income Tax question. The correct rate treatment depends on the taxable-income computation and the current personal-income-tax rules that apply to the individual.

Important note:
- Do not guess the rate treatment from salary alone without first confirming the taxpayer's taxable-income position.
- The correct computation should follow the current personal-income-tax framework applicable to the individual and the income involved.

Practical rule:
- Confirm the individual's income type, deduction position, and the current rule that applies before computing or quoting a Personal Income Tax liability.

What to do next:
1. Ask how to compute Personal Income Tax for the income involved.
2. Ask whether the case should be handled through PAYE.
3. Ask what records should support the Personal Income Tax computation.
""",
        "personal_income_tax_rate",
        "Personal Income Tax Rate",
    )


def compose_personal_income_tax_filing() -> Dict:
    return _answer(
        """
File Personal Income Tax through the approved channel of the State Internal Revenue Service that has the taxing right in the case.

Before filing:
- Confirm that the issue is a Personal Income Tax matter and not Company Income Tax or VAT.
- Confirm the correct state authority and the filing period involved.
- Gather the income details, computation support, and any records required for the filing.

Filing steps:
1. Use the approved state filing portal or filing channel for the taxpayer's case.
2. Complete the return or filing process with the correct taxpayer details and figures.
3. Submit the filing within the applicable deadline.
4. Keep the acknowledgement, confirmation page, or filing receipt.

What to do next:
1. Ask how to pay Personal Income Tax after filing.
2. Ask what records should be kept for Personal Income Tax.
3. Ask whether the case should instead be handled through PAYE.
""",
        "personal_income_tax_filing",
        "Personal Income Tax Filing Process",
    )


def compose_personal_income_tax_payment() -> Dict:
    return _answer(
        """
Pay Personal Income Tax through the approved payment channel of the State Internal Revenue Service that receives the tax in the case.

Before payment:
- Confirm the correct state authority, taxpayer details, and period involved.
- Make sure the amount being paid matches the return, assessment, or lawful computation.
- Generate or confirm any payment reference required by the official channel.

Payment steps:
1. Use the approved state portal, bank channel, or payment platform accepted by that authority.
2. Pay the correct amount due for the relevant period or assessment.
3. Keep the receipt, acknowledgement, or payment confirmation.

After payment:
- Match the payment evidence to the related filing, assessment, or tax record for the same period.

What to do next:
1. Ask how to file Personal Income Tax if the filing has not yet been completed.
2. Ask what records should support the Personal Income Tax payment.
3. Ask whether the case should be handled through PAYE.
""",
        "personal_income_tax_payment",
        "Personal Income Tax Payment Process",
    )


def compose_personal_income_tax_records() -> Dict:
    return _answer(
        """
Keep the income, computation, filing, and payment records that support the Personal Income Tax position for the period involved.

Records you should normally keep:
- income records, pay statements, or other source records supporting the income reported
- computation schedules or working papers supporting the tax position
- filed return, acknowledgement, or portal confirmation where a filing was made
- payment receipt, assessment notice, or other official evidence supporting the payment where applicable
- any state-authority correspondence or supporting record tied to that same tax period

Practical rule:
- Keep records in a form that lets you trace the income, the tax computation, the filing made, and any payment or assessment tied to the same period.
- If part of the issue is handled through PAYE, keep the payroll-side records together with the broader tax record where relevant.

What to do next:
1. Ask how to file Personal Income Tax for the period involved.
2. Ask how to pay Personal Income Tax once the amount due is confirmed.
3. Ask which authority should handle the Personal Income Tax in your case.
""",
        "personal_income_tax_records",
        "Personal Income Tax Records",
    )


# -------------------------------------------------
# PAYE
# -------------------------------------------------


def compose_paye_basic() -> Dict:
    return _answer(
        """
PAYE in Nigeria means Pay As You Earn.

What it is:
- PAYE is the system under which personal income tax is deducted from employment income by the employer.
- The deducted tax is then filed and remitted to the relevant State Internal Revenue Service for the employee's state tax treatment.

Practical point:
- PAYE is mainly an employer payroll compliance issue, not a separate tax that the employee usually files by hand each payroll cycle.

What to do next:
1. Ask who must deduct PAYE in your situation.
2. Ask how to file or remit PAYE after deduction.
3. Ask what payroll records should be kept for PAYE compliance.
""",
        "paye_basic",
        "PAYE Definition",
    )


def compose_paye_obligation() -> Dict:
    return _answer(
        """
PAYE usually applies where an employer pays taxable employment income and is expected to deduct tax from the employee's pay under the applicable rules.

Who this usually affects:
- employers paying salaries, wages, or other taxable employment income
- employees whose pay falls within the personal income tax system handled through payroll deduction

Practical rule:
- First confirm that the worker is being treated under the employment income rules and not under a different engagement structure.
- Then confirm which state tax authority should receive the PAYE filings and remittances.

What to do next:
1. Ask who should deduct PAYE for the worker or payroll in your case.
2. Ask how to file and remit PAYE after deduction.
3. Ask what state authority should receive the PAYE return.
""",
        "paye_obligation",
        "PAYE Obligation",
    )


def compose_paye_records() -> Dict:
    return _answer(
        """
Keep the core payroll and deduction records that support PAYE computation, filing, and remittance for each payroll period.

Records you should normally keep:
- payroll register or payroll schedule for the period
- employee pay details showing gross pay, deductions, and net pay
- PAYE computation support for each employee where applicable
- PAYE return or schedule submitted to the relevant State Internal Revenue Service
- payment receipt, remittance acknowledgement, or portal confirmation

Practical rule:
- Keep records in a form that lets you trace the PAYE deducted, the return filed, and the amount remitted for the same payroll period.
- Where employee details or payroll treatment change, keep the updated records that explain the change.

What to do next:
1. Ask how to file or remit PAYE after deduction.
2. Ask who should deduct PAYE in your case.
3. Ask what to do if payroll records do not match the PAYE return.
""",
        "paye_records",
        "PAYE Records",
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
""",
        "paye_remittance_process",
        "PAYE Remittance Process",
    )


# -------------------------------------------------
# TCC
# -------------------------------------------------


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
""",
        "tcc_verification",
        "TCC Verification Process",
    )


# -------------------------------------------------
# WHT
# -------------------------------------------------


def compose_withholding_tax_basic() -> Dict:
    return _answer(
        """
Withholding Tax in Nigeria is a tax deduction taken at source from certain qualifying payments and then remitted to the relevant tax authority on behalf of the recipient.

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
""",
        "withholding_tax_basic",
        "Withholding Tax Definition",
    )


def compose_withholding_tax_rate() -> Dict:
    return _answer(
        """
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
""",
        "withholding_tax_rate",
        "Withholding Tax Rate",
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
""",
        "withholding_tax_remittance",
        "Withholding Tax Remittance Process",
    )


def compose_withholding_tax_records() -> Dict:
    return _answer(
        """
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
""",
        "withholding_tax_records",
        "Withholding Tax Records",
    )


# -------------------------------------------------
# CIT
# -------------------------------------------------


def compose_company_income_tax_basic() -> Dict:
    return _answer(
        """
Company Income Tax in Nigeria is the tax charged on the taxable profits of companies under the current company-income-tax rules.

What it is:
- CIT is a company profit tax, not a payroll tax and not the same thing as VAT or Withholding Tax.
- The charge is tied to the taxable profit position of the company for the relevant accounting period.
- The exact treatment depends on the company category, the profit position, and the current rule in force.

Practical rule:
- First confirm that the taxpayer is a company and that the question is about company profits before applying any CIT rule.

What to do next:
1. Ask who is expected to pay Company Income Tax in your case.
2. Ask what Company Income Tax rate applies under the current rule.
3. Ask how to file or pay Company Income Tax for the relevant period.
""",
        "company_income_tax_basic",
        "Company Income Tax Definition",
    )


def compose_company_income_tax_obligation() -> Dict:
    return _answer(
        """
Companies that fall within the applicable Company Income Tax charge are the ones expected to pay CIT on their taxable profits for the relevant period.

Who this usually affects:
- companies carrying on business and earning profits that fall within the current company-income-tax rules
- companies that must file the required CIT return and settle any CIT due through the approved federal channel

Practical rule:
- First confirm that the taxpayer is being treated as a company under the applicable tax rules.
- Then confirm whether the company falls within the current CIT charge, what rate rule applies, and what filing obligations follow.

What to do next:
1. Ask what Company Income Tax rate applies in your case.
2. Ask how to file Company Income Tax for the relevant accounting period.
3. Ask what records should support Company Income Tax computation and filing.
""",
        "company_income_tax_obligation",
        "Company Income Tax Obligation",
    )


def compose_company_income_tax_rate() -> Dict:
    return _answer(
        """
The Company Income Tax rate in Nigeria depends on the category the company falls into under the current rule in force. It should be confirmed against the current CIT framework before filing or payment.

Important note:
- Do not assume there is one flat result for every company without first checking the company category under the current CIT rules.
- The correct rate treatment should be tied to the company's applicable classification for the relevant period.

Practical rule:
- Confirm the current category rule that applies to the company first, then use the applicable CIT rate for that category when computing the liability.

What to do next:
1. Ask which Company Income Tax category applies to the company in your case.
2. Ask how to file Company Income Tax after confirming the rate rule.
3. Ask what records should support the Company Income Tax computation.
""",
        "company_income_tax_rate",
        "Company Income Tax Rate",
    )


def compose_company_income_tax_filing() -> Dict:
    return _answer(
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

What to do next:
1. Ask how to pay Company Income Tax once the liability is confirmed.
2. Ask what records should support the Company Income Tax computation.
3. Ask what rate rule applies to the company category in your case.
""",
        "company_income_tax_filing",
        "Company Income Tax Filing Process",
    )


def compose_company_income_tax_payment() -> Dict:
    return _answer(
        """
Pay Company Income Tax through the approved federal payment channel for the relevant assessment or self-computed liability.

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
""",
        "company_income_tax_payment",
        "Company Income Tax Payment Process",
    )


def compose_company_income_tax_records() -> Dict:
    return _answer(
        """
Keep the accounting, profit-computation, tax-adjustment, filing, and payment records that support the Company Income Tax position for each relevant accounting period.

Records you should normally keep:
- financial statements, trial balance, ledgers, and supporting accounting schedules for the period
- income, expense, and adjustment records used to compute taxable profit
- tax computation working papers and schedules supporting the CIT liability
- filed Company Income Tax return, acknowledgement, or portal confirmation
- payment receipt, assessment notice, or other official evidence supporting the CIT settlement where applicable

Practical rule:
- Keep records in a form that lets you trace the accounting profit, the tax adjustments made, the CIT return filed, and any payment or assessment tied to the same period.

What to do next:
1. Ask how to file Company Income Tax for the period involved.
2. Ask how to pay Company Income Tax once the liability is confirmed.
3. Ask what Company Income Tax rate rule applies to the company category in your case.
""",
        "company_income_tax_records",
        "Company Income Tax Records",
    )


# -------------------------------------------------
# General process
# -------------------------------------------------


def compose_tax_payment_process() -> Dict:
    return _answer(
        """
To pay tax in Nigeria, use this general flow:

1. Identify the exact tax type involved.
2. Confirm the correct tax authority.
3. Make sure your registration details are in place, especially your TIN where required.
4. Confirm the payment basis and the amount due.
5. Generate or confirm the payment reference where the authority requires one.
6. Pay through the approved portal, bank channel, or payment platform accepted by the authority.
7. Keep the payment receipt and filing evidence for your records.

The exact payment process differs by tax type and authority, so confirm the applicable portal or payment channel before making payment.
""",
        "tax_payment_process",
        "General Nigerian Tax Payment Process",
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

If the tax type is specific, such as VAT, PAYE, WHT, Personal Income Tax, or Company Income Tax, the filing process should be tailored to that tax rather than treated as a generic filing question.
""",
        "tax_filing_process",
        "General Tax Filing Process",
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
    "withholding_tax_deduction": compose_withholding_tax_deduction,
    "withholding_tax_remittance": compose_withholding_tax_remittance,
    "company_income_tax_filing": compose_company_income_tax_filing,
    "company_income_tax_payment": compose_company_income_tax_payment,
    "personal_income_tax_filing": compose_personal_income_tax_filing,
    "personal_income_tax_payment": compose_personal_income_tax_payment,
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

    q = _normalize(question)
    topic_key = _normalize(topic)
    intent_key = _normalize(intent_type)
    action = _detect_action(question)

    # -----------------------------
    # Authority routing
    # -----------------------------
    if q and _is_authority_question(q):
        if _mentions_any(q, "personal income tax", "pit"):
            return compose_personal_income_tax_authority()
        if _mentions_any(q, "paye", "pay as you earn"):
            return compose_paye_authority()
        if _mentions_any(q, "vat", "value added tax"):
            return compose_vat_authority()
        if _mentions_any(q, "tcc", "tax clearance certificate"):
            return compose_tcc_authority()
        if _mentions_any(q, "tin", "tax identification number", "tax id", "tax identification"):
            return compose_tin_authority()
        if _mentions_any(q, "withholding tax", "wht"):
            return compose_withholding_tax_authority()
        if _mentions_any(q, "company income tax", "cit"):
            return compose_company_income_tax_authority()

    # -----------------------------
    # Direct TIN
    # -----------------------------
    if q in {"what is a tin", "what is tin"} or _mentions_any(
        q,
        "what is a tin",
        "what is tin",
        "meaning of tin",
        "define tin",
        "what does tin mean",
    ):
        return compose_tin_basic()

    if _mentions_any(
        q,
        "who issues a tin",
        "who issues tin",
        "which tax authority handles tin registration",
        "which authority handles tin registration",
        "who handles tin registration",
        "who manages tin",
    ):
        return compose_tin_authority()

    if _mentions_any(
        q,
        "what documents are needed for tin registration",
        "what documents are required for tin registration",
        "documents needed for tin registration",
        "documents for tin registration",
        "what registration details may be required",
    ):
        return compose_tin_documents()

    # -----------------------------
    # Direct VAT
    # -----------------------------
    if _mentions_any(
        q,
        "what is vat",
        "meaning of vat",
        "define vat",
        "what does vat mean",
        "what is value added tax",
    ):
        return compose_vat_basic()

    if _mentions_any(
        q,
        "who must comply with vat",
        "who should comply with vat",
        "who should register for vat",
        "who needs to register for vat",
        "does my business need vat",
        "who pays vat",
    ):
        return compose_vat_obligation()

    if _mentions_any(
        q,
        "what records should i keep for vat",
        "vat records",
        "records for vat",
        "keep records for vat",
    ):
        return compose_vat_records()

    if _mentions_any(
        q,
        "what is zero rated vat",
        "what is zero rated value added tax",
        "zero rated vat",
        "zero rated supply",
    ):
        return compose_vat_zero_rated()

    if _mentions_any(
        q,
        "what is vat exemption",
        "what is exempt vat",
        "vat exempt",
        "vat exemption",
        "exempt supply",
    ):
        return compose_vat_exemption()

    # -----------------------------
    # Direct PIT
    # -----------------------------
    if _mentions_any(
        q,
        "what is personal income tax",
        "meaning of personal income tax",
        "define personal income tax",
        "what is pit",
        "what does pit mean",
    ):
        return compose_personal_income_tax_basic()

    if _mentions_any(
        q,
        "who pays personal income tax",
        "who should pay personal income tax",
        "who must pay personal income tax",
        "who pays pit",
    ):
        return compose_personal_income_tax_obligation()

    if _mentions_any(
        q,
        "what is the personal income tax rate",
        "personal income tax rate",
        "pit rate",
    ):
        return compose_personal_income_tax_rate()

    if _mentions_any(
        q,
        "how do i file personal income tax",
        "how to file personal income tax",
        "file personal income tax",
        "file pit",
    ):
        return compose_personal_income_tax_filing()

    if _mentions_any(
        q,
        "how do i pay personal income tax",
        "how to pay personal income tax",
        "pay personal income tax",
        "pay pit",
    ):
        return compose_personal_income_tax_payment()

    if _mentions_any(
        q,
        "what records should i keep for personal income tax",
        "personal income tax records",
        "pit records",
    ):
        return compose_personal_income_tax_records()

    # -----------------------------
    # Direct PAYE
    # -----------------------------
    if _mentions_any(q, "what is paye", "meaning of paye", "define paye", "what does paye mean"):
        return compose_paye_basic()

    if _mentions_any(q, "who must deduct paye", "who should deduct paye", "who deducts paye"):
        return compose_paye_obligation()

    if _mentions_any(
        q,
        "what records should i keep for paye",
        "what payroll records should i keep",
        "keep records for paye",
        "paye records",
        "payroll records",
    ):
        return compose_paye_records()

    # -----------------------------
    # Direct WHT
    # -----------------------------
    if _mentions_any(
        q,
        "what is withholding tax",
        "meaning of withholding tax",
        "define withholding tax",
        "what does withholding tax mean",
        "what is wht",
    ):
        return compose_withholding_tax_basic()

    if _mentions_any(
        q,
        "who must deduct withholding tax",
        "who should deduct withholding tax",
        "who deducts withholding tax",
        "who must deduct wht",
    ):
        return compose_withholding_tax_authority()

    if _mentions_any(
        q,
        "what is the withholding tax rate",
        "withholding tax rate",
        "what is the wht rate",
        "wht rate",
    ):
        return compose_withholding_tax_rate()

    if _mentions_any(
        q,
        "how do i deduct withholding tax",
        "how to deduct withholding tax",
        "deduct withholding tax",
        "deduct wht",
    ):
        return compose_withholding_tax_deduction()

    if _mentions_any(
        q,
        "how do i remit withholding tax",
        "how to remit withholding tax",
        "remit withholding tax",
        "remit wht",
        "pay withholding tax",
    ):
        return compose_withholding_tax_remittance()

    if _mentions_any(
        q,
        "what records should i keep for withholding tax",
        "what records should i keep for wht",
        "wht records",
        "withholding tax records",
    ):
        return compose_withholding_tax_records()

    # -----------------------------
    # Direct CIT
    # -----------------------------
    if _mentions_any(
        q,
        "what is company income tax",
        "meaning of company income tax",
        "define company income tax",
        "what is cit",
    ):
        return compose_company_income_tax_basic()

    if _mentions_any(
        q,
        "who pays company income tax",
        "who should pay company income tax",
        "who pays cit",
    ):
        return compose_company_income_tax_obligation()

    if _mentions_any(
        q,
        "what is the company income tax rate",
        "company income tax rate",
        "cit rate",
    ):
        return compose_company_income_tax_rate()

    if _mentions_any(
        q,
        "how do i file company income tax",
        "how to file company income tax",
        "file company income tax",
        "file cit",
    ):
        return compose_company_income_tax_filing()

    if _mentions_any(
        q,
        "how do i pay company income tax",
        "how to pay company income tax",
        "pay company income tax",
        "pay cit",
    ):
        return compose_company_income_tax_payment()

    if _mentions_any(
        q,
        "what records should i keep for company income tax",
        "company income tax records",
        "cit records",
    ):
        return compose_company_income_tax_records()

    # -----------------------------
    # Direct TCC
    # -----------------------------
    if _mentions_any(q, "who issues a tcc", "who issues tcc"):
        return compose_tcc_authority()

    # -----------------------------
    # Topic + action routing
    # -----------------------------
    if _topic_in(topic_key, *TOPIC_ALIASES["tcc"]) or _mentions_any(q, *TOPIC_ALIASES["tcc"]):
        if action == "verify":
            return compose_tcc_verification()
        if action == "apply":
            return compose_tcc_application()

    if _topic_in(topic_key, *TOPIC_ALIASES["tin"]) or _mentions_any(q, *TOPIC_ALIASES["tin"]):
        if action == "verify":
            return compose_tin_verification()
        if action in {"apply", "register"}:
            return compose_tin_registration()
        if action == "records":
            return compose_tin_documents()

    if _topic_in(topic_key, *TOPIC_ALIASES["vat"]) or _mentions_any(q, *TOPIC_ALIASES["vat"]):
        if action == "register":
            return compose_vat_registration_process()
        if action == "file":
            return compose_vat_filing_process()
        if action == "pay":
            return compose_vat_payment_process()
        if action == "records":
            return compose_vat_records()

    if _topic_in(topic_key, *TOPIC_ALIASES["pit"]) or _mentions_any(q, *TOPIC_ALIASES["pit"]):
        if action == "file":
            return compose_personal_income_tax_filing()
        if action == "pay":
            return compose_personal_income_tax_payment()
        if action == "records":
            return compose_personal_income_tax_records()
        if action == "rate":
            return compose_personal_income_tax_rate()

    if _topic_in(topic_key, *TOPIC_ALIASES["paye"]) or _mentions_any(q, *TOPIC_ALIASES["paye"]):
        if action == "pay":
            return compose_paye_remittance_process()
        if action == "records":
            return compose_paye_records()

    if _topic_in(topic_key, *TOPIC_ALIASES["wht"]) or _mentions_any(q, *TOPIC_ALIASES["wht"]):
        if action == "deduct":
            return compose_withholding_tax_deduction()
        if action == "pay":
            return compose_withholding_tax_remittance()
        if action == "records":
            return compose_withholding_tax_records()
        if action == "rate":
            return compose_withholding_tax_rate()

    if _topic_in(topic_key, *TOPIC_ALIASES["cit"]) or _mentions_any(q, *TOPIC_ALIASES["cit"]):
        if action == "file":
            return compose_company_income_tax_filing()
        if action == "pay":
            return compose_company_income_tax_payment()
        if action == "records":
            return compose_company_income_tax_records()
        if action == "rate":
            return compose_company_income_tax_rate()

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
    "compose_personal_income_tax_authority",
    "compose_paye_authority",
    "compose_vat_authority",
    "compose_tcc_authority",
    "compose_tin_authority",
    "compose_withholding_tax_authority",
    "compose_company_income_tax_authority",
    "compose_tin_basic",
    "compose_tin_documents",
    "compose_tin_registration",
    "compose_tin_verification",
    "compose_vat_basic",
    "compose_vat_obligation",
    "compose_vat_registration_process",
    "compose_vat_filing_process",
    "compose_vat_payment_process",
    "compose_vat_records",
    "compose_vat_zero_rated",
    "compose_vat_exemption",
    "compose_personal_income_tax_basic",
    "compose_personal_income_tax_obligation",
    "compose_personal_income_tax_rate",
    "compose_personal_income_tax_filing",
    "compose_personal_income_tax_payment",
    "compose_personal_income_tax_records",
    "compose_paye_basic",
    "compose_paye_obligation",
    "compose_paye_records",
    "compose_paye_remittance_process",
    "compose_tcc_application",
    "compose_tcc_verification",
    "compose_withholding_tax_basic",
    "compose_withholding_tax_rate",
    "compose_withholding_tax_deduction",
    "compose_withholding_tax_remittance",
    "compose_withholding_tax_records",
    "compose_company_income_tax_basic",
    "compose_company_income_tax_obligation",
    "compose_company_income_tax_rate",
    "compose_company_income_tax_filing",
    "compose_company_income_tax_payment",
    "compose_company_income_tax_records",
    "compose_tax_payment_process",
    "compose_tax_filing_process",
    "PROCESS_MAP",
    "try_compose",
]
