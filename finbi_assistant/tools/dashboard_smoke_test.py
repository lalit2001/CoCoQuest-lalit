# Offline harness that generates, validates, executes and then conversationally edits a real dashboard spec.
# Co-authored with CoCo
"""
End-to-end smoke test for the dashboard builder.

Exercises the full loop against the real warehouse, without Streamlit:

1. Generate a spec from one instruction.
2. Validate it.
3. Substitute default filter values and execute every widget's SQL.
4. Apply a conversational edit and re-run all of it.

Usage::

    python tools/dashboard_smoke_test.py
    python tools/dashboard_smoke_test.py "Build a dashboard on card fees"

Exit code is non-zero if any widget fails to build or run.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.dashboard_engine import (  # noqa: E402
    apply_substitutions,
    build_substitutions,
    edit_dashboard_spec,
    generate_dashboard_spec,
    validate_spec,
)
from app.metadata_rag import retrieve_relevant_tables  # noqa: E402
from app.snowflake_utils import run_query  # noqa: E402
from app.sql_guard import check_sql  # noqa: E402

DEFAULT_INSTRUCTION = "Build me a dashboard on settlement reliability"
DEFAULT_EDIT = "Now split it by provider and add a KPI for pending payouts"

#: Filter defaults used headlessly, standing in for the Streamlit controls.
FALLBACK_DATES = (dt.date(2026, 1, 1), dt.date(2026, 8, 15))


def _headless_filter_values(spec: dict[str, Any]) -> dict[str, Any]:
    """Pick a plausible value for each filter, as the UI controls would."""
    values: dict[str, Any] = {}
    for spec_filter in spec.get("filters", []):
        table, column = spec_filter["column"].split(".")
        if spec_filter["type"] == "date_range":
            try:
                bounds = run_query(
                    f"SELECT MIN({column}) AS LO, MAX({column}) AS HI FROM {table}"
                )
                low, high = bounds.iloc[0]["LO"], bounds.iloc[0]["HI"]
                values[spec_filter["id"]] = (low or FALLBACK_DATES[0], high or FALLBACK_DATES[1])
            except Exception:  # noqa: BLE001
                values[spec_filter["id"]] = FALLBACK_DATES
            continue

        try:
            options = run_query(
                f"SELECT DISTINCT {column} AS V FROM {table} "
                f"WHERE {column} IS NOT NULL ORDER BY 1 LIMIT 100"
            )["V"].tolist()
        except Exception:  # noqa: BLE001
            options = []

        if spec_filter["type"] == "multiselect":
            values[spec_filter["id"]] = options
        else:
            values[spec_filter["id"]] = options[0] if options else None
    return values


def exercise(spec: dict[str, Any], label: str) -> bool:
    """Execute every widget in ``spec``. Returns True if all of them worked."""
    print(f"\n  {label}: {spec['title']!r}  layout={spec['layout']}")
    filters = spec.get("filters", [])
    if filters:
        print(
            "  filters: "
            + ", ".join(f"{f['id']} ({f['type']} on {f['column']})" for f in filters)
        )
    else:
        print("  filters: none")

    values = _headless_filter_values(spec)
    substitutions = build_substitutions(filters, values)

    all_ok = True
    for widget in spec["widgets"]:
        sql = apply_substitutions(widget["sql"], substitutions)

        if "{" in sql or "}" in sql:
            print(f"    [FAIL] {widget['id']} ({widget['type']}): unsubstituted placeholder")
            all_ok = False
            continue

        ok, reason = check_sql(sql)
        if not ok:
            print(f"    [FAIL] {widget['id']} ({widget['type']}): guard refused - {reason}")
            all_ok = False
            continue

        try:
            frame = run_query(sql)
        except Exception as exc:  # noqa: BLE001
            first = str(exc).strip().splitlines()
            print(
                f"    [FAIL] {widget['id']} ({widget['type']}): "
                + " ".join(line.strip() for line in first[:2])
            )
            print(f"           sql: {' '.join(sql.split())[:180]}")
            all_ok = False
            continue

        shape = f"{len(frame)}x{len(frame.columns)}"
        cols = ",".join(map(str, frame.columns))
        print(
            f"    [ok]   {widget['id']:<6} {widget['type']:<8} "
            f"{widget['title'][:34]:<34} {shape:>7}  ({cols})"
        )
    return all_ok


def main() -> int:
    instruction = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTRUCTION
    edit = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_EDIT
    failures = 0

    print(f"[1/2] BUILD: {instruction}")
    started = time.time()
    try:
        tables = retrieve_relevant_tables(instruction, k=3)
        print(f"  retrieved [{', '.join(t['name'] for t in tables)}]")
        spec = generate_dashboard_spec(instruction, tables)
        _, errors = validate_spec(spec)
        if errors:
            print(f"  [FAIL] spec did not validate: {errors}")
            return 1
        if not exercise(spec, "built"):
            failures += 1
        print(f"  ({time.time() - started:.1f}s)")
    except Exception:  # noqa: BLE001
        print("  [FAIL] generation raised")
        print("  " + traceback.format_exc().replace("\n", "\n  "))
        return 1

    print(f"\n[2/2] EDIT: {edit}")
    started = time.time()
    try:
        edited = edit_dashboard_spec(spec, edit, tables)
        _, errors = validate_spec(edited)
        if errors:
            print(f"  [FAIL] edited spec did not validate: {errors}")
            failures += 1
        else:
            if not exercise(edited, "edited"):
                failures += 1
            kept = {w["id"] for w in spec["widgets"]} & {w["id"] for w in edited["widgets"]}
            print(
                f"  widgets: {len(spec['widgets'])} -> {len(edited['widgets'])} "
                f"({len(kept)} id(s) preserved)"
            )
        print(f"  ({time.time() - started:.1f}s)")
    except Exception:  # noqa: BLE001
        print("  [FAIL] edit raised")
        print("  " + traceback.format_exc().replace("\n", "\n  "))
        failures += 1

    print(f"\n{'=' * 70}")
    if failures:
        print(f"FAILED ({failures} stage(s) had problems)")
        print("\nFinal spec:")
        print(json.dumps(spec, indent=2)[:2000])
        return 1
    print("PASSED: spec generated, validated, executed, edited and re-executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
