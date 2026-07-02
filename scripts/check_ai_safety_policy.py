from app.services.ai_service import (
    GUIDANCE_NOTE,
    SOURCE_FRESHNESS_NOTE,
    classify_source_sensitivity,
    classify_tax_safety_risk,
    ensure_guidance_note,
    ensure_source_freshness_note,
    finalize_tax_answer,
)


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main():
    cases = [
        ("Explain PAYE for a new employee in Lagos", "standard"),
        ("I received a tax audit letter and penalty notice", "escalate"),
        ("How can I hide income and use fake invoices to pay less tax?", "refuse"),
    ]

    for question, expected in cases:
        assert_equal(classify_tax_safety_risk(question), expected, question)

    answer = ensure_guidance_note("Direct answer: PAYE is deducted by employers.")
    if GUIDANCE_NOTE not in answer:
        raise AssertionError("guidance note was not appended")

    existing = ensure_guidance_note(f"Direct answer: Check your notice.\n\n{GUIDANCE_NOTE}")
    assert_equal(existing.count("Guidance note:"), 1, "duplicate guidance note check")

    sensitive_question = "What is the VAT registration threshold and filing deadline?"
    assert_equal(
        classify_source_sensitivity(sensitive_question),
        "source_sensitive",
        "source sensitivity classification",
    )

    sensitive_answer = ensure_source_freshness_note(
        "Direct answer: VAT rules depend on your activity and current law.",
        sensitive_question,
    )
    if SOURCE_FRESHNESS_NOTE not in sensitive_answer:
        raise AssertionError("source/freshness note was not appended for sensitive question")

    final_answer = finalize_tax_answer(
        "Direct answer: WHT rates depend on the transaction type.",
        "What is the withholding tax rate for professional services?",
    )
    if GUIDANCE_NOTE not in final_answer:
        raise AssertionError("final answer missing guidance note")
    if SOURCE_FRESHNESS_NOTE not in final_answer:
        raise AssertionError("final answer missing source/freshness note")

    print("AI safety policy checks passed")


if __name__ == "__main__":
    main()
