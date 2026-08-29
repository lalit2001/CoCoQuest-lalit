# Streamlit entry point: a chat tab that answers questions in SQL, and a one-prompt dashboard builder.
# Co-authored with CoCo
"""
Conversational BI Assistant for a card-payment MSME.

Two tabs:

**Ask** - type a question in plain English. The app retrieves the relevant tables,
asks Cortex for SQL plus a chart choice, guards the SQL, runs it, picks the best
visual for the shape that came back, and summarises the answer in a sentence.

**Dashboard Builder** - one instruction produces a whole dashboard as a JSON spec.
Further instructions edit that same spec rather than rebuilding it, so the
dashboard can be reshaped conversationally.

Everything - retrieval embeddings, SQL generation, summarisation - runs inside
Snowflake via Cortex. No data leaves the account.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from app import charts
from app.dashboard_engine import (
    SPEC_KEY,
    edit_dashboard_spec,
    generate_dashboard_spec,
    render_dashboard,
    validate_spec,
)
from app.metadata_rag import retrieve_relevant_tables
from app.output_picker import resolve_output, summarize_result
from app.persistence import (
    conversation_summary,
    delete_dashboard,
    delete_history,
    list_dashboards,
    load_dashboard,
    load_recent_turns,
    new_id,
    save_dashboard,
    save_turn,
)
from app.pipeline import UnanswerableError, UnsafeQueryError, answer_question
from app.snowflake_utils import run_query, summarize_error
from app.sql_guard import check_sql

st.set_page_config(
    page_title="Conversational BI Assistant",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — dark theme
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    /* ── Animations ────────────────────────────────────────────── */
    @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes slideInLeft {
        from { opacity: 0; transform: translateX(-12px); }
        to   { opacity: 1; transform: translateX(0); }
    }

    /* ── KPI metric cards ────────────────────────────────────── */
    div[data-testid="stMetric"] {
        background: #1A1D23;
        border: 1px solid #2A2D35;
        border-radius: 12px;
        padding: 18px 20px;
        border-left: 4px solid #6C5CE7;
        animation: fadeInUp 0.4s ease-out both;
        transition: border-color 0.2s ease;
    }
    div[data-testid="stMetric"]:hover {
        border-color: #6C5CE7;
    }
    div[data-testid="stMetric"] label {
        font-size: 0.75rem !important;
        font-weight: 700;
        color: #8B8D94 !important;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-size: 2rem !important;
        font-weight: 800;
        color: #FAFAFA !important;
    }

    /* Alternate KPI accent colours */
    div[data-testid="column"]:nth-child(2) div[data-testid="stMetric"] { border-left-color: #00B894; }
    div[data-testid="column"]:nth-child(3) div[data-testid="stMetric"] { border-left-color: #0984E3; }
    div[data-testid="column"]:nth-child(4) div[data-testid="stMetric"] { border-left-color: #E17055; }
    div[data-testid="column"]:nth-child(5) div[data-testid="stMetric"] { border-left-color: #E84393; }

    /* ── Chart widget cards ───────────────────────────────────── */
    div[data-testid="stVegaLiteChart"] {
        background: #1A1D23;
        border: 1px solid #2A2D35;
        border-radius: 12px;
        padding: 10px;
        animation: fadeInUp 0.5s ease-out both;
        overflow: hidden;
        max-width: 100%;
    }

    /* ── Data tables ──────────────────────────────────────────── */
    div[data-testid="stDataFrame"] {
        border: 1px solid #2A2D35;
        border-radius: 12px;
        overflow: hidden;
        max-width: 100%;
        animation: fadeInUp 0.5s ease-out both;
    }
    div[data-testid="stDataFrame"] > div {
        max-width: 100%;
        overflow-x: auto;
    }

    /* ── Expanders ────────────────────────────────────────────── */
    div[data-testid="stExpander"] {
        border: 1px solid #2A2D35;
        border-radius: 10px;
        background: #1A1D23;
    }

    /* ── Chat messages ────────────────────────────────────────── */
    div[data-testid="stChatMessage"] {
        border-radius: 12px;
        animation: slideInLeft 0.3s ease-out both;
    }

    /* ── Primary buttons ─────────────────────────────────────── */
    button[kind="primary"] {
        background: linear-gradient(135deg, #6C5CE7 0%, #a29bfe 100%) !important;
        border: none !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 10px rgba(108, 92, 231, 0.3) !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover {
        box-shadow: 0 4px 15px rgba(108, 92, 231, 0.5) !important;
    }

    /* ── Sidebar styling ─────────────────────────────────────── */
    section[data-testid="stSidebar"] {
        border-right: 1px solid #2A2D35;
    }
    section[data-testid="stSidebar"] .stRadio label {
        font-size: 0.9rem;
    }

    /* ── Sidebar nav buttons ─────────────────────────────────── */
    .nav-btn {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 14px;
        border-radius: 8px;
        color: #8B8D94;
        text-decoration: none;
        font-size: 0.9rem;
        transition: all 0.15s ease;
        cursor: pointer;
    }
    .nav-btn:hover { background: #2A2D35; color: #FAFAFA; }
    .nav-btn.active { background: #6C5CE7; color: #FFFFFF; font-weight: 600; }

    /* ── Chat history items in sidebar ────────────────────────── */
    .chat-hist-item {
        padding: 8px 12px;
        border-radius: 8px;
        color: #8B8D94;
        font-size: 0.82rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        cursor: pointer;
        transition: background 0.15s ease;
    }
    .chat-hist-item:hover { background: #2A2D35; color: #FAFAFA; }

    /* ── Filter controls ──────────────────────────────────────── */
    div[data-testid="stSelectbox"],
    div[data-testid="stMultiSelect"],
    div[data-testid="stDateInput"] {
        animation: fadeInUp 0.4s ease-out both;
    }

    html { scroll-behavior: smooth; }
    </style>
    """,
    unsafe_allow_html=True,
)

HISTORY_KEY = "chat_history"

#: Identifies this browser session so persisted turns stay grouped as one
#: conversation. Generated once and kept for the life of the session.
SESSION_KEY = "chat_session_id"

#: Id of the saved dashboard currently loaded, so Save updates it in place
#: instead of creating a duplicate every time.
LOADED_DASHBOARD_KEY = "loaded_dashboard_id"

#: The instructions that produced the current dashboard, oldest first. Saved with
#: the spec so a reloaded dashboard keeps its provenance and stays editable.
PROMPT_LOG_KEY = "dashboard_prompt_log"

#: Shown as clickable starters on the Ask tab.
SAMPLE_QUESTIONS = [
    "What is our net revenue by provider this year?",
    "Which disputes are we most likely to lose?",
    "Show me the bank account balance trend since April",
    "How much have we paid in card processing fees each month?",
]

#: Shown as clickable starters on the Dashboard Builder tab.
SAMPLE_DASHBOARDS = [
    "Build me a dashboard on settlement reliability",
    "Build a dashboard on chargeback performance by provider",
    "Build a dashboard about card processing fees and where they go",
]


# ---------------------------------------------------------------------------
# Ask tab
# ---------------------------------------------------------------------------


def _is_simple_text_result(df: pd.DataFrame) -> bool:
    """Return True if the result is a short text/descriptive answer, not analytical data.

    These are questions like "do I have ledger", "what is ledger", "describe X" where
    the SQL returns a few rows of mostly text. The LLM summary already captures the
    meaning, so we skip rendering a chart or table.
    """
    if df is None or df.empty:
        return False
    if len(df) > 5:
        return False
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric = len(df.columns) - len(numeric_cols)
    # All text columns — definitely a text answer
    if len(numeric_cols) == 0:
        return True
    # Mostly text with 1 numeric (like a count or flag) and few rows
    if len(df) <= 2 and non_numeric >= len(numeric_cols):
        return True
    return False


def _render_text_result(df: pd.DataFrame) -> None:
    """Render a simple text result as plain markdown instead of a table."""
    if len(df) == 1 and len(df.columns) == 1:
        st.markdown(f"**{str(df.iloc[0, 0])}**")
        return
    for _, row in df.iterrows():
        parts = [f"**{_label(col)}:** {row[col]}" for col in df.columns]
        st.markdown(" | ".join(parts))


def _label(name: str) -> str:
    return str(name).replace("_", " ").strip().title()


def render_turn(turn: dict[str, Any], index: int) -> None:
    """Render a single Q&A turn as a chat bubble pair."""
    with st.chat_message("user"):
        st.markdown(turn["question"])

    with st.chat_message("assistant"):
        if turn.get("refusal"):
            st.error(turn["refusal"])
            st.caption(
                "The safety guard allows only single read-only SELECT statements, "
                "so this assistant can read your books but never change them."
            )
            return

        if turn.get("unanswerable"):
            st.info(turn["unanswerable"])
            return

        if turn.get("failure"):
            st.error(turn["failure"])
            return

        st.markdown(turn["summary"])

        frame = turn["df"]
        if frame is not None and not frame.empty:
            if _is_simple_text_result(frame):
                pass  # Summary already covers it — no chart or table needed
            else:
                charts.render(
                    frame,
                    turn["chart_type"],
                    x=turn.get("x"),
                    y=turn.get("y"),
                    key=f"chat_chart_{index}",
                )

        with st.expander("How this was answered"):
            if turn.get("tables"):
                st.caption("Tables retrieved: " + ", ".join(turn["tables"]))
            if turn.get("reason"):
                st.caption(f"Chart choice: {turn['reason']}")

            history = turn.get("history") or []
            if history:
                st.caption(
                    f"Self-corrected after {len(history)} failed "
                    f"{'attempt' if len(history) == 1 else 'attempts'}. Each error "
                    "was fed back with a diagnosis of which columns actually exist."
                )
                for number, failed in enumerate(history, start=1):
                    st.caption(f"Attempt {number} - {failed['error']}")
                    st.code(failed["sql"], language="sql")
                st.caption(
                    f"Attempt {len(history) + 1} - succeeded:"
                    if len(history)
                    else ""
                )
            st.code(turn["sql"], language="sql")


def _record(turn: dict[str, Any]) -> None:
    """Append a turn to the in-memory conversation and persist it.

    Persistence is best-effort by design: :func:`app.persistence.save_turn`
    swallows its own errors and returns None. Losing a history row is a far
    smaller problem than losing the answer the user is looking at, so a failed
    write must never surface as an error.
    """
    st.session_state[HISTORY_KEY].append(turn)

    if turn.get("refusal"):
        outcome, detail = "refused", turn["refusal"]
    elif turn.get("unanswerable"):
        outcome, detail = "unanswerable", turn["unanswerable"]
    elif turn.get("failure"):
        outcome, detail = "failed", turn["failure"]
    else:
        outcome, detail = "answered", None

    frame = turn.get("df")
    save_turn(
        st.session_state[SESSION_KEY],
        len(st.session_state[HISTORY_KEY]) - 1,
        turn["question"],
        outcome,
        summary=turn.get("summary"),
        sql_text=turn.get("sql"),
        chart_type=turn.get("chart_type"),
        x_column=turn.get("x"),
        y_column=turn.get("y"),
        tables_used=turn.get("tables"),
        attempts=turn.get("attempts"),
        row_count=None if frame is None else len(frame),
        detail=detail,
    )


def handle_question(question: str) -> None:
    """Run one question through the pipeline and append the turn to history."""
    turn: dict[str, Any] = {"question": question}

    # Show the user's message immediately, with a thinking indicator
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.status("Thinking...", expanded=False) as status:
            try:
                status.update(label="Finding the right tables and writing the query...")
                result = answer_question(question)
            except UnsafeQueryError as exc:
                turn["refusal"] = str(exc)
                _record(turn)
                status.update(label="Done", state="error")
                return
            except UnanswerableError as exc:
                turn["unanswerable"] = str(exc)
                _record(turn)
                status.update(label="Done", state="error")
                return
            except Exception as exc:  # noqa: BLE001
                turn["failure"] = f"I could not answer that. {exc}"
                _record(turn)
                status.update(label="Done", state="error")
                return

            plan, frame = result["plan"], result["df"]

            if frame.empty:
                turn.update(
                    summary="That query returned no matching data.",
                    df=frame,
                    chart_type="table",
                    sql=plan["sql"],
                    tables=result["tables"],
                    repaired=plan.get("repaired", False),
                    history=result.get("history", []),
                    attempts=result.get("attempts", 1),
                )
                _record(turn)
                status.update(label="Done", state="complete")
                return

            output = resolve_output(frame, plan)
            status.update(label="Summarising the answer...")
            summary = summarize_result(question, frame)

            turn.update(
                summary=summary,
                df=frame,
                chart_type=output["chart_type"],
                x=output["x"],
                y=output["y"],
                reason=plan.get("reason"),
                chart_source=output["source"],
                sql=plan["sql"],
                tables=result["tables"],
                repaired=plan.get("repaired", False),
                history=result.get("history", []),
                attempts=result.get("attempts", 1),
            )
            _record(turn)
            status.update(label="Done", state="complete")



def ask_tab() -> None:
    """Render the conversational question-answering tab."""
    history: list[dict[str, Any]] = st.session_state.setdefault(HISTORY_KEY, [])
    pending: str | None = None

    if not history:
        st.markdown("**Try one of these:**")
        for position, sample in enumerate(SAMPLE_QUESTIONS):
            if st.button(sample, key=f"sample_q_{position}", use_container_width=True):
                pending = sample
    else:
        if st.button("Clear conversation"):
            st.session_state[HISTORY_KEY] = []
            st.rerun()

    for index, turn in enumerate(history):
        render_turn(turn, index)

    typed = st.chat_input("e.g. What is our net revenue by provider this year?")
    question = typed or pending
    if question:
        handle_question(question)
        st.rerun()


# ---------------------------------------------------------------------------
# Dashboard Builder tab
# ---------------------------------------------------------------------------


def build_or_edit(instruction: str) -> None:
    """Create the dashboard if there is none, otherwise edit the existing spec."""
    existing = st.session_state.get(SPEC_KEY)

    try:
        tables = retrieve_relevant_tables(instruction, k=3)
        if existing:
            with st.spinner("Reshaping the dashboard..."):
                spec = edit_dashboard_spec(existing, instruction, tables)
        else:
            with st.spinner("Designing the dashboard..."):
                spec = generate_dashboard_spec(instruction, tables)
    except Exception as exc:  # noqa: BLE001 - keep the last good dashboard on screen
        st.error(f"Could not {'update' if existing else 'build'} the dashboard: {exc}")
        if existing:
            st.caption("Your previous dashboard is unchanged and still shown below.")
        return

    normalised, errors = validate_spec(spec)
    if errors:
        st.error("The generated dashboard was not valid: " + "; ".join(errors[:3]))
        if existing:
            st.caption("Your previous dashboard is unchanged and still shown below.")
        return

    st.session_state[SPEC_KEY] = normalised
    st.session_state.setdefault(PROMPT_LOG_KEY, []).append(instruction)


def dashboard_tab() -> None:
    """Render the one-prompt dashboard builder tab."""
    spec = st.session_state.get(SPEC_KEY)

    # ── ACTIVE DASHBOARD: show it first, prompt in expander ──────────────
    if spec:
        # Back button to return to gallery
        back_col, title_col = st.columns([1, 5])
        with back_col:
            if st.button("Back to dashboards", icon=":material/arrow_back:",
                          type="tertiary"):
                for key in (SPEC_KEY, "dashboard_filters", PROMPT_LOG_KEY,
                            LOADED_DASHBOARD_KEY):
                    st.session_state.pop(key, None)
                # Clear any magic switch states for dashboard widgets
                for k in list(st.session_state.keys()):
                    if k.startswith("_magic_switch_") or k.startswith("_magic_result_"):
                        del st.session_state[k]
                st.rerun()

        render_dashboard(spec)

        # Compact edit prompt — the dashboard is the star, not the form.
        with st.expander("Edit this dashboard by prompt", icon=":material/edit:"):
            instruction = st.text_input(
                "What to change",
                placeholder="e.g. split by provider, add a KPI for pending payouts, make it monthly",
                key="dashboard_instruction_edit",
                label_visibility="collapsed",
            )
            edit_cols = st.columns([1, 1, 4])
            with edit_cols[0]:
                if st.button("Update", type="primary", use_container_width=True) and instruction.strip():
                    build_or_edit(instruction.strip())
                    st.rerun()
            with edit_cols[1]:
                if st.button("Reset", use_container_width=True):
                    for key in (SPEC_KEY, "dashboard_filters", PROMPT_LOG_KEY,
                                LOADED_DASHBOARD_KEY):
                        st.session_state.pop(key, None)
                    st.rerun()

        # Widget rearrangement
        with st.expander("Rearrange widgets", icon=":material/drag_indicator:"):
            st.caption("Move widgets up or down. Changes apply immediately.")
            widgets = spec.get("widgets", [])
            changed = False
            for idx, widget in enumerate(widgets):
                type_icons = {
                    "kpi": ":material/speed:", "bar": ":material/bar_chart:",
                    "line": ":material/show_chart:", "scatter": ":material/scatter_plot:",
                    "pie": ":material/pie_chart:", "table": ":material/table_chart:",
                }
                icon = type_icons.get(widget["type"], ":material/widgets:")
                cols = st.columns([0.5, 4, 1, 1])
                cols[0].markdown(f"**{idx + 1}**")
                cols[1].markdown(
                    f"{icon} **{widget['title']}** "
                    f"<span style='color:#888; font-size:0.8em'>({widget['type']})</span>",
                    unsafe_allow_html=True,
                )
                with cols[2]:
                    if idx > 0 and st.button(
                        ":material/arrow_upward:",
                        key=f"move_up_{widget['id']}",
                        use_container_width=True,
                    ):
                        widgets[idx - 1], widgets[idx] = widgets[idx], widgets[idx - 1]
                        changed = True
                with cols[3]:
                    if idx < len(widgets) - 1 and st.button(
                        ":material/arrow_downward:",
                        key=f"move_down_{widget['id']}",
                        use_container_width=True,
                    ):
                        widgets[idx], widgets[idx + 1] = widgets[idx + 1], widgets[idx]
                        changed = True
            if changed:
                spec["widgets"] = widgets
                st.session_state[SPEC_KEY] = spec
                st.rerun()

        # Save controls
        st.divider()
        save_cols = st.columns([1, 1, 4])
        with save_cols[0]:
            loaded_id = st.session_state.get(LOADED_DASHBOARD_KEY)
            label = "Save changes" if loaded_id else "Save dashboard"
            if st.button(label, icon=":material/save:", type="primary", use_container_width=True):
                new_ident = save_dashboard(
                    spec,
                    st.session_state.get(PROMPT_LOG_KEY, []),
                    dashboard_id=loaded_id,
                )
                if new_ident:
                    st.session_state[LOADED_DASHBOARD_KEY] = new_ident
                    st.toast("Dashboard saved.", icon=":material/check:")
                else:
                    st.error("Could not save the dashboard.")
        with save_cols[1]:
            if st.button("Save as new", icon=":material/content_copy:", use_container_width=True):
                new_ident = save_dashboard(
                    spec,
                    st.session_state.get(PROMPT_LOG_KEY, []),
                )
                if new_ident:
                    st.session_state[LOADED_DASHBOARD_KEY] = new_ident
                    st.toast("Saved as a new dashboard.", icon=":material/check:")

        with st.expander("Dashboard spec (JSON)"):
            st.code(json.dumps(spec, indent=2), language="json")
            log = st.session_state.get(PROMPT_LOG_KEY, [])
            if log:
                st.caption("Prompts: " + " → ".join(log))
        return

    # ── NO DASHBOARD: show gallery + build form ─────────────────────────
    _render_saved_dashboards_gallery()
    st.divider()

    st.markdown("**Create a new dashboard**")
    st.caption(
        "Describe what you want in one sentence. The LLM builds a full dashboard "
        "spec — then you can keep talking to reshape it."
    )

    instruction = st.text_area(
        "Describe your dashboard",
        placeholder="e.g. Build me a dashboard on settlement reliability",
        height=80,
        key="dashboard_instruction_new",
    )

    left, _ = st.columns([1, 5])
    with left:
        submitted = st.button("Build", type="primary", use_container_width=True)

    pending: str | None = None
    st.markdown("**Or start from one of these:**")
    for position, sample in enumerate(SAMPLE_DASHBOARDS):
        if st.button(sample, key=f"sample_d_{position}", use_container_width=True):
            pending = sample

    if (submitted and instruction.strip()) or pending:
        build_or_edit(pending or instruction.strip())
        st.rerun()


# ---------------------------------------------------------------------------
# Saved dashboards gallery — card-based layout
# ---------------------------------------------------------------------------

#: Card accent colors, cycled across saved dashboards.
_CARD_COLORS = ["#6C5CE7", "#00B894", "#0984E3", "#E17055", "#E84393", "#00CEC9"]

#: Type icons for the widget summary on each card.
_TYPE_ICONS = {
    "kpi": ":material/speed:", "bar": ":material/bar_chart:",
    "line": ":material/show_chart:", "scatter": ":material/scatter_plot:",
    "pie": ":material/pie_chart:", "table": ":material/table_chart:",
}


def _render_saved_dashboards_gallery() -> None:
    """Show saved dashboards as a grid of styled cards with Open / Delete."""
    saved = list_dashboards(limit=25)

    st.markdown(
        '<p style="font-size:1.1rem; font-weight:700; color:#2D3436; '
        'margin-bottom:4px;">Your Dashboards</p>',
        unsafe_allow_html=True,
    )

    if saved.empty:
        st.info(
            "No saved dashboards yet. Build one below and press Save — "
            "it will appear here as a card you can reopen anytime."
        )
        return

    per_row = 3
    rows_data = [saved.iloc[i : i + per_row] for i in range(0, len(saved), per_row)]

    for row_chunk in rows_data:
        cols = st.columns(per_row)
        for col_idx, (_, row) in enumerate(row_chunk.iterrows()):
            color = _CARD_COLORS[col_idx % len(_CARD_COLORS)]
            with cols[col_idx]:
                # Card HTML
                updated = row["UPDATED_AT"]
                time_str = f"{updated:%d %b %Y, %H:%M}" if hasattr(updated, "strftime") else str(updated)[:16]
                widgets_n = int(row["WIDGET_COUNT"]) if row["WIDGET_COUNT"] else 0

                st.markdown(
                    f'<div style="'
                    f"background: #1A1D23; "
                    f"border-radius: 12px; "
                    f"border-top: 4px solid {color}; "
                    f"border: 1px solid #2A2D35; "
                    f"border-top: 4px solid {color}; "
                    f"padding: 18px; "
                    f"margin-bottom: 10px; "
                    f'">'
                    f'<p style="font-weight:700; font-size:1rem; color:#FAFAFA; '
                    f'margin:0 0 6px 0;">{row["NAME"]}</p>'
                    f'<p style="font-size:0.82rem; color:#8B8D94; margin:0 0 4px 0;">'
                    f'{widgets_n} widgets</p>'
                    f'<p style="font-size:0.75rem; color:#555; margin:0;">'
                    f'Updated {time_str}</p>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

                btn_left, btn_right = st.columns(2)
                with btn_left:
                    if st.button(
                        "Open",
                        key=f"open_card_{row['DASHBOARD_ID']}",
                        icon=":material/folder_open:",
                        use_container_width=True,
                    ):
                        loaded = load_dashboard(row["DASHBOARD_ID"])
                        if loaded is None:
                            st.error("Could not load.")
                        else:
                            restored_spec, restored_log = loaded
                            st.session_state[SPEC_KEY] = restored_spec
                            st.session_state[PROMPT_LOG_KEY] = restored_log
                            st.session_state[LOADED_DASHBOARD_KEY] = row["DASHBOARD_ID"]
                            st.session_state.pop("dashboard_filters", None)
                            st.rerun()
                with btn_right:
                    if st.button(
                        "Delete",
                        key=f"del_card_{row['DASHBOARD_ID']}",
                        icon=":material/delete:",
                        use_container_width=True,
                    ):
                        delete_dashboard(row["DASHBOARD_ID"])
                        if st.session_state.get(LOADED_DASHBOARD_KEY) == row["DASHBOARD_ID"]:
                            st.session_state.pop(LOADED_DASHBOARD_KEY, None)
                        st.rerun()


# ---------------------------------------------------------------------------
# History tab
# ---------------------------------------------------------------------------


def history_tab() -> None:
    """Show persisted conversations, and let a past answer be re-run."""
    st.caption(
        "Every question is written to FINBI_DEMO.CORE.CHAT_MESSAGES, so this "
        "survives a page reload. Only the SQL is stored, never the result set - "
        "re-running an answer therefore shows current figures, not a stale copy."
    )

    conversations = conversation_summary(limit=25)
    if conversations.empty:
        st.info("No history yet. Ask something in the Ask tab.")
        return

    labels = {"All conversations": None}
    for _, row in conversations.iterrows():
        marker = " (current)" if row["SESSION_ID"] == st.session_state[SESSION_KEY] else ""
        labels[
            f"{row['STARTED_AT']:%d %b %H:%M}{marker} - {row['TURNS']} turns - "
            f"{str(row['FIRST_QUESTION'])[:60]}"
        ] = row["SESSION_ID"]

    picked = st.selectbox("Conversation", list(labels))
    turns = load_recent_turns(limit=200, session_id=labels[picked])

    if turns.empty:
        st.info("Nothing recorded for that conversation.")
        return

    metric_cols = st.columns(4)
    metric_cols[0].metric("Questions", len(turns))
    metric_cols[1].metric("Answered", int((turns["OUTCOME"] == "answered").sum()))
    metric_cols[2].metric("Refused", int((turns["OUTCOME"] == "refused").sum()))
    retried = int((turns["ATTEMPTS"].fillna(1) > 1).sum())
    metric_cols[3].metric("Self-corrected", retried)

    st.divider()

    icons = {
        "answered": ":material/check_circle:",
        "refused": ":material/shield:",
        "unanswerable": ":material/help:",
        "failed": ":material/error:",
    }

    for _, row in turns.iterrows():
        icon = icons.get(row["OUTCOME"], ":material/help:")
        header = f"{row['ASKED_AT']:%d %b %H:%M}  ·  {row['QUESTION']}"
        with st.expander(header, icon=icon):
            if row["SUMMARY"]:
                st.markdown(row["SUMMARY"])
            if row["DETAIL"]:
                st.warning(row["DETAIL"])

            facts = []
            if row["TABLES_USED"]:
                facts.append(f"tables: {row['TABLES_USED']}")
            if row["CHART_TYPE"]:
                facts.append(f"chart: {row['CHART_TYPE']}")
            if row["ROW_COUNT"] is not None and not pd.isna(row["ROW_COUNT"]):
                facts.append(f"{int(row['ROW_COUNT'])} rows")
            if row["ATTEMPTS"] is not None and not pd.isna(row["ATTEMPTS"]):
                attempts = int(row["ATTEMPTS"])
                facts.append(
                    "first try" if attempts == 1 else f"self-corrected over {attempts} tries"
                )
            if facts:
                st.caption("  ·  ".join(facts))

            if row["SQL_TEXT"]:
                st.code(row["SQL_TEXT"], language="sql")
                if st.button(
                    "Run this again",
                    key=f"rerun_{row['MESSAGE_ID']}",
                    icon=":material/refresh:",
                ):
                    _rerun_stored_query(row)

    st.divider()
    if st.button("Delete all my history", icon=":material/delete_forever:"):
        if delete_history():
            st.session_state[HISTORY_KEY] = []
            st.rerun()
        else:
            st.error("Could not delete the history.")


def _rerun_stored_query(row: Any) -> None:
    """Re-execute a stored query and draw its chart.

    The stored SQL goes back through :func:`app.sql_guard.check_sql` first. It was
    checked before it was ever run, but re-validating means a row edited in the
    table directly still cannot become an execution path.
    """
    sql = row["SQL_TEXT"]
    ok, reason = check_sql(sql)
    if not ok:
        st.error(reason)
        return

    try:
        with st.spinner("Re-running against current data..."):
            frame = run_query(sql)
    except Exception as exc:  # noqa: BLE001
        st.error(f"That query no longer runs: {summarize_error(exc)}")
        return

    if frame.empty:
        st.info("It runs, but returns no rows now.")
        return

    charts.render(
        frame,
        row["CHART_TYPE"] or "table",
        x=row["X_COLUMN"],
        y=row["Y_COLUMN"],
        key=f"hist_chart_{row['MESSAGE_ID']}",
    )


# ---------------------------------------------------------------------------
# Shell — sidebar navigation + page routing
# ---------------------------------------------------------------------------

NAV_KEY = "nav_page"

st.session_state.setdefault(SESSION_KEY, new_id())
st.session_state.setdefault(HISTORY_KEY, [])
st.session_state.setdefault(NAV_KEY, "Ask")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### FinBI Agent")
    st.caption("Your AI-powered financial analyst")

    if st.button("+ New chat", use_container_width=True, type="primary"):
        st.session_state[HISTORY_KEY] = []
        st.session_state[SESSION_KEY] = new_id()
        st.session_state[NAV_KEY] = "Ask"
        for k in list(st.session_state.keys()):
            if k.startswith(("_magic_switch_", "_magic_result_")):
                del st.session_state[k]
        st.rerun()

    st.divider()

    nav_items = {
        "Dashboards": ":material/dashboard:",
        "History": ":material/history:",
    }

    for label, icon in nav_items.items():
        btn_type = "primary" if st.session_state[NAV_KEY] == label else "tertiary"
        if st.button(
            label, key=f"nav_{label}", icon=icon,
            use_container_width=True, type=btn_type,
        ):
            st.session_state[NAV_KEY] = label
            st.rerun()

    st.divider()

    # Recent conversations from Snowflake persistence
    st.caption("Recent conversations")
    past_sessions = conversation_summary(limit=15)
    if not past_sessions.empty:
        for _, row in past_sessions.iterrows():
            q = row.get("FIRST_QUESTION", "")
            if not q:
                continue
            truncated = q[:38] + "..." if len(q) > 38 else q
            turns_n = int(row.get("TURNS", 0))
            sess_id = row.get("SESSION_ID", "")

            if st.button(
                truncated,
                key=f"sidebar_sess_{sess_id}",
                use_container_width=True,
                type="tertiary",
                help=f"{turns_n} turns",
            ):
                # Load this conversation into the current session
                loaded = load_recent_turns(limit=50, session_id=sess_id)
                if not loaded.empty:
                    rebuilt = []
                    for _, t in loaded.sort_values("TURN_INDEX").iterrows():
                        turn_data: dict[str, Any] = {
                            "question": t["QUESTION"],
                            "summary": t.get("SUMMARY"),
                            "sql": t.get("SQL_TEXT"),
                            "chart_type": t.get("CHART_TYPE") or "table",
                            "x": t.get("X_COLUMN"),
                            "y": t.get("Y_COLUMN"),
                            "df": None,
                        }
                        outcome = t.get("OUTCOME", "")
                        if outcome == "refused":
                            turn_data["refusal"] = t.get("DETAIL", "Refused")
                        elif outcome == "unanswerable":
                            turn_data["unanswerable"] = t.get("DETAIL", "Cannot answer")
                        elif outcome == "failed":
                            turn_data["failure"] = t.get("DETAIL", "Failed")
                        rebuilt.append(turn_data)
                    st.session_state[HISTORY_KEY] = rebuilt
                    st.session_state[SESSION_KEY] = sess_id
                    st.session_state[NAV_KEY] = "Ask"
                    st.rerun()
    else:
        st.caption("No conversations yet")

    st.divider()
    st.caption("Conversations are persisted to Snowflake.")


# ── Main content area ────────────────────────────────────────────────────────
page = st.session_state[NAV_KEY]

if page == "Ask":
    ask_tab()
elif page == "Dashboards":
    st.markdown("#### Dashboards")
    dashboard_tab()
elif page == "History":
    st.markdown("#### History")
    history_tab()
