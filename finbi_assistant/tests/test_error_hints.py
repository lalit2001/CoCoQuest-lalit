# Tests for the schema-aware repair hints that break the retry loop out of repeated guesses.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_error_hints.py

No Snowflake or Streamlit needed - :mod:`app.error_hints` is dependency-free.

The case these tests lock in: asked "what is the fees and charges" the model wrote
``SUM(fee) FROM FEE_SCHEDULE``. That column is on ``TRANSACTIONS``. Two blind
retries produced the identical error. The hint has to name the column, say where
it really lives, list the real columns of each offered table, and warn that
measures spread across tables cannot go in one flat SELECT.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.error_hints import (  # noqa: E402
    ambiguous_column_hint,
    build_hints,
    format_attempt_history,
    invalid_identifier_hint,
)
from app.schema_text import read_metadata  # noqa: E402

META = read_metadata()
ALL_TABLES = META["tables"]


def tables(*names: str) -> list[dict]:
    """Return metadata dicts for the named tables, in the order given."""
    by_name = {t["name"]: t for t in ALL_TABLES}
    return [by_name[n] for n in names]


def test_invalid_identifier() -> list[str]:
    """The real regression: FEE used against FEE_SCHEDULE."""
    failures = []
    retrieved = tables("FEE_SCHEDULE", "CARD_MACHINE_ACCOUNTS", "TRANSACTIONS")
    hint = invalid_identifier_hint("FEE", retrieved, ALL_TABLES)

    for needle, why in [
        ('"FEE"', "names the offending column"),
        ("TRANSACTIONS", "says which table actually has it"),
        ("transaction_fee", "lists FEE_SCHEDULE's real columns"),
        ("UNION ALL", "warns that cross-table measures need combining"),
    ]:
        if needle not in hint:
            failures.append(f"invalid_identifier_hint does not {why}: {needle!r} absent")

    # A qualified identifier must be handled the same way.
    if "TRANSACTIONS" not in invalid_identifier_hint("F.FEE", retrieved, ALL_TABLES):
        failures.append("qualified identifier F.FEE was not resolved")

    # A column that exists nowhere must be called out as invented.
    hint = invalid_identifier_hint("PROFIT_MARGIN", retrieved, ALL_TABLES)
    if "does not exist on ANY table" not in hint:
        failures.append(f"invented column not flagged: {hint[:120]}")

    # A column that exists only on a table that was NOT retrieved.
    retrieved = tables("FEE_SCHEDULE")
    hint = invalid_identifier_hint("DISPUTED_AMOUNT", retrieved, ALL_TABLES)
    if "DISPUTES" not in hint or "not in the list above" not in hint:
        failures.append(f"non-retrieved owner not explained: {hint[:160]}")

    return failures


def test_build_hints_from_real_errors() -> list[str]:
    """build_hints must parse the exact strings Snowflake produces."""
    failures = []
    retrieved = tables("FEE_SCHEDULE", "CARD_MACHINE_ACCOUNTS", "TRANSACTIONS")

    cases = [
        (
            "000904 SQL compilation error: error line 1 at position 46 "
            "invalid identifier 'FEE'",
            "TRANSACTIONS",
        ),
        (
            "000904 SQL compilation error: error line 1 at position 70 "
            "invalid identifier 'MONTHLY_FEE'",
            "monthly_fee",
        ),
        (
            "002028 SQL compilation error: ambiguous column name 'PROVIDER'",
            "more than one table",
        ),
    ]
    for error, needle in cases:
        hint = build_hints(error, retrieved, ALL_TABLES)
        if needle not in hint:
            failures.append(f"build_hints({error[:50]}...) missing {needle!r}")

    # An error we cannot diagnose must yield an empty hint, not a wrong one, so
    # the prompt's generic guidance applies instead.
    vague = "000603 execution error: something went wrong"
    if build_hints(vague, retrieved, ALL_TABLES) != "":
        failures.append("an undiagnosable error should produce no hint")

    return failures


def test_attempt_history() -> list[str]:
    """History must be numbered and include both the SQL and the error."""
    failures = []
    if format_attempt_history([]) != "(none)":
        failures.append("empty history should render as (none)")

    rendered = format_attempt_history(
        [
            {"sql": "SELECT SUM(fee) FROM FEE_SCHEDULE", "error": "invalid identifier 'FEE'"},
            {"sql": "SELECT SUM(fee) FROM TRANSACTIONS", "error": "invalid identifier 'MONTHLY_FEE'"},
        ]
    )
    for needle in ("Attempt 1", "Attempt 2", "FEE_SCHEDULE", "MONTHLY_FEE"):
        if needle not in rendered:
            failures.append(f"attempt history missing {needle!r}")
    return failures


def test_ambiguous() -> list[str]:
    """Ambiguous-column hints must name the tables involved."""
    failures = []
    hint = ambiguous_column_hint("PROVIDER", tables("SETTLEMENTS", "DISPUTES"))
    if "SETTLEMENTS" not in hint or "DISPUTES" not in hint:
        failures.append(f"ambiguous hint should list both tables: {hint}")
    if "alias" not in hint.lower():
        failures.append("ambiguous hint should tell the model to alias and qualify")
    return failures


def main() -> int:
    """Run all checks and report."""
    failures = (
        test_invalid_identifier()
        + test_build_hints_from_real_errors()
        + test_attempt_history()
        + test_ambiguous()
    )
    if failures:
        print(f"FAILED ({len(failures)} problems)\n")
        for line in failures:
            print("  " + line)
        return 1
    print(
        "PASSED  invalid-identifier diagnosis, real Snowflake error parsing, "
        "attempt history, ambiguous columns"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
