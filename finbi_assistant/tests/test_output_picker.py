# Tests for chart selection: deterministic rules, and reconciling the model's nomination with reality.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_output_picker.py

Covers the cases that actually broke during development: Snowflake DATE columns
arriving as ``object`` dtype, and the model naming axis columns that do not exist
in its own result set.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from app.output_picker import (  # noqa: E402
    categorical_columns,
    numeric_columns,
    pick_output_type,
    resolve_output,
    temporal_columns,
)

CASES: list[tuple[str, pd.DataFrame, str]] = [
    (
        "single number -> kpi",
        pd.DataFrame({"TOTAL": [600384.07]}),
        "kpi",
    ),
    (
        "category + measure -> bar",
        pd.DataFrame({"PROVIDER": ["RAZORPAY", "PINELABS", "PAYTM"], "NET": [1.0, 2.0, 3.0]}),
        "bar",
    ),
    (
        "datetime + measure -> line",
        pd.DataFrame({"MONTH": pd.to_datetime(["2026-01-01", "2026-02-01"]), "NET": [1.0, 2.0]}),
        "line",
    ),
    (
        "date objects (Snowflake DATE) + measure -> line",
        pd.DataFrame({"D": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)], "BAL": [10.0, 20.0]}),
        "line",
    ),
    (
        "two measures, many rows -> scatter",
        pd.DataFrame({"A": list(range(40)), "B": list(range(40))}),
        "scatter",
    ),
    (
        "many categories -> table",
        pd.DataFrame({"ID": [f"T{i}" for i in range(40)], "V": list(range(40))}),
        "table",
    ),
    (
        "three dimensions -> table",
        pd.DataFrame({"A": ["x"], "B": ["y"], "C": ["z"], "N": [1.0]}),
        "table",
    ),
    (
        "empty -> table",
        pd.DataFrame({"A": pd.Series(dtype="float64")}),
        "table",
    ),
]


def test_pick_output_type() -> list[str]:
    """Check the deterministic shape rules."""
    failures = []
    for name, df, expected in CASES:
        actual = pick_output_type(df)
        if actual != expected:
            failures.append(f"pick_output_type: {name}: expected {expected}, got {actual}")
    return failures


def test_column_typing() -> list[str]:
    """Snowflake DATE columns arrive as objects and must still count as temporal."""
    failures = []
    df = pd.DataFrame(
        {
            "D": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
            "TS": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "CAT": ["a", "b"],
            "NUM": [1.5, 2.5],
        }
    )
    if set(temporal_columns(df)) != {"D", "TS"}:
        failures.append(f"temporal_columns: got {temporal_columns(df)}")
    if numeric_columns(df) != ["NUM"]:
        failures.append(f"numeric_columns: got {numeric_columns(df)}")
    if categorical_columns(df) != ["CAT"]:
        failures.append(f"categorical_columns: got {categorical_columns(df)}")
    return failures


def test_resolve_output() -> list[str]:
    """The model's nomination is honoured only when the result can support it."""
    failures = []
    bar_df = pd.DataFrame({"PROVIDER": ["A", "B", "C"], "NET_REVENUE": [3.0, 2.0, 1.0]})

    # Lowercase alias from the model must match the uppercased Snowflake column.
    got = resolve_output(bar_df, {"chart_type": "bar", "x": "provider", "y": "net_revenue"})
    if got["chart_type"] != "bar" or got["x"] != "PROVIDER" or got["y"] != "NET_REVENUE":
        failures.append(f"case-insensitive axis match failed: {got}")
    if got["source"] != "model":
        failures.append(f"expected model-sourced choice, got {got['source']}")

    # Model names a column that does not exist -> fall back to rules.
    got = resolve_output(bar_df, {"chart_type": "bar", "x": "nope", "y": "net_revenue"})
    if got["source"] != "rules" or got["chart_type"] != "bar":
        failures.append(f"unknown axis should fall back to rules: {got}")
    if got["x"] != "PROVIDER":
        failures.append(f"fallback should pick a real axis, got {got}")

    # Model says kpi but there are many rows -> must not render a KPI.
    got = resolve_output(bar_df, {"chart_type": "kpi", "x": None, "y": None})
    if got["chart_type"] == "kpi":
        failures.append("multi-row result must not be accepted as a kpi")

    # Pie with too many slices downgrades to bar.
    many = pd.DataFrame({"C": [f"c{i}" for i in range(12)], "V": list(range(12))})
    got = resolve_output(many, {"chart_type": "pie", "x": "c", "y": "v"})
    if got["chart_type"] != "bar":
        failures.append(f"12-slice pie should downgrade to bar, got {got['chart_type']}")

    # Bar with too many rows downgrades to table.
    huge = pd.DataFrame({"C": [f"c{i}" for i in range(60)], "V": list(range(60))})
    got = resolve_output(huge, {"chart_type": "bar", "x": "c", "y": "v"})
    if got["chart_type"] != "table":
        failures.append(f"60-row bar should downgrade to table, got {got['chart_type']}")

    # Empty result is always a table.
    got = resolve_output(pd.DataFrame({"A": pd.Series(dtype="float64")}),
                         {"chart_type": "bar", "x": "a", "y": "a"})
    if got["chart_type"] != "table":
        failures.append(f"empty result must be a table, got {got['chart_type']}")

    return failures


def main() -> int:
    """Run all checks and report."""
    failures = test_pick_output_type() + test_column_typing() + test_resolve_output()
    if failures:
        print(f"FAILED ({len(failures)} problems)\n")
        for line in failures:
            print("  " + line)
        return 1
    print(f"PASSED  {len(CASES)} shape cases + column typing + 6 reconciliation cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
