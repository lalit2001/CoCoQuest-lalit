# Hostile tests for the SQL safety guard - the single most important reliability check in the app.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_sql_guard.py

No Streamlit or Snowflake session required - :mod:`app.sql_guard` is standalone.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sql_guard import check_sql  # noqa: E402

# Statements that must be allowed through.
SHOULD_PASS = [
    "SELECT 1",
    "SELECT COUNT(*) AS n FROM TRANSACTIONS",
    "select provider, sum(net_amount) as net from transactions group by provider",
    "WITH t AS (SELECT * FROM DISPUTES) SELECT COUNT(*) AS n FROM t",
    "SELECT * FROM SETTLEMENTS WHERE status = 'PENDING' LIMIT 500",
    "SELECT * FROM TRANSACTIONS LIMIT 500;",  # single trailing semicolon is fine
    # Forbidden words appearing only inside string literals must not trip it.
    "SELECT * FROM TRANSACTIONS WHERE notes = 'customer asked us to delete the charge'",
    "SELECT * FROM DISPUTES WHERE description = 'they said DROP it'",
    "SELECT 'it''s a drop in revenue' AS note",
    # Column and alias names that merely contain a keyword substring.
    "SELECT created_at, updated_count FROM TRANSACTIONS LIMIT 10",
    "SELECT SUM(fee) AS total_fees FROM TRANSACTIONS",
    "SELECT DATE_TRUNC('month', value_date) AS m, SUM(net_amount) AS net FROM TRANSACTIONS GROUP BY m",
]

# Statements that must be refused.
SHOULD_FAIL = [
    "",
    "   ",
    "DELETE FROM TRANSACTIONS",
    "delete from transactions",
    "DROP TABLE TRANSACTIONS",
    "TRUNCATE TABLE LEDGER",
    "UPDATE TRANSACTIONS SET fee = 0",
    "INSERT INTO LEDGER VALUES (1)",
    "MERGE INTO LEDGER USING TRANSACTIONS ON 1=1",
    "GRANT ALL ON DATABASE FINBI_DEMO TO ROLE PUBLIC",
    "CREATE TABLE evil AS SELECT 1",
    "ALTER TABLE TRANSACTIONS DROP COLUMN fee",
    "CALL SYSTEM$WAIT(10)",
    "USE ROLE ACCOUNTADMIN",
    "COPY INTO @stage FROM TRANSACTIONS",
    # Stacked statements.
    "SELECT 1; DROP TABLE TRANSACTIONS",
    "SELECT 1; SELECT 2",
    "SELECT * FROM TRANSACTIONS LIMIT 1; DELETE FROM DISPUTES;",
    # Comment-based injection.
    "SELECT 1 -- ; DROP TABLE TRANSACTIONS",
    "SELECT 1 /* DROP TABLE TRANSACTIONS */",
    # Not a query at all.
    "EXPLAIN SELECT 1",
    "SHOW TABLES",
    "DESCRIBE TABLE TRANSACTIONS",
    # Unterminated literal hiding a stacked statement.
    "SELECT * FROM TRANSACTIONS WHERE notes = 'abc; DROP TABLE LEDGER",
]


def main() -> int:
    """Run every case and report failures. Returns a process exit code."""
    failures: list[str] = []

    for sql in SHOULD_PASS:
        ok, reason = check_sql(sql)
        if not ok:
            failures.append(f"FALSE POSITIVE (should pass): {sql!r}\n    -> {reason}")

    for sql in SHOULD_FAIL:
        ok, _ = check_sql(sql)
        if ok:
            failures.append(f"FALSE NEGATIVE (should fail): {sql!r}")

    total = len(SHOULD_PASS) + len(SHOULD_FAIL)
    if failures:
        print(f"FAILED {len(failures)}/{total}\n")
        for line in failures:
            print("  " + line)
        return 1

    print(f"PASSED {total}/{total}")
    print(f"  {len(SHOULD_PASS)} legitimate queries allowed")
    print(f"  {len(SHOULD_FAIL)} hostile or malformed statements refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
