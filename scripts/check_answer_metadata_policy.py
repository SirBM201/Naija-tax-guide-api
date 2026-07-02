from app.services.answer_metadata_service import (
    append_source_metadata_note,
    build_source_metadata,
    infer_risk_level,
    infer_source_category,
    source_metadata_note,
)
from app.services.answer_metadata_patch import enrich_ask_result, enrich_database_result, enrich_ai_result


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main():
    row = {
        "source_category": "state_authority_practice",
        "last_reviewed_at": "2026-07-01T10:00:00+00:00",
        "jurisdiction": "Lagos State, Nigeria",
        "risk_level": "high",
        "review_status": "approved",
    }

    assert_equal(
        infer_source_category(row, source_kind="library", question="When should PAYE be remitted?"),
        "state_authority_practice",
        "explicit source category",
    )

    assert_equal(
        infer_risk_level({}, question="What is the VAT registration threshold and filing deadline?"),
        "high",
        "source-sensitive risk inference",
    )

    metadata = build_source_metadata(
        row=row,
        source_kind="library",
        table="qa_library",
        question="When should PAYE be remitted?",
        mode="library_exact",
    )
    assert_equal(metadata["source_category"], "state_authority_practice", "metadata category")
    assert_equal(metadata["last_reviewed_at"], "2026-07-01", "metadata review date")

    note = source_metadata_note(metadata)
    if "Source details:" not in note or "Last reviewed: 2026-07-01" not in note:
        raise AssertionError("source metadata note missing expected details")

    answer = append_source_metadata_note("Direct answer: PAYE is deducted by employers.", metadata)
    if "Source details:" not in answer:
        raise AssertionError("answer source details were not appended")

    db_result = enrich_database_result(
        {"ok": True, "found": True, "answer": "Direct answer: PAYE is remitted by employers.", "source": "library", "mode": "library_exact"},
        question="When should PAYE be remitted?",
        row=row,
        table="qa_library",
        source_kind="library",
        mode="library_exact",
    )
    if "source_metadata" not in db_result or "Source details:" not in db_result["answer"]:
        raise AssertionError("database result was not enriched")

    ai_result = enrich_ai_result(
        {"ok": True, "answer": "Direct answer: VAT dates can change.", "source": "ai", "mode": "ai_grounded", "model": "test-model"},
        question="What is the VAT filing deadline?",
    )
    if "source_metadata" not in ai_result or "Source/freshness note:" not in ai_result["answer"]:
        raise AssertionError("AI result was not finalized with source/freshness metadata")

    guarded = enrich_ask_result(
        {"ok": True, "answer": "Direct answer: Check the official document.", "source": "ai", "mode": "ai_grounded", "meta": {}},
        question="What should I do with an official tax document?",
    )
    if not guarded.get("meta", {}).get("source_metadata"):
        raise AssertionError("ask result meta.source_metadata missing")

    print("Answer metadata policy checks passed")


if __name__ == "__main__":
    main()
