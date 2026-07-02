import os
from datetime import datetime, timezone
from pprint import pprint

from app.core.supabase_client import get_supabase_client
from app.services.answer_metadata_service import build_source_metadata


def clean(value):
    return str(value or "").strip()


def rows(resp):
    data = getattr(resp, "data", None) or []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def table_rows(sb, table, limit):
    return rows(
        sb.table(table)
        .select("*")
        .limit(limit)
        .execute()
    )


def answer_question(row):
    return clean(row.get("question") or row.get("normalized_question") or row.get("canonical_key") or row.get("topic"))


def build_patch(row, table):
    source_kind = clean(row.get("source") or ("library" if table == "qa_library" else "database"))
    question = answer_question(row)
    metadata = build_source_metadata(
        row=row,
        source_kind=source_kind,
        table=table,
        question=question,
        mode=clean(row.get("mode")),
        ai_model=clean(row.get("model")),
    )

    patch = {
        "source_metadata": metadata,
        "source_category": metadata.get("source_category"),
        "source_label": metadata.get("source_label"),
        "source_type": metadata.get("source_type"),
        "jurisdiction": metadata.get("jurisdiction") or "Nigeria",
        "tax_year": metadata.get("tax_year"),
        "risk_level": metadata.get("risk_level") or "medium",
    }

    # Do not invent a reviewed date. Use existing dates only.
    if metadata.get("last_reviewed_at"):
        patch["last_reviewed_at"] = metadata.get("last_reviewed_at")

    return {k: v for k, v in patch.items() if v not in (None, "")}


def needs_patch(row):
    if not clean(row.get("source_category")):
        return True
    if not clean(row.get("risk_level")):
        return True
    if not isinstance(row.get("source_metadata"), dict) or not row.get("source_metadata"):
        return True
    return False


def row_id(row):
    return clean(row.get("id"))


def main():
    dry_run = clean(os.getenv("DRY_RUN", "1")).lower() not in {"0", "false", "no"}
    limit = int(clean(os.getenv("LIMIT", "500")) or "500")
    tables = [item.strip() for item in clean(os.getenv("TABLES", "qa_library,qa_cache")).split(",") if item.strip()]
    allowed = {"qa_library", "qa_cache"}

    sb = get_supabase_client(admin=True)
    report = {
        "ok": True,
        "dry_run": dry_run,
        "limit": limit,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tables": [],
    }

    for table in tables:
        if table not in allowed:
            report["tables"].append({"table": table, "skipped": True, "reason": "not_allowed"})
            continue

        checked = 0
        patched = 0
        skipped_no_id = 0
        samples = []
        errors = []

        for row in table_rows(sb, table, limit):
            checked += 1
            if not needs_patch(row):
                continue

            patch = build_patch(row, table)
            rid = row_id(row)
            if not rid:
                skipped_no_id += 1
                continue

            samples.append({"id": rid, "question": answer_question(row)[:120], "patch": patch})

            if dry_run:
                patched += 1
                continue

            try:
                sb.table(table).update(patch).eq("id", rid).execute()
                patched += 1
            except Exception as exc:
                errors.append({"id": rid, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})

        report["tables"].append(
            {
                "table": table,
                "checked": checked,
                "would_patch" if dry_run else "patched": patched,
                "skipped_no_id": skipped_no_id,
                "sample_count": len(samples[:10]),
                "samples": samples[:10],
                "errors": errors[:10],
            }
        )

    pprint(report)
    if any(table.get("errors") for table in report["tables"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
