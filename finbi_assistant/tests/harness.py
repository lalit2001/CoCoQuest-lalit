# Shared test harness: gives the app a real Snowpark session outside Streamlit in Snowflake.
# Co-authored with CoCo
"""
The app calls ``get_active_session()``, which only exists inside Streamlit in
Snowflake. For local testing we build an ordinary Snowpark session from the
sandbox's Snowflake connection and patch it into :mod:`app.snowflake_utils`, so
every module under test talks to the real FINBI_DEMO tables and the real Cortex
functions without any code changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snowflake.snowpark import Session  # noqa: E402

_session: Session | None = None


def _build_session() -> Session:
    """Create a Snowpark session pointed at FINBI_DEMO.CORE."""
    session = Session.builder.getOrCreate()
    session.sql("USE WAREHOUSE FINBI_WH").collect()
    session.sql("USE SCHEMA FINBI_DEMO.CORE").collect()
    return session


def install() -> Session:
    """Patch ``app.snowflake_utils.get_session`` to return a local session.

    Must be called before importing any module that resolves the session at
    import time. Returns the session so tests can run their own SQL too.
    """
    global _session
    if _session is None:
        _session = _build_session()

    import app.snowflake_utils as utils

    # Replace the Streamlit-cached accessor. Everything downstream - run_query,
    # cortex_complete, metadata_rag, nl2sql, dashboard_engine - goes through it.
    utils.get_session = lambda: _session  # type: ignore[assignment]
    return _session
