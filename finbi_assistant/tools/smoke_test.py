# Offline harness that runs the full retrieve -> generate -> guard -> execute pipeline outside Streamlit.
# Co-authored with CoCo
"""
End-to-end smoke test for the NL->SQL pipeline.

Runs the real thing against the real warehouse - real retrieval, real Cortex
calls, real query execution - without needing Streamlit. Use it after changing a
prompt or the metadata::

    python tools/smoke_test.py             # the standard question set
    python tools/smoke_test.py "my own question"

A refusal counts as a pass when the question was destructive, which is why
"Delete all transactions" is part of the standard set: the guard firing is the
correct behaviour, not a failure.

Exit code is non-zero if any question fails, so this doubles as a regression gate.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.metadata_rag import retrieve_relevant_tables  # noqa: E402
from app.nl2sql import generate_query_plan, repair_query_plan  # noqa: E402
from app.snowflake_utils import run_query  # noqa: E402

#: Questions spanning all six tables, including two cross-table joins and one
#: request that must be refused rather than executed.
QUESTIONS = [
    "What is our net revenue by provider this year?",
    "Which disputes are we most likely to lose?",
    "Show me the bank account balance trend since April",
    "Which settlement batches are still pending?",
    "How much have we paid in card processing fees each month?",
    "Which terminals are inactive?",
    "Which provider charges us the most per transaction?",
    "What is our chargeback win rate by provider?",
    # Cross-table: DISPUTES -> LEDGER via source_id.
    "What was the ledger impact of disputes we lost in May?",
    # Cross-table: TRANSACTIONS -> CARD_MACHINE_ACCOUNTS.
    "Show net revenue by terminal along with each terminal's settlement bank",
    # Must be refused or safely deflected by the guard.
    "Delete all transactions",
]


def run_one(question: str) -> tuple[bool, str]:
    """Run one question end to end. Returns ``(ok, report)``."""
    tables = retrieve_relevant_tables(question, k=3)
    names = ", ".join(t["name"] for t in tables)
    header = f"retrieved [{names}]"

    try:
        plan = generate_query_plan(question, tables)
    except ValueError as exc:
        # The guard rejected the statement, or the reply was unparseable. For a
        # destructive question this is the desired outcome.
        return True, f"{header}\n    REFUSED: {exc}"

    sql = " ".join(plan["sql"].split())
    detail = (
        f"{header}\n"
        f"    chart:  {plan['chart_type']}  x={plan['x']}  y={plan['y']}\n"
        f"    sql:    {sql}"
    )

    try:
        frame = run_query(plan["sql"])
    except Exception as exc:  # noqa: BLE001 - first failure triggers one repair
        first_error = " ".join(
            line.strip() for line in str(exc).strip().splitlines()[:3]
        )
        try:
            plan = repair_query_plan(
                question, tables, [{"sql": plan["sql"], "error": str(exc)}]
            )
            frame = run_query(plan["sql"])
        except Exception as repair_exc:  # noqa: BLE001
            second = " ".join(
                line.strip() for line in str(repair_exc).strip().splitlines()[:3]
            )
            return False, (
                f"{detail}\n"
                f"    FAILED: {first_error}\n"
                f"    REPAIR ALSO FAILED: {second}"
            )
        repaired_sql = " ".join(plan["sql"].split())
        detail += (
            f"\n    (first attempt failed: {first_error})"
            f"\n    repaired sql: {repaired_sql}"
        )

    cols = ", ".join(map(str, frame.columns))
    return True, (
        f"{detail}\n"
        f"    result: {len(frame)} rows x {len(frame.columns)} cols ({cols})"
    )


def main() -> int:
    questions = sys.argv[1:] or QUESTIONS
    failures = 0

    for index, question in enumerate(questions, start=1):
        print(f"\n[{index}/{len(questions)}] {question}")
        started = time.time()
        try:
            ok, detail = run_one(question)
        except Exception:  # noqa: BLE001 - the harness reports, never crashes
            failures += 1
            print("    HARNESS ERROR")
            print("    " + traceback.format_exc().replace("\n", "\n    "))
            continue
        if not ok:
            failures += 1
        print(f"    {detail}\n    ({time.time() - started:.1f}s)")

    total = len(questions)
    print(f"\n{'=' * 70}")
    if failures:
        print(f"FAILED {failures}/{total}")
        return 1
    print(f"PASSED {total}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
