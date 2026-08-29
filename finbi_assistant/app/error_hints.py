# Turns a Snowflake compilation error into a precise, actionable hint for the repair prompt.
# Co-authored with CoCo
"""
Targeted repair hints.

A generic "your query failed, fix it" retry is close to useless: the model tends
to make the same mistake again. What breaks the loop is telling it *exactly* what
was wrong and where the thing it wanted actually lives.

The motivating case: asked "what is the fees and charges", the model wrote
``SUM(fee) FROM FEE_SCHEDULE``. ``FEE_SCHEDULE`` has ``transaction_fee``,
``monthly_fee`` and ``chargeback_fee`` but no ``fee`` - that column is on
``TRANSACTIONS``. Two blind retries both failed. With the hint below ("fee does
not exist on FEE_SCHEDULE; it exists on TRANSACTIONS; FEE_SCHEDULE has these
columns: ...") the first retry succeeds.

This module is dependency-free so it can be unit-tested without a session.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Iterable

#: "invalid identifier 'FEE'" or "invalid identifier 'T.FEE'"
_INVALID_IDENTIFIER = re.compile(r"invalid identifier '([^']+)'", re.IGNORECASE)

#: "ambiguous column name 'PROVIDER'"
_AMBIGUOUS = re.compile(r"ambiguous column name '([^']+)'", re.IGNORECASE)


def _column_owners(column: str, tables: Iterable[dict[str, Any]]) -> list[str]:
    """Return the names of the given tables that actually have ``column``."""
    wanted = column.upper()
    return [
        table["name"]
        for table in tables
        if any(c["name"].upper() == wanted for c in table["columns"])
    ]


def _columns_of(table: dict[str, Any]) -> list[str]:
    """Return the column names of one table."""
    return [c["name"] for c in table["columns"]]


def _closest(column: str, candidates: Iterable[str], limit: int = 3) -> list[str]:
    """Return the candidate column names most similar to ``column``."""
    return difflib.get_close_matches(column.upper(), [c.upper() for c in candidates],
                                     n=limit, cutoff=0.5)


def invalid_identifier_hint(
    identifier: str,
    retrieved: list[dict[str, Any]],
    all_tables: list[dict[str, Any]],
) -> str:
    """Explain an ``invalid identifier`` error in terms of the real schema.

    Args:
        identifier: The identifier Snowflake rejected, e.g. ``FEE`` or ``T.FEE``.
        retrieved: The table metadata dicts that were offered to the model.
        all_tables: Every table in the metadata, so the hint can point at a table
            that was not retrieved.

    Returns:
        A multi-line hint, or an empty string if nothing useful can be said.
    """
    bare = identifier.split(".")[-1].strip('"')
    if not bare:
        return ""

    lines = [
        f'- The column "{bare}" does not exist on the table you used it on.',
    ]

    owners_retrieved = _column_owners(bare, retrieved)
    owners_all = _column_owners(bare, all_tables)

    if owners_retrieved:
        lines.append(
            f"  It DOES exist on: {', '.join(owners_retrieved)}. "
            f"Query it from there, or join to that table."
        )
    elif owners_all:
        lines.append(
            f"  It exists on {', '.join(owners_all)}, which is not in the list "
            f"above. Do not use it. Answer from the tables you were given."
        )
    else:
        lines.append("  It does not exist on ANY table. Do not invent it.")

    # Spell out the real columns of each retrieved table, plus near-miss
    # suggestions. This is what stops the model repeating the same guess.
    for table in retrieved:
        columns = _columns_of(table)
        near = _closest(bare, columns)
        suffix = f"  (closest matches: {', '.join(near)})" if near else ""
        lines.append(f"  {table['name']} columns: {', '.join(columns)}{suffix}")

    # The failure mode that a plain "use the right table" hint does not fix: the
    # wanted measures are spread across tables, so no single flat SELECT can work.
    # Asked for "fees and charges", the model moved fee to TRANSACTIONS and then
    # broke on monthly_fee, which is on FEE_SCHEDULE. It needs to be told to stop
    # flattening and combine per-table aggregates instead.
    if owners_retrieved and len(retrieved) > 1:
        lines.append(
            "  IMPORTANT: if the measures you want live on DIFFERENT tables, a "
            "single flat SELECT cannot work. Either aggregate each table in its "
            "own CTE and join the CTEs, or build one row per measure with "
            "UNION ALL, for example:"
        )
        lines.append(
            "    SELECT 'fees paid' AS metric, ROUND(SUM(t.fee), 2) AS amount "
            "FROM TRANSACTIONS t"
        )
        lines.append(
            "    UNION ALL SELECT 'avg contracted rate', "
            "ROUND(AVG(f.transaction_fee), 4) FROM FEE_SCHEDULE f"
        )

    return "\n".join(lines)


def ambiguous_column_hint(column: str, retrieved: list[dict[str, Any]]) -> str:
    """Explain an ``ambiguous column name`` error."""
    owners = _column_owners(column, retrieved)
    where = f" It appears on: {', '.join(owners)}." if owners else ""
    return (
        f'- The column "{column}" exists on more than one table in your query.{where}\n'
        f"  Give every table a short alias and qualify every column with it."
    )


def build_hints(
    error: str,
    retrieved: list[dict[str, Any]],
    all_tables: list[dict[str, Any]],
) -> str:
    """Build the schema-aware hint block for a failed query.

    Args:
        error: The (already flattened) Snowflake error message.
        retrieved: Table metadata offered to the model.
        all_tables: Every table in the metadata.

    Returns:
        A hint block, or an empty string when the error is not one we can
        diagnose precisely (in which case the prompt's generic guidance applies).
    """
    text = str(error)
    hints: list[str] = []

    for match in _INVALID_IDENTIFIER.finditer(text):
        hint = invalid_identifier_hint(match.group(1), retrieved, all_tables)
        if hint:
            hints.append(hint)

    for match in _AMBIGUOUS.finditer(text):
        hints.append(ambiguous_column_hint(match.group(1).split(".")[-1], retrieved))

    return "\n".join(hints)


def format_attempt_history(attempts: list[dict[str, str]]) -> str:
    """Render previous failed attempts so the model does not repeat them.

    Args:
        attempts: Dicts with ``sql`` and ``error`` keys, oldest first.

    Returns:
        A numbered list of what was already tried and how it failed.
    """
    if not attempts:
        return "(none)"
    blocks = []
    for number, attempt in enumerate(attempts, start=1):
        blocks.append(
            f"Attempt {number}:\n"
            f"  SQL:   {attempt['sql']}\n"
            f"  Error: {attempt['error']}"
        )
    return "\n".join(blocks)
