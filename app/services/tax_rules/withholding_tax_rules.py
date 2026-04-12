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


def _is_withholding_topic(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\bwithholding tax\b", r"\bwht\b", r"\bwithholding\b")


def _is_definition_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\bwhat is\b", r"\bmeaning\b", r"\bdefine\b", r"\bstand for\b")


def _is_deductor_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\bwho must deduct\b", r"\bwho deducts\b", r"\bwho should deduct\b", r"\bwho withholds\b", r"\bwho is responsible\b", r"\bshould i deduct\b", r"\bwhen do i deduct\b")


def _is_rate_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\brate\b", r"\bpercentage\b", r"\bhow much\b")


def _is_records_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(q, r"\brecords?\b", r"\bdocumentation\b", r"\bwhat should i keep\b", r"\bkeep .*record\b", r"\bevidence\b")


def _is_remittance_question(question: str) -> bool:
    q = _normalize(question)
    return _has_any(
        q,
        r"\bhow do i remit\b",
        r"\bhow to remit\b",
        r"\bremit\b",
        r"\bremittance\b",
        r"\bhow do i pay\b",
        r"\bhow to pay\b",
        r"\bpay wht\b",
        r"\bfile wht\b",
        r"\bhow do i file\b",
        r"\bhow to file\b",
    )


def compose_withholding_tax_definition() -> Dict:
    answer = """
Withholding Tax (WHT) in Nigeria is a deduction taken at source from certain payments and then remitted to the relevant tax authority on behalf of the recipient.

What it is:
- WHT is not usually a separate final tax in every case.
- It is commonly treated as an advance or credit mechanism within the wider income tax system, depending on the taxpayer and the type of payment.
- The exact treatment depends on the nature of the payment, the recipient, and the applicable rule.

Practical point:
- First classify the exact payment involved before deciding whether WHT applies and at what rate.

What to do next:
1. Ask whether the exact payment in your case is subject to WHT.
2. Ask who should deduct and remit WHT in that situation.
3. Ask what rate applies to the exact payment category.

Source: current official Federal Inland Revenue Service and relevant tax-authority guidance for withholding tax deduction, remittance, and credit treatment.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_definition", "answer_mode": "rule", "source_type": "rule_composer", "source_label": "Withholding Tax Basics", "grounded": True}}


def compose_withholding_tax_deductor_rule() -> Dict:
    answer = """
The payer is usually the party that must deduct Withholding Tax when making a payment that falls within a withholding category under the applicable rule.

Who this usually affects:
- businesses or organizations making qualifying payments for services, contracts, rents, interest, dividends, commissions, or similar categories where WHT applies
- payers that must deduct before paying the net amount to the recipient

Practical rule:
- Do not deduct WHT just because a payment is business-related.
- First confirm that the exact payment category is one that attracts WHT and that the recipient is being treated under the correct tax rule.
- Then apply the correct rate and remit to the correct authority.

What to do next:
1. Ask whether your exact payment type is subject to WHT.
2. Ask what WHT rate applies to that payment type.
3. Ask how to remit WHT after deduction.

Source: current official withholding-tax deduction and remittance guidance for qualifying payments.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_deductor_rule", "answer_mode": "rule", "source_type": "rule_composer", "source_label": "Who Deducts Withholding Tax", "grounded": True}}


def compose_withholding_tax_rate_rule() -> Dict:
    answer = """
There is no single universal Withholding Tax rate for every payment in Nigeria. The applicable rate depends on the exact payment category, the recipient, and the rule that governs that transaction.

Important note:
- Do not apply one general rate across all contracts, services, rents, interest, dividends, commissions, or other payments.
- The correct rate must be tied to the exact payment type and the current rule in force.

Practical rule:
- Identify the exact nature of the payment first, then confirm the applicable WHT rate from the current official schedule or authority guidance before deducting.

What to do next:
1. Ask what WHT rate applies to your exact payment type.
2. Ask whether the payment should be deducted at all before checking the rate.
3. Ask how to remit the WHT once the rate is confirmed.

Source: current official withholding-tax schedules and tax-authority guidance for payment-specific deduction rules.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_rate_rule", "answer_mode": "rule", "source_type": "rule_composer", "source_label": "Withholding Tax Rate Basics", "grounded": True}}


def compose_withholding_tax_remittance_rule() -> Dict:
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

Source: current official withholding-tax remittance, deduction, and tax-credit support guidance.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_remittance_rule", "answer_mode": "rule", "source_type": "rule_composer", "source_label": "How to Remit Withholding Tax", "grounded": True}}


def compose_withholding_tax_records_rule() -> Dict:
    answer = """
Keep the core payment, deduction, remittance, and credit-support records that show how the Withholding Tax was computed, deducted, and remitted.

Records you should normally keep:
- contract, invoice, payment instruction, or other source document for the transaction
- gross payment amount, WHT amount deducted, and net amount paid
- deduction schedule or computation support showing how the WHT was calculated
- remittance receipt, acknowledgement, or portal confirmation
- credit note, receipt, or evidence issued to the recipient where applicable

Practical rule:
- Keep records in a form that lets you trace the original payment, the WHT deducted, the authority remittance, and the recipient credit support for the same transaction.

What to do next:
1. Ask whether the exact payment in your case should attract WHT first.
2. Ask how to remit WHT after deduction.
3. Ask what evidence the recipient should receive for tax-credit purposes.

Source: current official withholding-tax deduction, remittance, and credit-support guidance.
""".strip()
    return {"ok": True, "answer": answer, "meta": {"intent_type": "withholding_tax_records_rule", "answer_mode": "rule", "source_type": "rule_composer", "source_label": "Withholding Tax Records", "grounded": True}}


def try_answer(question: Optional[str] = None, *_, **__) -> Optional[Dict]:
    q = _normalize(question)
    if not q or not _is_withholding_topic(q):
        return None
    if _is_records_question(q):
        return compose_withholding_tax_records_rule()
    if _is_remittance_question(q):
        return compose_withholding_tax_remittance_rule()
    if _is_deductor_question(q):
        return compose_withholding_tax_deductor_rule()
    if _is_rate_question(q):
        return compose_withholding_tax_rate_rule()
    if _is_definition_question(q):
        return compose_withholding_tax_definition()
    return None
