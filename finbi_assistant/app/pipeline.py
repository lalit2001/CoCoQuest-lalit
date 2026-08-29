# Orchestrates one question end to end: retrieve schema, generate SQL, guard it, execute, repair once on failure.
# Co-authored with CoCo
"""
The single code path a question travels.

Both the chat tab and the test suite call :func:`answer_question`, so what the
tests prove is exactly what the app does. Keeping the orchestration here (rather
than inline in ``streamlit_app.py``) is what makes the retry behaviour testable.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

import re

from app.metadata_rag import retrieve_relevant_tables
from app.nl2sql import generate_query_plan, repair_query_plan, sentinel_kind
from app.snowflake_utils import run_query, summarize_error


class UnsafeQueryError(RuntimeError):
    """Raised when a request was refused rather than executed."""


class UnanswerableError(RuntimeError):
    """Raised when the question cannot be answered from the six demo tables."""


#: Words that signal the user genuinely wants to write/mutate data.
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|remove|destroy|"
    r"wipe|erase|modify|change\s+data|move\s+data)\b",
    re.IGNORECASE,
)

MAX_ATTEMPTS = 4


def answer_question(
    question: str,
    k: int = 3,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Take a natural-language question all the way to a DataFrame.

    Retrieves the relevant tables, asks Cortex for SQL plus a chart choice, runs
    it through the safety guard, executes it, and on a Snowflake error loops -
    feeding back every previous attempt plus a schema-aware diagnosis of what was
    wrong - until the query runs or ``max_attempts`` is reached.

    Args:
        question: The user's question.
        k: How many tables to retrieve into the prompt.
        max_attempts: Total execution attempts, including the first.

    Returns:
        A dict with ``plan`` (the final query plan), ``df`` (the result),
        ``tables`` (retrieved table names), ``attempts`` (how many executions were
        needed) and ``history`` (the failed attempts, each with ``sql`` and
        ``error``).

    Raises:
        UnsafeQueryError: If the request was a write request, refused either by
            the prompt (sentinel) or by the guard.
        UnanswerableError: If the question needs data these tables do not hold,
            or the model concluded as much while repairing.
        RuntimeError: If the query still failed after ``max_attempts``.
    """
    max_attempts = max(1, int(max_attempts))
    tables = retrieve_relevant_tables(question, k=k)

    try:
        plan = generate_query_plan(question, tables)
    except ValueError as exc:
        if str(exc).startswith("Refused:"):
            raise UnsafeQueryError(str(exc)) from exc
        raise RuntimeError(str(exc)) from exc

    # Check for false-positive refusals: if the model returned a read-only
    # sentinel but the question has no write keywords, retry once with a hint.
    try:
        _check_sentinel(plan["sql"], question)
    except ValueError:
        # False positive — retry with a clarified question
        clarified = (
            f"{question}\n\n(NOTE: This is a READ-ONLY question about "
            f"existing data. Do NOT refuse it. Write a SELECT query to answer it.)"
        )
        try:
            plan = generate_query_plan(clarified, tables)
        except ValueError as exc2:
            if str(exc2).startswith("Refused:"):
                raise UnsafeQueryError(str(exc2)) from exc2
            raise RuntimeError(str(exc2)) from exc2
        _check_sentinel(plan["sql"])  # no question arg = strict mode

    history: list[dict[str, str]] = []

    for attempt in range(1, max_attempts + 1):
        try:
            df = run_query(plan["sql"])
        except Exception as exc:  # noqa: BLE001 - any Snowflake error is retryable
            history.append(
                {"sql": plan["sql"], "error": summarize_error(exc)}
            )
            if attempt == max_attempts:
                raise RuntimeError(
                    f"I tried {attempt} times and could not get a working query. "
                    f"Snowflake's last error was: {history[-1]['error']}"
                ) from exc

            try:
                plan = repair_query_plan(question, tables, history)
            except ValueError as repair_exc:
                if str(repair_exc).startswith("Refused:"):
                    raise UnsafeQueryError(str(repair_exc)) from repair_exc
                raise RuntimeError(
                    f"The query failed and could not be repaired: "
                    f"{history[-1]['error']}"
                ) from exc

            _check_sentinel(plan["sql"], question)
            continue

        return {
            "plan": plan,
            "df": df if isinstance(df, pd.DataFrame) else pd.DataFrame(),
            "tables": plan["tables"],
            "attempts": attempt,
            "history": history,
            "error": history[0]["error"] if history else None,
        }

    raise RuntimeError("Unreachable: the retry loop always returns or raises.")


def _check_sentinel(sql: str, question: str = "") -> None:
    """Convert a sentinel answer into the matching refusal or explanation.

    For read-only sentinels, checks whether the user's question actually contains
    write keywords. If not, this is a false positive and we raise a ValueError
    (not UnsafeQueryError) so the caller can retry with a clarification.
    """
    kind = sentinel_kind(sql)
    if kind == "read_only":
        if question and not _WRITE_KEYWORDS.search(question):
            raise ValueError(
                "false_positive_refusal: The model incorrectly refused a "
                "read-only question as a write request."
            )
        raise UnsafeQueryError(
            "Refused: that would change or remove data. This assistant is "
            "read-only - it can query your books but never modify them."
        )
    if kind == "unanswerable":
        raise UnanswerableError(
            "That cannot be answered from the available tables (transactions, "
            "disputes, settlements, ledger, terminals and the fee schedule)."
        )
