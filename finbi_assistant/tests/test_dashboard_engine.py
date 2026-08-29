# Tests for dashboard spec validation and typed, escaped filter substitution.
# Co-authored with CoCo
"""
Dashboard engine tests.

Run with::

    python tests/test_dashboard_engine.py

Covers the two things that must not break:

* :func:`validate_spec` accepts a good spec and reports errors for malformed ones,
  rather than letting them reach the renderer.
* :func:`sql_literal` / :func:`sql_list` / :func:`apply_substitutions` produce
  correctly typed and escaped SQL, and the substituted statement still passes the
  read-only guard - including when a filter value contains a quote or a smuggled
  statement.

Needs ``pandas`` only; no warehouse, no Streamlit runtime.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dashboard_engine import (  # noqa: E402
    apply_substitutions,
    build_substitutions,
    sql_list,
    sql_literal,
    validate_spec,
)
from app.sql_guard import check_sql  # noqa: E402

GOOD_SPEC = {
    "title": "Settlement reliability",
    "layout": "grid-2col",
    "filters": [
        {
            "id": "date_range",
            "label": "Date range",
            "type": "date_range",
            "column": "SETTLEMENTS.settlement_date",
        },
        {
            "id": "provider",
            "label": "Provider",
            "type": "select",
            "column": "SETTLEMENTS.provider",
        },
    ],
    "widgets": [
        {
            "id": "w1",
            "type": "kpi",
            "title": "Pending payouts",
            "sql": (
                "SELECT COUNT(*) AS pending_payouts FROM SETTLEMENTS "
                "WHERE status = 'PENDING' AND settlement_date BETWEEN "
                "{date_range.start} AND {date_range.end}"
            ),
            "x": None,
            "y": "pending_payouts",
        },
        {
            "id": "w2",
            "type": "bar",
            "title": "Settled by provider",
            "sql": (
                "SELECT provider, SUM(net_settled_amount) AS settled FROM SETTLEMENTS "
                "WHERE provider = {provider} GROUP BY provider ORDER BY settled DESC"
            ),
            "x": "provider",
            "y": "settled",
        },
    ],
}

# (label, spec) - each must produce at least one validation error.
BAD_SPECS = [
    ("not an object", []),
    ("no widgets", {"title": "x", "widgets": []}),
    (
        "widget with no sql",
        {"widgets": [{"id": "w1", "type": "kpi", "title": "t", "sql": ""}]},
    ),
    (
        "widget with a write statement",
        {"widgets": [{"id": "w1", "type": "kpi", "sql": "DROP TABLE LEDGER"}]},
    ),
    (
        "unsupported widget type",
        {"widgets": [{"id": "w1", "type": "sunburst", "sql": "SELECT 1 AS a"}]},
    ),
    (
        "filter with no column",
        {
            "filters": [{"id": "p", "label": "P", "type": "select"}],
            "widgets": [{"id": "w1", "type": "kpi", "sql": "SELECT 1 AS a"}],
        },
    ),
    (
        "filter naming a column that does not exist",
        {
            "filters": [
                {"id": "p", "type": "select", "column": "DISPUTES.does_not_exist"}
            ],
            "widgets": [{"id": "w1", "type": "kpi", "sql": "SELECT 1 AS a"}],
        },
    ),
    (
        "filter naming a table that does not exist",
        {
            "filters": [{"id": "p", "type": "select", "column": "GHOSTS.provider"}],
            "widgets": [{"id": "w1", "type": "kpi", "sql": "SELECT 1 AS a"}],
        },
    ),
    (
        "unsupported filter type",
        {
            "filters": [
                {"id": "p", "type": "slider", "column": "DISPUTES.provider"}
            ],
            "widgets": [{"id": "w1", "type": "kpi", "sql": "SELECT 1 AS a"}],
        },
    ),
    (
        "placeholder referencing an undeclared filter",
        {
            "filters": [],
            "widgets": [
                {
                    "id": "w1",
                    "type": "kpi",
                    "sql": (
                        "SELECT COUNT(*) AS n FROM DISPUTES "
                        "WHERE date_raised > {ghost.start}"
                    ),
                }
            ],
        },
    ),
]


def check_validation() -> list[str]:
    """Validate the good spec and confirm each bad spec reports an error."""
    failures: list[str] = []

    spec, errors = validate_spec(GOOD_SPEC)
    if errors:
        return [f"the good spec was rejected: {errors}"]
    if len(spec["widgets"]) != 2:
        failures.append(f"expected 2 widgets, kept {len(spec['widgets'])}")
    if len(spec["filters"]) != 2:
        failures.append(
            f"expected both filters kept (both are referenced), got {len(spec['filters'])}"
        )
    if spec["layout"] != "grid-2col":
        failures.append(f"layout mangled to {spec['layout']!r}")

    # An unreferenced filter should be dropped rather than left to confuse the UI.
    trimmed, trim_errors = validate_spec(
        {
            "filters": [
                {"id": "unused", "type": "select", "column": "DISPUTES.provider"}
            ],
            "widgets": [
                {"id": "w1", "type": "kpi", "sql": "SELECT COUNT(*) AS n FROM DISPUTES"}
            ],
        }
    )
    if trim_errors:
        failures.append(f"spec with an unused filter should still validate: {trim_errors}")
    elif trimmed["filters"]:
        failures.append("an unreferenced filter should have been dropped")

    # An unknown layout should fall back rather than error.
    fallback, _ = validate_spec(
        {
            "layout": "carousel",
            "widgets": [
                {"id": "w1", "type": "kpi", "sql": "SELECT COUNT(*) AS n FROM DISPUTES"}
            ],
        }
    )
    if fallback["layout"] != "grid-2col":
        failures.append(f"unknown layout should fall back, got {fallback['layout']!r}")

    for label, candidate in BAD_SPECS:
        _, candidate_errors = validate_spec(candidate)
        if not candidate_errors:
            failures.append(f"invalid spec accepted: {label}")

    return failures


def check_literals() -> list[str]:
    """Confirm values become correctly typed and escaped SQL literals."""
    failures: list[str] = []

    if sql_literal(dt.date(2026, 4, 1)) != "DATE '2026-04-01'":
        failures.append("a date must become a typed DATE literal")
    if sql_literal(None) != "NULL":
        failures.append("None must become NULL")
    if sql_literal("RAZORPAY") != "'RAZORPAY'":
        failures.append("a string must be single-quoted")
    if sql_literal("O'Brien Bank") != "'O''Brien Bank'":
        failures.append("an embedded quote must be doubled")
    if sql_list([]) != "(NULL)":
        failures.append("an empty IN list must match nothing, not everything")
    if sql_list(["A", "B"]) != "('A', 'B')":
        failures.append("a list must be parenthesised and comma separated")

    return failures


def check_substitution() -> list[str]:
    """Confirm substituted widget SQL is complete and still passes the guard."""
    failures: list[str] = []

    spec, _ = validate_spec(GOOD_SPEC)
    values = {
        "date_range": (dt.date(2026, 4, 1), dt.date(2026, 5, 31)),
        "provider": "RAZORPAY",
    }
    substitutions = build_substitutions(spec["filters"], values)

    for widget in spec["widgets"]:
        sql = apply_substitutions(widget["sql"], substitutions)
        if "{" in sql or "}" in sql:
            failures.append(f"{widget['id']}: placeholders left behind: {sql}")
        ok, reason = check_sql(sql)
        if not ok:
            failures.append(f"{widget['id']}: substituted SQL failed the guard: {reason}")
        if "DATE '2026-04-01'" in widget["sql"]:
            failures.append(f"{widget['id']}: template should not contain a literal date")

    dated = apply_substitutions(
        "SELECT 1 AS a FROM SETTLEMENTS WHERE settlement_date BETWEEN "
        "{date_range.start} AND {date_range.end}",
        substitutions,
    )
    if "DATE '2026-04-01'" not in dated or "DATE '2026-05-31'" not in dated:
        failures.append(f"dates not substituted as typed literals: {dated}")

    # A hostile filter value must stay inside its literal, so the guard still sees
    # exactly one read-only statement.
    hostile = apply_substitutions(
        "SELECT 1 AS a FROM SETTLEMENTS WHERE provider = {provider}",
        build_substitutions(
            [{"id": "provider", "type": "select", "column": "SETTLEMENTS.provider"}],
            {"provider": "x'; DROP TABLE LEDGER; --"},
        ),
    )
    ok, _ = check_sql(hostile)
    if not ok:
        failures.append(f"a hostile filter value escaped its literal: {hostile}")

    # A multiselect expands pre-parenthesised for direct use with IN.
    listed = apply_substitutions(
        "SELECT 1 AS a FROM TRANSACTIONS WHERE provider IN {providers}",
        build_substitutions(
            [{"id": "providers", "type": "multiselect", "column": "TRANSACTIONS.provider"}],
            {"providers": ["RAZORPAY", "PAYTM"]},
        ),
    )
    if "IN ('RAZORPAY', 'PAYTM')" not in listed:
        failures.append(f"multiselect did not expand correctly: {listed}")
    ok, _ = check_sql(listed)
    if not ok:
        failures.append(f"multiselect SQL failed the guard: {listed}")

    # An empty multiselect must match nothing rather than everything.
    empty = apply_substitutions(
        "SELECT 1 AS a FROM TRANSACTIONS WHERE provider IN {providers}",
        build_substitutions(
            [{"id": "providers", "type": "multiselect", "column": "TRANSACTIONS.provider"}],
            {"providers": []},
        ),
    )
    if "IN (NULL)" not in empty:
        failures.append(f"empty multiselect should match nothing: {empty}")

    return failures


def main() -> int:
    failures = check_validation() + check_literals() + check_substitution()
    if failures:
        print(f"FAILED ({len(failures)} problem(s))\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print("PASSED")
    print(f"  validation: good spec accepted, {len(BAD_SPECS)} malformed specs refused,")
    print("              unused filter dropped, unknown layout defaulted")
    print("  literals:   typed dates, NULL, quote escaping, empty IN list")
    print("  guard:      injection contained, multiselect expansion, no leftovers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
