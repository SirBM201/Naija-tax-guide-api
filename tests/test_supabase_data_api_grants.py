from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
SEED_SQL = ROOT / "supabase" / "seed_tax_quiz_questions.sql"
CREATE_TABLE = re.compile(
    r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?public\.([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
REVOKE_ALL = re.compile(
    r"\brevoke\s+all(?:\s+privileges)?\s+on\s+table\s+public\.([a-z_][a-z0-9_]*)"
    r"\s+from\s+public\s*,\s*anon\s*,\s*authenticated\s*,\s*service_role\s*;",
    re.IGNORECASE,
)
GRANT = re.compile(
    r"\bgrant\s+([a-z ,]+)\s+on\s+table\s+public\.([a-z_][a-z0-9_]*)"
    r"\s+to\s+service_role\s*;",
    re.IGNORECASE,
)


class SupabaseDataApiGrantTests(unittest.TestCase):
    def test_every_table_creation_has_explicit_role_revokes(self) -> None:
        sql_files = sorted(MIGRATIONS.glob("*.sql")) + [SEED_SQL]
        self.assertTrue(sql_files, "Expected NTG SQL migrations or seed SQL")
        for path in sql_files:
            sql = path.read_text(encoding="utf-8")
            created_tables = {name.lower() for name in CREATE_TABLE.findall(sql)}
            if not created_tables:
                continue
            revoked_tables = {name.lower() for name in REVOKE_ALL.findall(sql)}
            for table in sorted(created_tables):
                with self.subTest(file=str(path.relative_to(ROOT)), table=table):
                    self.assertIn(
                        table,
                        revoked_tables,
                        "Every CREATE TABLE migration/seed must explicitly revoke "
                        "PUBLIC, anon, authenticated, and service_role before grants.",
                    )

    def test_seed_declares_columns_used_by_current_backend(self) -> None:
        sql = SEED_SQL.read_text(encoding="utf-8").lower()
        required_columns = {
            "tax_quiz_questions": ("law_year text", "metadata jsonb"),
            "tax_quiz_options": ("metadata jsonb",),
            "tax_quiz_attempts": (
                "wa_id text",
                "displayed_option_order jsonb",
                "selected_label text",
                "q5_explanation_used boolean",
                "credits_charged integer",
                "q5_explained_at timestamptz",
            ),
        }
        for table, columns in required_columns.items():
            for column in columns:
                with self.subTest(table=table, column=column):
                    self.assertIn(
                        f"add column if not exists {column}",
                        sql,
                        "Seed SQL must include the deployed fields used by current NTG runtime code.",
                    )

    def test_runtime_quiz_grants_match_backend_operations(self) -> None:
        sql = (
            SEED_SQL.read_text(encoding="utf-8")
            + "\n"
            + (MIGRATIONS / "20260929_ntg_oct30_data_api_grants.sql").read_text(encoding="utf-8")
        )
        grants = {
            table.lower(): {priv.strip().upper() for priv in privileges.split(",")}
            for privileges, table in GRANT.findall(sql)
        }
        self.assertEqual(grants.get("tax_quiz_questions"), {"SELECT"})
        self.assertEqual(grants.get("tax_quiz_options"), {"SELECT"})
        self.assertEqual(grants.get("tax_quiz_attempts"), {"SELECT", "INSERT", "UPDATE"})
        self.assertNotIn(
            "ntg_subscription_fulfillments",
            grants,
            "The subscription ledger is read/written inside a SECURITY DEFINER RPC.",
        )


if __name__ == "__main__":
    unittest.main()
