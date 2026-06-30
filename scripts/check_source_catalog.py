from app.services.tax_source_catalog import (
    get_source_category,
    high_risk_source_codes,
    source_review_summary,
)


def main():
    primary = get_source_category("primary_law")
    if primary is None:
        raise AssertionError("primary_law category is missing")
    if primary.risk_level != "high":
        raise AssertionError("primary_law should be high risk")

    missing = get_source_category("unknown")
    if missing is not None:
        raise AssertionError("unknown category should not resolve")

    high_risk = set(high_risk_source_codes())
    required = {"primary_law", "federal_authority_guidance", "state_authority_practice"}
    if not required.issubset(high_risk):
        raise AssertionError(f"missing high-risk source categories: {required - high_risk}")

    summary = source_review_summary()
    if len(summary) < 4:
        raise AssertionError("source review summary is unexpectedly small")

    print("Source catalog checks passed")


if __name__ == "__main__":
    main()
