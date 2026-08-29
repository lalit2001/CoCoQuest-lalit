# Live test: chat turns and dashboard specs survive a round trip through Snowflake.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_persistence.py

Writes to FINBI_DEMO.CORE.CHAT_MESSAGES and DASHBOARDS under a throwaway session
id, verifies it can be read back, then cleans up after itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness  # noqa: E402

harness.install()

from app.persistence import (  # noqa: E402
    CHAT_TABLE,
    DASHBOARD_TABLE,
    conversation_summary,
    delete_dashboard,
    list_dashboards,
    load_dashboard,
    load_recent_turns,
    new_id,
    save_dashboard,
    save_turn,
)
from app.snowflake_utils import get_session  # noqa: E402

SPEC = {
    "title": "Persistence test dashboard",
    "layout": "grid-2col",
    "filters": [],
    "widgets": [
        {
            "id": "w1",
            "type": "kpi",
            "title": "Total settled",
            "sql": "SELECT ROUND(SUM(net_settled_amount), 2) AS total FROM SETTLEMENTS",
            "x": None,
            "y": None,
        }
    ],
}


def main() -> int:  # noqa: C901 - a linear script reads better than helpers here
    """Round-trip a conversation and a dashboard, then clean up."""
    problems: list[str] = []
    session_id = new_id()
    dashboard_id: str | None = None

    try:
        # --- chat turns -----------------------------------------------------
        ok = save_turn(
            session_id, 0, "What is our net revenue by provider?", "answered",
            summary="RAZORPAY leads at INR 250,000.",
            sql_text="SELECT provider, SUM(net_amount) AS net_revenue FROM TRANSACTIONS GROUP BY provider",
            chart_type="bar", x_column="PROVIDER", y_column="NET_REVENUE",
            tables_used=["TRANSACTIONS", "FEE_SCHEDULE"], attempts=1, row_count=3,
        )
        if not ok:
            problems.append("save_turn returned no id for an answered turn")

        if not save_turn(
            session_id, 1, "Delete all transactions", "refused",
            detail="Refused: read-only assistant.",
        ):
            problems.append("save_turn returned no id for a refused turn")

        turns = load_recent_turns(limit=10, session_id=session_id)
        if len(turns) != 2:
            problems.append(f"expected 2 persisted turns, read back {len(turns)}")
        else:
            answered = turns[turns["OUTCOME"] == "answered"]
            if answered.empty:
                problems.append("answered turn missing from history")
            else:
                row = answered.iloc[0]
                if row["CHART_TYPE"] != "bar":
                    problems.append(f"chart_type not persisted: {row['CHART_TYPE']}")
                if row["ATTEMPTS"] != 1:
                    problems.append(f"attempts not persisted: {row['ATTEMPTS']}")
                if "TRANSACTIONS" not in str(row["TABLES_USED"]):
                    problems.append(f"tables_used not persisted: {row['TABLES_USED']}")
            refused = turns[turns["OUTCOME"] == "refused"]
            if refused.empty or "Refused" not in str(refused.iloc[0]["DETAIL"]):
                problems.append("refusal detail not persisted")

        summary = conversation_summary(limit=50)
        if summary.empty or session_id not in set(summary["SESSION_ID"]):
            problems.append("conversation_summary did not include the test session")
        else:
            row = summary[summary["SESSION_ID"] == session_id].iloc[0]
            if row["TURNS"] != 2 or row["ANSWERED"] != 1:
                problems.append(
                    f"summary counts wrong: turns={row['TURNS']} answered={row['ANSWERED']}"
                )

        # --- dashboards -----------------------------------------------------
        dashboard_id = save_dashboard(SPEC, ["Build a persistence test dashboard"])
        if not dashboard_id:
            problems.append("save_dashboard returned no id")
        else:
            loaded = load_dashboard(dashboard_id)
            if loaded is None:
                problems.append("load_dashboard returned None for a saved dashboard")
            else:
                spec, log = loaded
                if spec.get("title") != SPEC["title"]:
                    problems.append(f"spec title not round-tripped: {spec.get('title')}")
                if len(spec.get("widgets", [])) != 1:
                    problems.append("spec widgets not round-tripped")
                if log != ["Build a persistence test dashboard"]:
                    problems.append(f"prompt log not round-tripped: {log}")

            listed = list_dashboards(limit=100)
            if listed.empty or dashboard_id not in set(listed["DASHBOARD_ID"]):
                problems.append("saved dashboard missing from list_dashboards")

            # Updating in place must not create a second row.
            before = len(list_dashboards(limit=200))
            renamed = dict(SPEC, title="Renamed dashboard")
            save_dashboard(renamed, ["edit"], dashboard_id=dashboard_id)
            after = len(list_dashboards(limit=200))
            if after != before:
                problems.append(f"update created a new row: {before} -> {after}")
            again = load_dashboard(dashboard_id)
            if not again or again[0].get("title") != "Renamed dashboard":
                problems.append("update did not persist the new title")

    finally:
        session = get_session()
        session.sql(
            f"DELETE FROM {CHAT_TABLE} WHERE session_id = ?", params=[session_id]
        ).collect()
        if dashboard_id:
            delete_dashboard(dashboard_id)
            if load_dashboard(dashboard_id) is not None:
                problems.append("delete_dashboard did not remove the dashboard")

    if problems:
        print(f"FAILED ({len(problems)} problems)\n")
        for line in problems:
            print("  " + line)
        return 1
    print("PASSED  chat turns and dashboard specs round-trip through Snowflake, "
          "update in place, and delete cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
