# Live test: one-prompt dashboard generation, then conversational edits, executing every widget.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_dashboard_live.py

Generates a real dashboard spec from Cortex, executes every widget's SQL against
FINBI_DEMO with default filter values, then applies the two conversational edits
from the demo script and re-executes. Fails if any widget fails to run or returns
no rows.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402

harness.install()

from app.dashboard_engine import (  # noqa: E402
    apply_substitutions,
    build_substitutions,
    edit_dashboard_spec,
    generate_dashboard_spec,
)
from app.metadata_rag import retrieve_relevant_tables  # noqa: E402
from app.snowflake_utils import run_query, summarize_error  # noqa: E402
from app.sql_guard import check_sql  # noqa: E402

BUILD_INSTRUCTION = "Build me a dashboard on settlement reliability"
EDIT_INSTRUCTIONS = [
    "Now split that by provider and add a KPI for pending payouts",
    "Make it monthly instead",
]


def default_filter_values(spec: dict) -> dict:
    """Pick sensible defaults for each filter, as the UI would on first render."""
    values: dict = {}
    for item in spec.get("filters", []):
        table, column = item["column"].split(".")
        if item["type"] == "date_range":
            frame = run_query(f"SELECT MIN({column}) AS lo, MAX({column}) AS hi FROM {table}")
            low, high = frame.iloc[0]["LO"], frame.iloc[0]["HI"]
            values[item["id"]] = (low or dt.date(2026, 1, 1), high or dt.date.today())
        else:
            frame = run_query(
                f"SELECT DISTINCT {column} AS v FROM {table} "
                f"WHERE {column} IS NOT NULL ORDER BY 1 LIMIT 100"
            )
            options = frame["V"].tolist() if "V" in frame.columns else []
            values[item["id"]] = options if item["type"] == "multiselect" else (
                options[0] if options else None
            )
    return values


def exercise(spec: dict, label: str) -> list[str]:
    """Execute every widget in ``spec``. Returns a list of problems."""
    problems: list[str] = []
    values = default_filter_values(spec)
    subs = build_substitutions(spec.get("filters", []), values)

    print(f"\n  {label}")
    print(f"    title   : {spec.get('title')}")
    print(f"    layout  : {spec.get('layout')}")
    print(f"    filters : {[f['id'] + ':' + f['type'] for f in spec.get('filters', [])] or 'none'}")

    for widget in spec.get("widgets", []):
        sql = apply_substitutions(widget["sql"], subs)
        ok, reason = check_sql(sql)
        if not ok:
            problems.append(f"[{label}] {widget['id']} refused by guard: {reason}")
            print(f"    {widget['id']:<4} {widget['type']:<7} GUARD REFUSED")
            continue
        try:
            frame = run_query(sql)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"[{label}] {widget['id']} failed: {summarize_error(exc)}")
            print(f"    {widget['id']:<4} {widget['type']:<7} SQL ERROR: {summarize_error(exc)[:90]}")
            continue

        note = ""
        if frame.empty:
            problems.append(f"[{label}] {widget['id']} returned no rows")
            note = "  <-- EMPTY"
        elif widget["type"] == "kpi" and len(frame) != 1:
            problems.append(f"[{label}] {widget['id']} is a kpi but returned {len(frame)} rows")
            note = f"  <-- kpi with {len(frame)} rows"
        print(
            f"    {widget['id']:<4} {widget['type']:<7} rows={len(frame):<4} "
            f"cols={list(frame.columns)[:4]}{note}"
        )
    return problems


def main() -> int:
    """Build a dashboard, then edit it twice, executing everything each time."""
    tables = retrieve_relevant_tables(BUILD_INSTRUCTION, k=3)
    print(f"retrieved: {[t['name'] for t in tables]}")

    try:
        spec = generate_dashboard_spec(BUILD_INSTRUCTION, tables)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to generate spec: {exc}")
        return 1

    problems = exercise(spec, f'BUILD: "{BUILD_INSTRUCTION}"')

    for instruction in EDIT_INSTRUCTIONS:
        try:
            spec = edit_dashboard_spec(spec, instruction, tables)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"edit failed ({instruction}): {exc}")
            print(f"\n  EDIT FAILED: {instruction}\n    {exc}")
            continue
        problems += exercise(spec, f'EDIT: "{instruction}"')

    print("\n--- final spec ---")
    print(json.dumps(spec, indent=2)[:1400])

    if problems:
        print(f"\nFAILED ({len(problems)} problems)")
        for line in problems:
            print("  " + line)
        return 1
    print("\nPASSED  dashboard built, edited twice, every widget executed and returned rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
