from app.services.ai_service import GUIDANCE_NOTE, classify_tax_safety_risk, ensure_guidance_note


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

    print("AI safety policy checks passed")


if __name__ == "__main__":
    main()
