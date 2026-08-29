# Live end-to-end test: question -> schema retrieval -> Cortex SQL -> guard -> execution.
# Co-authored with CoCo
"""
Runs real questions against real Cortex and the real FINBI_DEMO tables.

Usage::

    python3 tests/test_nl2sql_live.py           # run every question
    python3 tests/test_nl2sql_live.py 3         # run only question index 3

For each question it reports whether the model produced SQL, whether the guard
accepted it, whether Snowflake executed it, and the shape of the result. A
question counts as passing only if all four succeed.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# The workspace is a symlink, which can leave sys.path[0] pointing somewhere that
# does not contain this file, so locate the test directory explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402

harness.install()

from app.pipeline import UnsafeQueryError, answer_question  # noqa: E402

QUESTIONS = [
    # Single-table aggregations
    "What is our net revenue by provider this year?",
    "How much have we paid in card processing fees each month?",
    "What is our transaction failure rate by terminal?",
    "Which sales category brings in the most money?",
    # Disputes
    "What is our chargeback win rate by provider?",
    "Which disputes are we most likely to lose?",
    # Settlements and ledger
    "Which settlement batches are still pending, and how much do they total?",
    "Show me the bank account balance trend since April",
    # Reference tables
    "Which provider charges us the most per transaction?",
    # Cross-table joins
    "What was the ledger impact of disputes we lost, by month?",
    "For each provider, show total settled amount and total disputed amount",
]


def run_one(index: int, question: str) -> tuple[bool, str]:
    """Run one question through the full pipeline. Returns (passed, detail)."""
    try:
        result = answer_question(question, k=3)
    except UnsafeQueryError as exc:
        return False, f"guard refused: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"failed: {exc}"

    plan, df = result["plan"], result["df"]
    retrieved = ", ".join(result["tables"])
    repaired = " (REPAIRED)" if plan.get("repaired") else ""

    if df.empty:
        return False, (
            f"returned ZERO rows [{retrieved}]\n        SQL: {plan['sql'][:300]}"
        )

    return True, (
        f"[{retrieved}] chart={plan['chart_type']:<7} "
        f"rows={len(df):<4} cols={len(df.columns)}{repaired}  {list(df.columns)[:5]}"
    )


def main() -> int:
    """Run the selected questions and print a pass/fail table."""
    if len(sys.argv) > 1:
        indices = [int(a) for a in sys.argv[1:]]
    else:
        indices = list(range(len(QUESTIONS)))

    passed = 0
    failures: list[tuple[str, str]] = []

    for i in indices:
        question = QUESTIONS[i]
        try:
            ok, detail = run_one(i, question)
        except Exception:  # noqa: BLE001
            ok, detail = False, "unexpected error:\n" + traceback.format_exc()

        mark = "PASS" if ok else "FAIL"
        print(f"{mark} {i:>2}. {question}")
        print(f"        {detail}")
        if ok:
            passed += 1
        else:
            failures.append((question, detail))

    print(f"\n{passed}/{len(indices)} questions passed end-to-end")
    return 0 if passed == len(indices) else 1


if __name__ == "__main__":
    sys.exit(main())
