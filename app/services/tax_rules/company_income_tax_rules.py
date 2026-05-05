from __future__ import annotations

import re
from typing import Optional, Dict


def _normalize(text: Optional[str]) -> str:
    raw = str(text or "").strip().lower()
    raw = raw.replace("_", " ")
    raw = re.sub(r"[^a-z0-9\s]+", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _has_any(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _is_cit_topic(question: str) -> bool:
    q = _normalize(question)
    return _has_any(
        q,
        r"\bcompany income tax\b",
        r"\bcompanies income tax\b",
        r"\bcit\b",
        r"\bcita\b",
    )


def _is_definition_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\bwhat is\b", r"\bmeaning\b", r"\bdefine\b", r"\bstand for\b")


def _is_payer_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(
        q,
        r"\bwho pays\b",
        r"\bwho must pay\b",
        r"\bwho should pay\b",
        r"\bwho is liable\b",
        r"\bwho is responsible\b",
        r"\bdoes my company pay\b",
        r"\bwhich companies pay\b",
    )


def _is_rate_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\brate\b", r"\bpercentage\b", r"\bhow much\b")


def _is_records_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\brecords?\b", r"\bdocumentation\b", r"\bwhat should i keep\b", r"\bkeep .*record\b", r"\bevidence\b")


def compose_company_income_tax_definition() -> Dict:
    answer = """
Company Income Tax (CIT) in Nigeria is the tax charged on the taxable profits of companies under the applicable company-income-tax rules.

What it is:
- CIT is a company-level profit tax, not a payroll tax and not the same thing as VAT or withholding tax.
- The charge is tied to the taxable profit position of the company for the relevant accounting period.
- The exact treatment depends on the type of company, the profit position, the applicable law, and the current rate rule in force.

Practical rule:
- First confirm that the taxpayer is a company and that the question is about company profits before applying any CIT rule.

What to do next:
1. Ask who is expected to pay Company Income Tax in your case.
2. Ask what Company Income Tax rate applies under the current rule.
3. Ask how to file or pay Company Income Tax for the relevant period.

Source: current official Federal Inland Revenue Service guidance and the current company-income-tax framework in force.
""".strip()
    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_definition",
            "answer_mode": "rule",
            "source_type": "rule_composer",
            "source_label": "Company Income Tax Basics",
            "grounded": True,
        },
    }


def compose_company_income_tax_payer_rule() -> Dict:
    answer = """
Companies that fall within the applicable Company Income Tax charge are the ones expected to pay CIT on their taxable profits for the relevant period.

Who this usually affects:
- companies carrying on business and earning profits that fall within the current company-income-tax rules
- companies that must file the required CIT return and settle any CIT due through the approved FIRS channel

Practical rule:
- First confirm that the taxpayer is being treated as a company under the applicable tax rules.
- Then confirm whether the company falls within the current CIT charge, what rate rule applies, and what filing obligations follow.

What to do next:
1. Ask what Company Income Tax rate applies in your case.
2. Ask how to file Company Income Tax for the relevant accounting period.
3. Ask what records should support Company Income Tax computation and filing.

Source: current official Federal Inland Revenue Service company-income-tax guidance and filing rules.
""".strip()
    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_payer_rule",
            "answer_mode": "rule",
            "source_type": "rule_composer",
            "source_label": "Who Pays Company Income Tax",
            "grounded": True,
        },
    }


def compose_company_income_tax_rate_rule() -> Dict:
    answer = """
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

Source: current official Federal Inland Revenue Service company-income-tax rate guidance and the current CIT framework in force.
""".strip()
    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_rate_rule",
            "answer_mode": "rule",
            "source_type": "rule_composer",
            "source_label": "Company Income Tax Rate Basics",
            "grounded": True,
        },
    }


def compose_company_income_tax_records_rule() -> Dict:
    answer = """
Keep the accounting, profit-computation, tax-adjustment, filing, and payment records that support the Company's Income Tax position for each relevant accounting period.

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

Source: current official Federal Inland Revenue Service company-income-tax filing, computation, and payment-support guidance.
""".strip()
    return {
        "ok": True,
        "answer": answer,
        "meta": {
            "intent_type": "company_income_tax_records_rule",
            "answer_mode": "rule",
            "source_type": "rule_composer",
            "source_label": "Company Income Tax Records",
            "grounded": True,
        },
    }


def try_answer(question: Optional[str] = None, *_, **__) -> Optional[Dict]:
    q = _normalize(question)
    if not q or not _is_cit_topic(q):
        return None
    if _is_records_question(q):
        return compose_company_income_tax_records_rule()
    if _is_payer_question(q):
        return compose_company_income_tax_payer_rule()
    if _is_rate_question(q):
        return compose_company_income_tax_rate_rule()
    if _is_definition_question(q):
        return compose_company_income_tax_definition()
    return None
