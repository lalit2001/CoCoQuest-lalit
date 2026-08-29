# Snowflake session helpers: session lookup, query execution and Cortex calls, usable with or without Streamlit.
# Co-authored with CoCo
"""
Thin wrapper around the Snowpark session used everywhere else in the app.

Inside a Workspace Streamlit app there are no credentials to manage: the Snowflake
connection is embedded, so ``st.connection("snowflake").session()`` is all that is
needed.

Streamlit is imported *optionally*. When it is missing - a plain Python process,
a test run, the offline harness in ``tools/`` - the caching decorators degrade to
no-ops and the session falls back to a named connection from the Snowflake CLI
config. That means every module below this one can be exercised headlessly
instead of only inside a running app.

All Cortex calls and all generated queries funnel through here, so parameter
binding is enforced in exactly one place.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Sequence, TypeVar

import pandas as pd
from snowflake.snowpark import Session

#: Database and schema holding the six demo tables.
DATABASE = "FINBI_DEMO"
SCHEMA = "CORE"

#: Model for cheap, highly constrained calls: result summaries and intent
#: classification. Small keeps the demo responsive.
DEFAULT_MODEL = "llama3.1-8b"

#: Model for SQL and dashboard-spec generation. These need real reasoning about
#: joins, table aliases, CTE column names and GROUP BY/ORDER BY validity, and the
#: 8B model measurably fails at them: it produced ORDER BY on a non-grouped
#: column, referenced a CTE by name instead of by its column, and attributed a
#: CARD_MACHINE_ACCOUNTS column to the TRANSACTIONS alias. The 70B model handles
#: all three.
SQL_MODEL = "llama3.3-70b"

#: Cortex embedding model for schema retrieval. Produces 768-dim vectors.
EMBED_MODEL = "snowflake-arctic-embed-m"

F = TypeVar("F", bound=Callable[..., Any])

try:  # pragma: no cover - depends on runtime environment
    import streamlit as st

    HAS_STREAMLIT = True
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]
    HAS_STREAMLIT = False


def cache_resource(func: F) -> F:
    """``st.cache_resource`` when Streamlit is available, else a no-op."""
    if HAS_STREAMLIT:
        return st.cache_resource(show_spinner=False)(func)  # type: ignore[union-attr]
    return func


def cache_data(func: F) -> F:
    """``st.cache_data`` when Streamlit is available, else a no-op."""
    if HAS_STREAMLIT:
        return st.cache_data(show_spinner=False)(func)  # type: ignore[union-attr]
    return func


_headless_session: Session | None = None


def _build_headless_session() -> Session:
    """Create a session from the Snowflake CLI config, for non-Streamlit runs."""
    global _headless_session
    if _headless_session is None:
        _headless_session = Session.builder.config("connection_name", "default").create()
    return _headless_session


@cache_resource
def get_session() -> Session:
    """Return the Snowpark session for this app, cached per process.

    Inside a Workspace Streamlit app the connection is embedded: ``st.connection``
    resolves it with no credentials in source, and ``.session()`` hands back a
    Snowpark session. The ``ttl`` comes from ``SNOWFLAKE_CONNECTION_TTL``, which
    the Workspace container runtime sets.

    Falls back to a CLI connection when there is no Streamlit runtime, so the same
    code path works in the offline test harness.
    """
    if HAS_STREAMLIT:
        try:
            connection = st.connection(  # type: ignore[union-attr]
                "snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL")
            )
            session = connection.session()
        except Exception:  # noqa: BLE001 - no embedded connection means headless
            session = _build_headless_session()
    else:
        session = _build_headless_session()

    session.sql(f"USE SCHEMA {DATABASE}.{SCHEMA}").collect()
    return session


def run_query(sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
    """Execute ``sql`` and return the result as a pandas DataFrame.

    Args:
        sql: A single SQL statement. Callers passing LLM-generated SQL must run
            it through :func:`app.sql_guard.check_sql` first.
        params: Optional bind parameters.

    Returns:
        The full result set as a DataFrame, empty if there were no rows.
    """
    session = get_session()
    return session.sql(sql, params=list(params) if params else None).to_pandas()


def cortex_complete(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Run a Cortex text completion and return the model's reply.

    The prompt is passed as a bind parameter and never formatted into the
    statement text, so user input cannot alter the SQL being executed.
    """
    session = get_session()
    rows = session.sql(
        "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?) AS reply",
        params=[model, prompt],
    ).collect()
    return (rows[0]["REPLY"] or "").strip()


def qualified(table: str) -> str:
    """Return a fully qualified name for one of the demo tables."""
    return f"{DATABASE}.{SCHEMA}.{table}"


#: Leading noise in Snowpark errors: "(1304): 01c68de2-...-18f2...: 002028 (42601): "
_ERROR_NOISE = re.compile(
    r"\(\d+\):\s*|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\s*|^\d{6}\s*",
    re.IGNORECASE,
)


def summarize_error(error: object, limit: int = 400) -> str:
    """Condense a Snowflake error into one informative line.

    Snowflake puts the useless part first ("SQL compilation error:") and the part
    that actually says what went wrong ("ambiguous column name 'PROVIDER'") on
    the *next* line. Taking only the first line therefore throws away the detail
    a repair attempt needs, so this joins every line and strips the query-id and
    error-code noise.

    Args:
        error: An exception or message.
        limit: Maximum length of the returned string.

    Returns:
        A single-line, de-noised description of the failure.
    """
    lines = [ln.strip() for ln in str(error).splitlines() if ln.strip()]
    joined = " ".join(lines)
    cleaned = _ERROR_NOISE.sub("", joined).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit] if cleaned else str(error)[:limit]
