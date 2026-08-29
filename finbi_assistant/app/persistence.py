# Persists chat turns and saved dashboards to Snowflake so history survives a page reload.
# Co-authored with CoCo
"""
Durable history for the assistant.

Streamlit's ``session_state`` is lost on refresh, on a rerun of the container, and
whenever a second person opens the app. Anything worth calling "history" has to
live in a table, so chat turns go to ``CHAT_MESSAGES`` and dashboard specs to
``DASHBOARDS``.

Two decisions worth stating:

* **Chat turns store the SQL, not the result.** Reopening a past answer re-runs
  its query, so the numbers are current rather than a stale snapshot, and the
  table stays small no matter how large the answers were. The stored SQL is
  re-checked by the guard before it is ever re-executed.
* **A dashboard is fully described by its spec.** Because the renderer is
  generic, persisting the JSON is enough to reconstruct the dashboard exactly -
  and because the prompt log is stored with it, a reloaded dashboard is still
  conversationally editable rather than frozen.

Note the asymmetry with :mod:`app.sql_guard`: the guard exists to stop
*model-generated* SQL from writing. The statements here are hand-written with
bound parameters and only ever touch these two tables, which is why they are
allowed to insert.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pandas as pd

from app.snowflake_utils import DATABASE, SCHEMA, get_session, run_query

CHAT_TABLE = f"{DATABASE}.{SCHEMA}.CHAT_MESSAGES"
DASHBOARD_TABLE = f"{DATABASE}.{SCHEMA}.DASHBOARDS"

#: Outcomes recorded against a chat turn.
OUTCOMES = ("answered", "refused", "unanswerable", "failed")


def new_id() -> str:
    """Return a fresh UUID string, used for session, message and dashboard ids."""
    return str(uuid.uuid4())


def current_user() -> str:
    """Return the Snowflake user the app is running as."""
    try:
        rows = get_session().sql("SELECT CURRENT_USER() AS u").collect()
        return str(rows[0]["U"] or "UNKNOWN")
    except Exception:  # noqa: BLE001 - identity is nice to have, not essential
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------


def save_turn(
    session_id: str,
    turn_index: int,
    question: str,
    outcome: str,
    *,
    summary: str | None = None,
    sql_text: str | None = None,
    chart_type: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
    tables_used: list[str] | None = None,
    attempts: int | None = None,
    row_count: int | None = None,
    detail: str | None = None,
) -> str | None:
    """Persist one chat turn.

    Args:
        session_id: Id of the browser session, so a conversation stays grouped.
        turn_index: Position of this turn within the conversation.
        question: What the user asked.
        outcome: One of :data:`OUTCOMES`.
        summary: The natural-language answer, when there was one.
        sql_text: The SQL that produced the answer.
        chart_type: The chart that was drawn.
        x_column: Resolved x axis column.
        y_column: Resolved y axis column.
        tables_used: Retrieved table names.
        attempts: How many execution attempts were needed.
        row_count: Rows returned.
        detail: Refusal reason or error message.

    Returns:
        The new message id, or None if the write failed. A failed write is logged
        into the returned value only - it never interrupts answering, because
        losing history is far less bad than losing the answer.
    """
    if outcome not in OUTCOMES:
        outcome = "failed"

    message_id = new_id()
    try:
        get_session().sql(
            f"""
            INSERT INTO {CHAT_TABLE} (
                message_id, session_id, user_name, turn_index, question, outcome,
                summary, sql_text, chart_type, x_column, y_column, tables_used,
                attempts, row_count, detail
            )
            VALUES (?, ?, CURRENT_USER(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params=[
                message_id,
                session_id,
                int(turn_index),
                question,
                outcome,
                summary,
                sql_text,
                chart_type,
                x_column,
                y_column,
                ", ".join(tables_used or []) or None,
                int(attempts) if attempts is not None else None,
                int(row_count) if row_count is not None else None,
                detail,
            ],
        ).collect()
        return message_id
    except Exception:  # noqa: BLE001 - never break the answer over history
        return None


def load_recent_turns(limit: int = 50, session_id: str | None = None) -> pd.DataFrame:
    """Load recent chat turns, newest first.

    Args:
        limit: Maximum rows to return.
        session_id: Restrict to one conversation, or None for every conversation
            belonging to the current user.

    Returns:
        A DataFrame of turns, empty if there is no history or the read failed.
    """
    limit = max(1, min(int(limit), 500))
    where = "WHERE user_name = CURRENT_USER()"
    params: list[Any] = []
    if session_id:
        where += " AND session_id = ?"
        params.append(session_id)

    try:
        return run_query(
            f"""
            SELECT message_id, session_id, turn_index, asked_at, question, outcome,
                   summary, sql_text, chart_type, x_column, y_column, tables_used,
                   attempts, row_count, detail
            FROM {CHAT_TABLE}
            {where}
            ORDER BY asked_at DESC
            LIMIT {limit}
            """,
            params=params or None,
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def conversation_summary(limit: int = 20) -> pd.DataFrame:
    """Summarise past conversations for a history picker.

    Returns:
        One row per session with its start time, turn count and first question.
    """
    limit = max(1, min(int(limit), 200))
    try:
        return run_query(
            f"""
            SELECT session_id,
                   MIN(asked_at)                       AS started_at,
                   COUNT(*)                            AS turns,
                   COUNT_IF(outcome = 'answered')      AS answered,
                   MIN_BY(question, turn_index)        AS first_question
            FROM {CHAT_TABLE}
            WHERE user_name = CURRENT_USER()
            GROUP BY session_id
            ORDER BY started_at DESC
            LIMIT {limit}
            """
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def delete_history() -> bool:
    """Delete every chat turn belonging to the current user."""
    try:
        get_session().sql(
            f"DELETE FROM {CHAT_TABLE} WHERE user_name = CURRENT_USER()"
        ).collect()
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


def save_dashboard(
    spec: dict[str, Any],
    prompt_log: list[str] | None = None,
    dashboard_id: str | None = None,
) -> str | None:
    """Insert or update a saved dashboard.

    Args:
        spec: The validated dashboard spec.
        prompt_log: The instructions that produced it, oldest first.
        dashboard_id: Update this dashboard if given, otherwise create one.

    Returns:
        The dashboard id, or None if the write failed.
    """
    identifier = dashboard_id or new_id()
    name = str(spec.get("title") or "Untitled dashboard")[:200]
    spec_json = json.dumps(spec)
    log_json = json.dumps(prompt_log or [])
    widgets = len(spec.get("widgets") or [])

    try:
        session = get_session()
        if dashboard_id:
            session.sql(
                f"""
                UPDATE {DASHBOARD_TABLE}
                SET name = ?, spec = PARSE_JSON(?), prompt_log = PARSE_JSON(?),
                    widget_count = ?, updated_at = CURRENT_TIMESTAMP()
                WHERE dashboard_id = ? AND user_name = CURRENT_USER()
                """,
                params=[name, spec_json, log_json, widgets, dashboard_id],
            ).collect()
        else:
            session.sql(
                f"""
                INSERT INTO {DASHBOARD_TABLE}
                    (dashboard_id, user_name, name, spec, prompt_log, widget_count)
                SELECT ?, CURRENT_USER(), ?, PARSE_JSON(?), PARSE_JSON(?), ?
                """,
                params=[identifier, name, spec_json, log_json, widgets],
            ).collect()
        return identifier
    except Exception:  # noqa: BLE001
        return None


def list_dashboards(limit: int = 50) -> pd.DataFrame:
    """List saved dashboards for the current user, most recently updated first."""
    limit = max(1, min(int(limit), 200))
    try:
        return run_query(
            f"""
            SELECT dashboard_id, name, widget_count, created_at, updated_at
            FROM {DASHBOARD_TABLE}
            WHERE user_name = CURRENT_USER()
            ORDER BY updated_at DESC
            LIMIT {limit}
            """
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def load_dashboard(dashboard_id: str) -> tuple[dict[str, Any], list[str]] | None:
    """Load one saved dashboard.

    Returns:
        ``(spec, prompt_log)``, or None if it does not exist or could not be read.
    """
    try:
        frame = run_query(
            f"""
            SELECT TO_VARCHAR(spec) AS spec_json, TO_VARCHAR(prompt_log) AS log_json
            FROM {DASHBOARD_TABLE}
            WHERE dashboard_id = ? AND user_name = CURRENT_USER()
            """,
            params=[dashboard_id],
        )
    except Exception:  # noqa: BLE001
        return None

    if frame.empty:
        return None

    try:
        spec = json.loads(frame.iloc[0]["SPEC_JSON"])
        log = json.loads(frame.iloc[0]["LOG_JSON"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    return spec, list(log)


def delete_dashboard(dashboard_id: str) -> bool:
    """Delete one saved dashboard belonging to the current user."""
    try:
        get_session().sql(
            f"DELETE FROM {DASHBOARD_TABLE} "
            f"WHERE dashboard_id = ? AND user_name = CURRENT_USER()",
            params=[dashboard_id],
        ).collect()
        return True
    except Exception:  # noqa: BLE001
        return False
