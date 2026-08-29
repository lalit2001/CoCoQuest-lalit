# Live test: the chart type and axes handed to the renderer must be valid for the data that came back.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_chart_wiring.py

This guards a bug that is invisible until you look at the screen: the model names
its axes in lower_snake_case ("net_revenue") while Snowflake returns columns
uppercased ("NET_REVENUE"). If the raw ``plan["x"]`` is passed to the renderer it
never matches a column, ``build_figure`` returns None, and *every* chart silently
degrades to a table. Nothing raises, so no other test catches it.

Here we assert that for any chart type needing axes, the resolved x and y are real
columns of the returned DataFrame.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402

harness.install()

from app.charts import build_figure  # noqa: E402
from app.output_picker import resolve_output  # noqa: E402
from app.pipeline import answer_question  # noqa: E402

#: Chart types that require both axes to be resolvable.
NEEDS_AXES = ("bar", "line", "scatter", "pie")

QUESTIONS = [
    "What is our net revenue by provider this year?",
    "How much have we paid in card processing fees each month?",
    "Show me the bank account balance trend since April",
    "What is our chargeback win rate by provider?",
    "Which sales category brings in the most money?",
    "How many disputes are still open?",
]


def main() -> int:
    """Check chart wiring for every question."""
    problems: list[str] = []

    for index, question in enumerate(QUESTIONS):
        try:
            result = answer_question(question)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{index}. {question}\n    pipeline failed: {exc}")
            continue

        frame, plan = result["df"], result["plan"]
        output = resolve_output(frame, plan)
        chart_type, x, y = output["chart_type"], output["x"], output["y"]

        note = ""
        if chart_type in NEEDS_AXES:
            if x is None or y is None:
                problems.append(
                    f"{index}. {question}\n    {chart_type} has unresolved axes "
                    f"x={x!r} y={y!r}; columns are {list(frame.columns)}"
                )
                note = "  <-- UNRESOLVED AXES"
            elif x not in frame.columns or y not in frame.columns:
                problems.append(
                    f"{index}. {question}\n    {chart_type} axes not in result: "
                    f"x={x!r} y={y!r}; columns are {list(frame.columns)}"
                )
                note = "  <-- AXES NOT IN RESULT"
            elif build_figure(frame, chart_type, x, y) is None:
                # Only a real problem when Plotly is installed; without it the
                # native fallback is expected to take over.
                from app.charts import HAS_PLOTLY

                if HAS_PLOTLY:
                    problems.append(
                        f"{index}. {question}\n    {chart_type} produced no figure "
                        f"with x={x!r} y={y!r}"
                    )
                    note = "  <-- NO FIGURE"

        print(
            f"{index}. {question}\n"
            f"    model asked for {plan['chart_type']:<7} -> rendered {chart_type:<7} "
            f"({output['source']})  x={x} y={y}{note}"
        )

    if problems:
        print(f"\nFAILED ({len(problems)} problems)\n")
        for line in problems:
            print("  " + line)
        return 1
    print(f"\nPASSED  {len(QUESTIONS)} questions: every chart has valid, resolvable axes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
