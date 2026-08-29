# Renders the real Streamlit app headlessly with AppTest to prove it boots and answers a question.
# Co-authored with CoCo
"""
Run with::

    python3 tests/test_app_smoke.py

Uses ``streamlit.testing.v1.AppTest`` to execute ``streamlit_app.py`` the way the
real runtime does - session state, tabs, widgets and all - and fails on any
uncaught exception. Then it drives one real question through the Ask tab and one
dashboard build, so a regression in the UI layer is caught here rather than in
front of an audience.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "finbi_assistant"
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(APP_DIR))

import harness  # noqa: E402

harness.install()

from streamlit.testing.v1 import AppTest  # noqa: E402

from app.persistence import (  # noqa: E402
    CHAT_TABLE,
    conversation_summary,
    delete_dashboard,
    load_dashboard,
    load_recent_turns,
)
from app.snowflake_utils import get_session  # noqa: E402

APP_FILE = APP_DIR / "streamlit_app.py"

#: Generous: persistence checks drive multiple Cortex round trips.
TIMEOUT = 300


def _exceptions(app: AppTest) -> list[str]:
    """Return the text of any exceptions the script raised."""
    return [str(e.value) for e in app.exception]


def _state(app: AppTest, key: str, default: object = None) -> object:
    """Read a session-state key. AppTest's session state has no .get()."""
    try:
        return app.session_state[key]
    except (KeyError, AttributeError):
        return default


def test_boots() -> list[str]:
    """The app must render both tabs with no exception."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()

    errors = _exceptions(app)
    if errors:
        problems.append("app raised on first render: " + " | ".join(errors))
        return problems

    if not app.tabs:
        problems.append("expected the Ask and Dashboard Builder tabs, found none")
    if not app.title:
        problems.append("expected a page title")

    print(f"  boot: {len(app.tabs)} tabs, title={app.title[0].value!r}")
    return problems


def test_answers_a_question() -> list[str]:
    """Typing a question must produce an answer with no exception."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()

    if not app.chat_input:
        return ["no chat_input found on the Ask tab"]

    app.chat_input[0].set_value("What is our net revenue by provider this year?").run()

    errors = _exceptions(app)
    if errors:
        problems.append("asking a question raised: " + " | ".join(errors))
        return problems

    history = _state(app, "chat_history") or []
    if not history:
        problems.append("the question did not produce a history entry")
        return problems

    turn = history[-1]
    if turn.get("failure") or turn.get("refusal"):
        problems.append(f"legitimate question was rejected: {turn}")
        return problems
    if not turn.get("summary"):
        problems.append(f"no summary produced: {turn.keys()}")

    frame = turn.get("df")
    rows = 0 if frame is None else len(frame)
    print(
        f"  ask : chart={turn.get('chart_type')} rows={rows} "
        f"tables={turn.get('tables')}"
    )
    print(f"        summary: {str(turn.get('summary'))[:140]}")
    return problems


def test_refuses_a_write() -> list[str]:
    """A destructive request must be refused, not executed."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()
    app.chat_input[0].set_value("Delete all transactions").run()

    errors = _exceptions(app)
    if errors:
        problems.append("refusal path raised: " + " | ".join(errors))
        return problems

    history = _state(app, "chat_history") or []
    if not history:
        return ["the destructive request produced no history entry"]

    turn = history[-1]
    if not turn.get("refusal"):
        problems.append(f"expected a refusal, got keys {list(turn)}")
    else:
        print(f"  guard: {turn['refusal'][:120]}")
    return problems


def test_builds_a_dashboard() -> list[str]:
    """The Dashboard Builder must produce a renderable spec with no exception."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()

    areas = app.text_area
    if not areas:
        return ["no text_area found on the Dashboard Builder tab"]

    areas[0].set_value("Build me a dashboard on settlement reliability")
    # The Build button is the first primary button on that tab.
    target = next((b for b in app.button if b.label in ("Build", "Update")), None)
    if target is None:
        return [f"no Build button found; buttons were {[b.label for b in app.button]}"]

    target.click().run()

    errors = _exceptions(app)
    if errors:
        problems.append("building a dashboard raised: " + " | ".join(errors))
        return problems

    spec = _state(app, "dashboard_spec")
    if not spec:
        problems.append("no dashboard_spec was stored in session state")
        return problems

    widgets = spec.get("widgets", [])
    if len(widgets) < 2:
        problems.append(f"expected several widgets, got {len(widgets)}")
    print(
        f"  dash: {spec.get('title')!r} layout={spec.get('layout')} "
        f"widgets={[w['type'] for w in widgets]} "
        f"filters={[f['id'] for f in spec.get('filters', [])]}"
    )
    return problems


def test_persists_and_shows_history() -> list[str]:
    """A turn must reach the CHAT_MESSAGES table and appear in the History tab."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()

    session_id = _state(app, "chat_session_id")
    if not session_id:
        return ["no chat_session_id was generated for the session"]

    app.chat_input[0].set_value("How many disputes are still open?").run()
    errors = _exceptions(app)
    if errors:
        return ["asking a question raised: " + " | ".join(errors)]

    # Read the row back out of Snowflake, not out of session state.
    turns = load_recent_turns(limit=20, session_id=str(session_id))
    if turns.empty:
        problems.append("the turn was not persisted to CHAT_MESSAGES")
        return problems

    row = turns.iloc[0]
    if row["OUTCOME"] not in ("answered", "unanswerable"):
        problems.append(f"unexpected persisted outcome: {row['OUTCOME']}")
    if row["OUTCOME"] == "answered" and not row["SQL_TEXT"]:
        problems.append("an answered turn was persisted without its SQL")

    print(
        f"  save: session={str(session_id)[:8]} outcome={row['OUTCOME']} "
        f"chart={row['CHART_TYPE']} rows={row['ROW_COUNT']} attempts={row['ATTEMPTS']}"
    )

    # The History tab must render that row without raising. A fresh AppTest run
    # proves it survives losing session state, which is the whole point.
    fresh = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()
    errors = _exceptions(fresh)
    if errors:
        problems.append("History tab raised on a fresh session: " + " | ".join(errors))
    else:
        summary = conversation_summary(limit=25)
        if summary.empty or str(session_id) not in set(summary["SESSION_ID"]):
            problems.append("the conversation is missing from conversation_summary")
        else:
            print(f"  hist: {len(summary)} conversation(s) visible after a reload")

    # Clean up so repeated runs do not accumulate rows.
    get_session().sql(
        f"DELETE FROM {CHAT_TABLE} WHERE session_id = ?", params=[str(session_id)]
    ).collect()
    return problems


def test_saves_and_reopens_a_dashboard() -> list[str]:
    """Saving a dashboard must survive a reload and come back editable."""
    problems: list[str] = []
    app = AppTest.from_file(str(APP_FILE), default_timeout=TIMEOUT).run()

    app.text_area[0].set_value("Build a dashboard on chargeback performance")
    build = next((b for b in app.button if b.label in ("Build", "Update")), None)
    if build is None:
        return ["no Build button found"]
    build.click().run()

    if _exceptions(app):
        return ["building raised: " + " | ".join(_exceptions(app))]

    spec = _state(app, "dashboard_spec")
    if not spec:
        return ["no spec produced, cannot test saving"]

    save = next((b for b in app.button if "Save" in b.label), None)
    if save is None:
        return [f"no Save button found; buttons were {[b.label for b in app.button]}"]
    save.click().run()

    if _exceptions(app):
        problems.append("saving raised: " + " | ".join(_exceptions(app)))

    dashboard_id = _state(app, "loaded_dashboard_id")
    if not dashboard_id:
        problems.append("saving did not record a dashboard id")
        return problems

    restored = load_dashboard(str(dashboard_id))
    if restored is None:
        problems.append("the saved dashboard could not be read back")
    else:
        restored_spec, restored_log = restored
        if restored_spec.get("title") != spec.get("title"):
            problems.append(
                f"title changed on round trip: {spec.get('title')!r} -> "
                f"{restored_spec.get('title')!r}"
            )
        if len(restored_spec.get("widgets", [])) != len(spec.get("widgets", [])):
            problems.append("widget count changed on round trip")
        if not restored_log:
            problems.append("the prompt log was not saved with the spec")
        else:
            print(
                f"  dsave: {restored_spec.get('title')!r} "
                f"widgets={len(restored_spec.get('widgets', []))} "
                f"prompts={len(restored_log)}"
            )

    delete_dashboard(str(dashboard_id))
    return problems


def main() -> int:
    """Run every UI check and report."""
    checks = (
        ("boots", test_boots),
        ("answers a question", test_answers_a_question),
        ("refuses a write", test_refuses_a_write),
        ("builds a dashboard", test_builds_a_dashboard),
        ("persists and shows history", test_persists_and_shows_history),
        ("saves and reopens a dashboard", test_saves_and_reopens_a_dashboard),
    )

    problems: list[str] = []
    for name, check in checks:
        print(f"[{name}]")
        try:
            found = check()
        except Exception as exc:  # noqa: BLE001
            found = [f"{name} raised in the harness: {exc}"]
        problems += found

    if problems:
        print(f"\nFAILED ({len(problems)} problems)")
        for line in problems:
            print("  " + line)
        return 1
    print(
        "\nPASSED  app boots, answers, refuses writes, builds a dashboard, "
        "and persists both chat history and saved dashboards"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
