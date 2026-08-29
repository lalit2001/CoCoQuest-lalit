# Defensive validation that only single, read-only SELECT/WITH queries ever reach Snowflake.
# Co-authored with CoCo
"""
The SQL safety gate.

Every statement produced by a language model - whether from the chat tab or from
a dashboard widget - must pass :func:`check_sql` before execution. This module
deliberately imports nothing outside the standard library so the guard can be
unit-tested on its own, with no Streamlit or Snowflake session in scope.

The guard is a whitelist on statement *shape*, not a blacklist on strings:

1. The statement must begin with SELECT or WITH.
2. It must be exactly one statement (no stacked semicolons).
3. It must contain no SQL comments.
4. It must contain no write, DDL, permission or session keyword.

String literal contents are blanked before scanning, so a legitimate value like
``WHERE notes = 'customer asked to delete the charge'`` is not mistaken for a
DELETE statement, while a real ``DELETE`` outside quotes is still caught.
"""

from __future__ import annotations

import re

#: Statement keywords that must never appear in generated SQL: anything that
#: writes data, changes structure, changes permissions or changes session state.
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "CREATE", "ALTER", "TRUNCATE", "RENAME", "SWAP",
    "GRANT", "REVOKE", "OWNERSHIP",
    "CALL", "EXECUTE", "USE", "UNSET",
    "COPY", "PUT", "REMOVE", "UNLOAD",
    "BEGIN", "COMMIT", "ROLLBACK",
    "PROCEDURE", "TASK", "PIPE", "STAGE",
)

#: Only these two tokens may start a generated statement.
ALLOWED_STARTS = ("SELECT", "WITH")


def _scan_and_blank(sql: str) -> tuple[str, bool]:
    """Blank out string literal contents and report whether one was unterminated.

    Returns:
        ``(scanned_sql, unterminated)``. ``unterminated`` is True if a quote or
        ``$$`` block was opened and never closed, which means the rest of the
        statement was swallowed and cannot be scanned for keywords.
    """
    out: list[str] = []
    unterminated = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "$" and sql.startswith("$$", i):  # dollar-quoted block
            end = sql.find("$$", i + 2)
            out.append("''")
            if end == -1:
                unterminated = True
                break
            i = end + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(quote)
            i += 1
            closed = False
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # escaped quote
                        i += 2
                        continue
                    closed = True
                    break
                i += 1
            if not closed:
                unterminated = True
                break
            out.append(quote)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), unterminated


def blank_string_literals(sql: str) -> str:
    """Replace the *contents* of string literals with nothing, keeping the quotes.

    A value such as ``'please DROP everything'`` therefore cannot trip the
    keyword scan, while an actual ``DROP`` outside quotes still will. Handles
    single quotes, double quotes, doubled-quote escapes and ``$$`` blocks.
    """
    return _scan_and_blank(sql)[0]


def check_sql(sql: str) -> tuple[bool, str]:
    """Validate model-generated SQL before it is executed.

    Args:
        sql: The candidate statement.

    Returns:
        ``(True, "")`` if the statement is a single read-only query, otherwise
        ``(False, reason)`` where ``reason`` is safe to show to the user.
    """
    if not sql or not sql.strip():
        return False, "The model returned an empty query."

    scan, unterminated = _scan_and_blank(sql.strip())

    # An unterminated literal hides everything after it from the keyword scan,
    # so we can no longer prove the statement is safe. Snowflake would reject it
    # anyway; refusing here keeps the guard's guarantee airtight.
    if unterminated:
        return False, "Refused: the generated SQL has an unterminated string literal."

    # Comment markers are never legitimate here: the prompt forbids them, so
    # their presence means either prompt injection or a mangled response.
    if "--" in scan or "/*" in scan or "*/" in scan:
        return False, "Refused: the generated SQL contained a comment."

    # Stacked statements: any semicolon other than a single trailing one.
    body = scan.rstrip().rstrip(";").rstrip()
    if ";" in body:
        return False, "Refused: only a single SQL statement may be executed."

    if not re.match(rf"^\s*({'|'.join(ALLOWED_STARTS)})\b", body, re.IGNORECASE):
        return False, (
            "Refused: only SELECT and WITH queries are allowed. "
            "This assistant can read your books but never change them."
        )

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", body, re.IGNORECASE):
            return False, (
                f"Refused: the generated SQL contains '{keyword}'. "
                "This assistant can only read data, never modify it."
            )

    # SELECT ... INTO creates a table, so it writes despite starting with SELECT
    # and containing no keyword from the list above.
    if re.search(r"\bINTO\b", body, re.IGNORECASE):
        return False, (
            "Refused: SELECT ... INTO writes data, which this assistant "
            "cannot do."
        )

    # An unbalanced statement means the reply was truncated; executing it would
    # only produce a confusing Snowflake syntax error.
    if body.count("(") != body.count(")"):
        return False, "Refused: the generated SQL is incomplete (unbalanced brackets)."

    return True, ""


def is_safe_sql(sql: str) -> bool:
    """Boolean form of :func:`check_sql`."""
    return check_sql(sql)[0]
