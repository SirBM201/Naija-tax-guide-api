from __future__ import annotations

import re
from typing import Dict, Optional


def _normalize(text: Optional[str]) -> str:
    raw = str(text or "").strip().lower()
    raw = raw.replace("_", " ")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _mentions_any(text: str, *terms: str) -> bool:
    value = _normalize(text)
    return any(_normalize(term) in value for term in terms if term)


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
        ]
    )


def _answer(answer: str, intent_type: str, source_label: str) -> Dict:
    return {
        "ok": True,
        "answer": answer.strip(),
        "meta": {
            "intent_type": intent_type,
            "answer_mode": "rule",
            "source_type": "tax_authority_rules",
            "source_label": source_label,
            "grounded": True,
        },
    }


def answer_personal_income_tax_authority() -> Dict:
    return _answer(
        """
Personal Income Tax for individuals is usually handled by the relevant State Internal Revenue Service for the state that has the taxing right in the case.

What this usually means:
- PAYE and other personal-income-tax matters are generally handled at state level.
- You should first confirm the state that is entitled to receive the tax before filing or paying.

Practical rule:
- Do not route a personal-income-tax question to the federal company-tax channel just because the person works for a company.
- First confirm that the question is about an individual's income tax position and then use the relevant state tax authority channel.

What to do next:
1. Ask which state tax authority should receive the personal income tax in your case.
2. Ask whether the question is about PAYE or another personal-income-tax issue.
3. Ask which portal or filing channel that state authority uses.

Source: current official Nigerian tax-administration structure for personal income tax at state level and the relevant State Internal Revenue Service channel for the taxpayer's case.
""",
        "personal_income_tax_authority",
        "Personal Income Tax Authority Routing",
    )


def answer_paye_authority() -> Dict:
    return _answer(
        """
PAYE is usually handled by the relevant State Internal Revenue Service, not by the federal company-income-tax channel.

What this usually means:
- PAYE is part of personal income tax administered through payroll deduction.
- The employer should use the state tax authority that has the right to receive the employee-related PAYE remittance.

Practical rule:
- If the question is whether FIRS or a State Internal Revenue Service handles PAYE, start from the state personal-income-tax side.
- Then confirm the exact state authority that should receive the PAYE filing and remittance in the case.

What to do next:
1. Ask which state authority should receive the PAYE return in your case.
2. Ask who must deduct PAYE on the payroll involved.
3. Ask which state portal or remittance channel should be used.

Source: current official state-level PAYE administration structure and the relevant State Internal Revenue Service filing and remittance channel.
""",
        "paye_authority",
        "PAYE Authority Routing",
    )


def answer_vat_authority() -> Dict:
    return _answer(
        """
VAT is handled through the federal tax authority channel, currently the Nigeria Revenue Service / former FIRS platform and its approved VAT service channels.

What this usually means:
- VAT questions about registration, filing, payment, and federal VAT administration should be routed through the federal VAT channel.
- Where an official federal portal is required, use the approved NRS/FIRS VAT or self-service channel for the taxpayer's profile and transaction.

Practical rule:
- Do not route a VAT question to a state personal-income-tax portal just because the business also deals with state taxes.
- First confirm that the question is actually about VAT, then use the federal VAT administration channel.

What to do next:
1. Ask which federal portal should be used for VAT in your case.
2. Ask how to register, file, or pay VAT after confirming the channel.
3. Ask whether the exact supply is taxable, exempt, or zero-rated before charging VAT.

Source: current official Nigeria Revenue Service / former FIRS VAT administration structure and approved federal VAT portal channels.
""",
        "vat_authority",
        "VAT Authority Routing",
    )


def answer_tcc_authority() -> Dict:
    return _answer(
        """
The Tax Clearance Certificate should be issued by the tax authority that administers the taxpayer's relevant tax record for the case.

What this usually means:
- For many personal income tax cases, the relevant State Internal Revenue Service is the issuing authority.
- For relevant federal cases, the approved Nigeria Revenue Service / former FIRS TCC channel is used.

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


def answer_withholding_tax_authority() -> Dict:
    return _answer(
        """
The tax authority that should receive Withholding Tax depends on the payment category and the authority that administers that WHT obligation in the case.

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


def answer_company_income_tax_authority() -> Dict:
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


def try_answer(question: Optional[str] = None, **_: object):
    q = _normalize(question)
    if not q:
        return None

    if _is_authority_question(q):
        if _mentions_any(q, "personal income tax", "pit"):
            return answer_personal_income_tax_authority()
        if _mentions_any(q, "paye", "pay as you earn"):
            return answer_paye_authority()
        if _mentions_any(q, "vat", "value added tax"):
            return answer_vat_authority()
        if _mentions_any(q, "tcc", "tax clearance certificate"):
            return answer_tcc_authority()
        if _mentions_any(q, "withholding tax", "wht"):
            return answer_withholding_tax_authority()
        if _mentions_any(q, "company income tax", "cit"):
            return answer_company_income_tax_authority()

    if "does firs or state internal revenue handle paye" in q or "does firs or state internal revenue service handle paye" in q:
        return answer_paye_authority()
    if "does nrs or state internal revenue handle paye" in q or "does nrs or state internal revenue service handle paye" in q:
        return answer_paye_authority()
    if "who issues a tcc" in q or "who issues tcc" in q:
        return answer_tcc_authority()
    if "which tax authority handles personal income tax" in q:
        return answer_personal_income_tax_authority()
    if "which tax authority handles vat" in q:
        return answer_vat_authority()
    if "which tax authority receives withholding tax" in q or "which tax authority receives wht" in q:
        return answer_withholding_tax_authority()
    if "which tax authority handles company income tax" in q or "which tax authority handles cit" in q:
        return answer_company_income_tax_authority()

    return None
