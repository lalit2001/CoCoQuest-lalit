# Production-quality Altair chart builders with vibrant styling, donut/area support, and native fallbacks.
# Co-authored with CoCo
"""
Every visual in the app is built here.

:func:`render` is the single dispatch point: it takes a DataFrame, a chart type
and the axis columns, and draws the result into the current Streamlit container.
Both the chat tab and the dashboard renderer go through it.
"""

from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Palette & Theme
# ---------------------------------------------------------------------------

PALETTE = [
    "#6C5CE7", "#00B894", "#0984E3", "#E17055", "#E84393",
    "#00CEC9", "#F39C12", "#FDCB6E", "#2D3436",
]

PIE_PALETTE = [
    "#6C5CE7", "#00B894", "#0984E3", "#E17055", "#E84393",
    "#00CEC9", "#F39C12", "#A29BFE", "#55EFC4", "#74B9FF",
]

CHART_HEIGHT = 360


def _label(name: Any) -> str:
    return str(name).replace("_", " ").strip().title()


def _fmt_axis(col: str) -> alt.Axis:
    return alt.Axis(
        labelColor="#636E72", titleColor="#636E72",
        labelFontSize=11, titleFontSize=12,
        gridColor="#F0F0F5", domainColor="#DFE6E9",
    )


# ---------------------------------------------------------------------------
# Individual chart builders — all return an alt.Chart
# ---------------------------------------------------------------------------

def bar_chart(df: pd.DataFrame, x: str, y: str, title: str | None = None) -> alt.Chart:
    data = df.sort_values(by=y, ascending=False).head(20)
    n = len(data)
    color_scale = alt.Scale(range=PALETTE[:max(n, 1)])

    chart = (
        alt.Chart(data, title=title or "")
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
        .encode(
            x=alt.X(f"{x}:N", title=_label(x), axis=_fmt_axis(x), sort="-y"),
            y=alt.Y(f"{y}:Q", title=_label(y), axis=_fmt_axis(y)),
            color=alt.Color(f"{x}:N", scale=color_scale, legend=None),
            tooltip=[
                alt.Tooltip(f"{x}:N", title=_label(x)),
                alt.Tooltip(f"{y}:Q", title=_label(y), format=",.2f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return chart


def hbar_chart(df: pd.DataFrame, x: str, y: str, title: str | None = None) -> alt.Chart:
    data = df.sort_values(by=y, ascending=True).tail(15)
    n = len(data)
    color_scale = alt.Scale(range=PALETTE[:max(n, 1)])

    chart = (
        alt.Chart(data, title=title or "")
        .mark_bar(cornerRadiusTopRight=6, cornerRadiusBottomRight=6)
        .encode(
            y=alt.Y(f"{x}:N", title=_label(x), axis=_fmt_axis(x), sort="-x"),
            x=alt.X(f"{y}:Q", title=_label(y), axis=_fmt_axis(y)),
            color=alt.Color(f"{x}:N", scale=color_scale, legend=None),
            tooltip=[
                alt.Tooltip(f"{x}:N", title=_label(x)),
                alt.Tooltip(f"{y}:Q", title=_label(y), format=",.2f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return chart


def line_chart(df: pd.DataFrame, x: str, y: str, title: str | None = None) -> alt.Chart:
    data = df.sort_values(by=x)

    line = (
        alt.Chart(data, title=title or "")
        .mark_line(
            color=PALETTE[0], strokeWidth=3,
            interpolate="monotone",
        )
        .encode(
            x=alt.X(f"{x}:T" if _is_temporal(data, x) else f"{x}:O",
                     title=_label(x), axis=_fmt_axis(x)),
            y=alt.Y(f"{y}:Q", title=_label(y), axis=_fmt_axis(y)),
            tooltip=[
                alt.Tooltip(f"{x}", title=_label(x)),
                alt.Tooltip(f"{y}:Q", title=_label(y), format=",.2f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )

    points = line.mark_point(
        color=PALETTE[0], filled=True, size=50,
        stroke="#FFFFFF", strokeWidth=2,
    ) if len(data) <= 30 else alt.Chart(data).mark_point(opacity=0)

    area = (
        alt.Chart(data)
        .mark_area(
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="rgba(108,92,231,0.15)", offset=0),
                    alt.GradientStop(color="rgba(108,92,231,0.01)", offset=1),
                ],
                x1=0, x2=0, y1=0, y2=1,
            ),
            interpolate="monotone",
        )
        .encode(
            x=alt.X(f"{x}:T" if _is_temporal(data, x) else f"{x}:O"),
            y=alt.Y(f"{y}:Q"),
        )
    )

    return alt.layer(area, line, points).properties(height=CHART_HEIGHT)


def area_chart(df: pd.DataFrame, x: str, y: str, title: str | None = None) -> alt.Chart:
    data = df.sort_values(by=x)
    x_type = f"{x}:T" if _is_temporal(data, x) else f"{x}:O"

    area = (
        alt.Chart(data, title=title or "")
        .mark_area(
            color=alt.Gradient(
                gradient="linear",
                stops=[
                    alt.GradientStop(color="rgba(9,132,227,0.25)", offset=0),
                    alt.GradientStop(color="rgba(9,132,227,0.02)", offset=1),
                ],
                x1=0, x2=0, y1=0, y2=1,
            ),
            line={"color": PALETTE[2], "strokeWidth": 2.5},
            interpolate="monotone",
        )
        .encode(
            x=alt.X(x_type, title=_label(x), axis=_fmt_axis(x)),
            y=alt.Y(f"{y}:Q", title=_label(y), axis=_fmt_axis(y)),
            tooltip=[
                alt.Tooltip(f"{x}", title=_label(x)),
                alt.Tooltip(f"{y}:Q", title=_label(y), format=",.2f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return area


def scatter_chart(df: pd.DataFrame, x: str, y: str, title: str | None = None) -> alt.Chart:
    chart = (
        alt.Chart(df, title=title or "")
        .mark_circle(
            size=80, color=PALETTE[0], opacity=0.7,
            stroke="#FFFFFF", strokeWidth=1.5,
        )
        .encode(
            x=alt.X(f"{x}:Q", title=_label(x), axis=_fmt_axis(x)),
            y=alt.Y(f"{y}:Q", title=_label(y), axis=_fmt_axis(y)),
            tooltip=[
                alt.Tooltip(f"{x}:Q", title=_label(x), format=",.2f"),
                alt.Tooltip(f"{y}:Q", title=_label(y), format=",.2f"),
            ],
        )
        .properties(height=CHART_HEIGHT)
    )
    return chart


def pie_chart(df: pd.DataFrame, labels: str, values: str, title: str | None = None) -> alt.Chart:
    data = df.head(10).copy()
    total = data[values].sum()
    color_scale = alt.Scale(domain=data[labels].tolist(), range=PIE_PALETTE[:len(data)])

    base = alt.Chart(data, title=title or "").encode(
        theta=alt.Theta(f"{values}:Q", stack=True),
        color=alt.Color(f"{labels}:N", scale=color_scale,
                        legend=alt.Legend(orient="bottom", columns=3, labelFontSize=11)),
        tooltip=[
            alt.Tooltip(f"{labels}:N", title=_label(labels)),
            alt.Tooltip(f"{values}:Q", title=_label(values), format=",.2f"),
        ],
    )

    donut = base.mark_arc(innerRadius=65, outerRadius=130, stroke="#fff", strokeWidth=2.5)

    center_text = (
        alt.Chart(pd.DataFrame({"text": [_format_metric(total)]}))
        .mark_text(fontSize=20, fontWeight="bold", color="#2D3436")
        .encode(text="text:N")
    )

    return alt.layer(donut, center_text).properties(height=CHART_HEIGHT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_temporal(df: pd.DataFrame, col: str) -> bool:
    if col not in df.columns:
        return False
    return pd.api.types.is_datetime64_any_dtype(df[col])


def resolve_column(df: pd.DataFrame, name: str | None) -> str | None:
    if df is None or name is None or not len(df.columns):
        return None
    target = str(name).strip().upper()
    for column in df.columns:
        if str(column).upper() == target:
            return column
    return None


# ---------------------------------------------------------------------------
# Build figure
# ---------------------------------------------------------------------------

_BUILDERS: dict[str, Any] = {
    "bar": bar_chart,
    "hbar": hbar_chart,
    "line": line_chart,
    "area": area_chart,
    "scatter": scatter_chart,
    "pie": pie_chart,
    "donut": pie_chart,
}


def build_figure(
    df: pd.DataFrame,
    chart_type: str,
    x: str | None,
    y: str | None,
    title: str | None = None,
) -> alt.Chart | None:
    if df is None or df.empty or chart_type in ("kpi", "table"):
        return None

    x_column = resolve_column(df, x)
    y_column = resolve_column(df, y)
    if x_column is None or y_column is None:
        return None

    builder = _BUILDERS.get(chart_type)
    return builder(df, x_column, y_column, title) if builder else None


# ---------------------------------------------------------------------------
# KPI & Table
# ---------------------------------------------------------------------------

def _format_metric(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)) and not pd.isna(value):
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:,.1f}M"
        if abs(value) >= 1_000:
            return f"{value / 1_000:,.1f}K"
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return "-" if value is None or pd.isna(value) else str(value)


def render_kpi(df: pd.DataFrame, title: str | None = None, color_index: int = 0) -> None:
    if df is None or df.empty:
        st.info("No data.")
        return
    row = df.iloc[0]
    columns = list(df.columns)
    for col_idx, (column, slot) in enumerate(zip(columns, st.columns(len(columns)))):
        with slot:
            st.metric(label=_label(column), value=_format_metric(row[column]))


def render_table(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        st.info("No data.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True, height=300)


# ---------------------------------------------------------------------------
# Magic action popover — single icon at top-right, all actions inside
# ---------------------------------------------------------------------------

_SWITCHABLE = ["bar", "line", "area", "donut", "scatter", "table"]

_SUMMARISE_PROMPT = (
    "You are a data analyst. Summarise the following data in 2-3 concise bullet "
    "points for a small business owner. Focus on the top insight, any notable "
    "outlier, and the overall trend. Data:\n\n{data}"
)


def _data_preview(df: pd.DataFrame, max_rows: int = 30) -> str:
    return df.head(max_rows).to_string(index=False, max_colwidth=40)


def _magic_popover(
    df: pd.DataFrame,
    chart_type: str,
    x: str | None,
    y: str | None,
    title: str | None,
    key: str | None,
) -> None:
    """Render a single magic icon that opens a popover with AI actions.

    Summarise and Ask results are displayed inside the popover itself.
    """
    from app.snowflake_utils import DEFAULT_MODEL, cortex_complete

    uid = key or f"magic_{id(df)}"
    result_key = f"_magic_result_{uid}"

    with st.popover(":material/auto_awesome:", help="AI actions"):
        # --- Show any previous result at the top of the popover ---
        stored = st.session_state.get(result_key)
        if stored and stored[0] == "summary":
            st.info(stored[1], icon=":material/auto_awesome:")
            if st.button("Clear", key=f"clr_{uid}", type="tertiary"):
                st.session_state[result_key] = None
                st.rerun()
            st.divider()
        elif stored and stored[0] == "answer":
            st.success(stored[1], icon=":material/chat:")
            if st.button("Clear", key=f"clr_{uid}", type="tertiary"):
                st.session_state[result_key] = None
                st.rerun()
            st.divider()

        st.caption(f"Current view: **{chart_type.title()}**")

        if st.button("Summarise data", key=f"sum_{uid}",
                      icon=":material/summarize:", use_container_width=True):
            with st.spinner("Summarising..."):
                preview = _data_preview(df)
                prompt = _SUMMARISE_PROMPT.format(data=preview)
                summary = cortex_complete(prompt, DEFAULT_MODEL)
                st.session_state[result_key] = ("summary", summary)
                st.rerun()

        st.markdown("**Switch chart type**")
        other_types = [t for t in _SWITCHABLE if t != chart_type]
        type_cols = st.columns(3)
        for i, ctype in enumerate(other_types):
            with type_cols[i % 3]:
                if st.button(ctype.title(), key=f"sw_{uid}_{ctype}",
                              use_container_width=True):
                    st.session_state[result_key] = ("switch", ctype)

        st.divider()

        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV", csv_data, file_name="data.csv", mime="text/csv",
            key=f"dl_{uid}", icon=":material/download:", use_container_width=True,
        )

        st.divider()

        st.markdown("**Ask about this data**")
        custom = st.text_area(
            "Question", key=f"ask_{uid}",
            placeholder="e.g. What's the top performer? Why did revenue drop?",
            label_visibility="collapsed", height=80,
        )
        if st.button("Get answer", key=f"go_{uid}", type="primary",
                      icon=":material/chat:", use_container_width=True):
            if custom and custom.strip():
                with st.spinner("Thinking..."):
                    preview = _data_preview(df)
                    prompt = (
                        f"You are a data analyst. The user is looking at a "
                        f"{chart_type} chart titled '{title or 'Untitled'}' "
                        f"with this data:\n\n{preview}\n\n"
                        f"User question: {custom.strip()}\n\n"
                        f"Answer in 2-4 concise sentences for a business owner."
                    )
                    answer = cortex_complete(prompt, DEFAULT_MODEL)
                    st.session_state[result_key] = ("answer", answer)
                    st.rerun()


def _show_magic_result(key: str | None) -> None:
    """Display summary/answer results in a collapsible expander, called by render()."""
    uid = key or ""
    result_key = f"_magic_result_{uid}"
    stored = st.session_state.get(result_key)
    if not stored:
        return
    kind, value = stored
    if kind == "summary":
        with st.expander("AI Summary", expanded=True, icon=":material/auto_awesome:"):
            st.markdown(value)
            if st.button("Dismiss", key=f"dismiss_{uid}", type="tertiary"):
                st.session_state[result_key] = None
                st.rerun()
    elif kind == "answer":
        with st.expander("AI Answer", expanded=True, icon=":material/chat:"):
            st.markdown(value)
            if st.button("Dismiss", key=f"dismiss_{uid}", type="tertiary"):
                st.session_state[result_key] = None
                st.rerun()


def _render_visual(
    df: pd.DataFrame,
    chart_type: str,
    x: str | None,
    y: str | None,
    title: str | None,
    key: str | None,
) -> None:
    """Draw just the visual (no toolbar), used for chart-type switching."""
    if chart_type == "kpi":
        render_kpi(df, title)
        return
    if chart_type == "table":
        render_table(df)
        return
    figure = build_figure(df, chart_type, x, y, title)
    if figure is not None:
        switch_key = f"{key}_sw" if key else None
        st.altair_chart(figure, use_container_width=True, key=switch_key)
        return
    _render_native(df, chart_type, x, y)


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def _infer_axes(df: pd.DataFrame, chart_type: str) -> tuple[str | None, str | None]:
    """Auto-detect x (categorical/temporal) and y (numeric) columns for chart switching."""
    if df is None or df.empty:
        return None, None

    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric = [c for c in df.columns if c not in numeric]
    temporal = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]

    if chart_type in ("line", "area") and temporal and numeric:
        return temporal[0], numeric[0]
    if chart_type in ("bar", "hbar", "pie", "donut") and non_numeric and numeric:
        return non_numeric[0], numeric[0]
    if chart_type == "scatter" and len(numeric) >= 2:
        return numeric[0], numeric[1]
    if non_numeric and numeric:
        return non_numeric[0], numeric[0]
    return None, None


def render(
    df: pd.DataFrame,
    chart_type: str,
    x: str | None = None,
    y: str | None = None,
    title: str | None = None,
    key: str | None = None,
) -> None:
    if df is None or df.empty:
        st.info("This query returned no rows.")
        return

    uid = key or f"magic_{id(df)}"
    result_key = f"_magic_result_{uid}"
    switch_key = f"_magic_switch_{uid}"

    # Persist the switched type so it survives reruns until changed again.
    active_type = st.session_state.get(switch_key, chart_type)
    active_x, active_y = x, y

    # Check if a NEW switch was just requested (from popover button click).
    stored = st.session_state.get(result_key)
    if stored and stored[0] == "switch":
        active_type = stored[1]
        st.session_state[switch_key] = active_type
        st.session_state[result_key] = None

    # If active axes are missing (e.g. original was table/kpi), auto-detect.
    if active_type not in ("kpi", "table") and (not active_x or not active_y):
        active_x, active_y = _infer_axes(df, active_type)

    # Magic icon at top-right, chart below
    _, icon_col = st.columns([9, 1])
    with icon_col:
        _magic_popover(df, active_type, active_x, active_y, title, key)

    # Re-read in case the popover just set a switch on this same run.
    stored = st.session_state.get(result_key)
    if stored and stored[0] == "switch":
        active_type = stored[1]
        st.session_state[switch_key] = active_type
        st.session_state[result_key] = None
        if not active_x or not active_y:
            active_x, active_y = _infer_axes(df, active_type)

    if active_type == "kpi":
        render_kpi(df, title)
    elif active_type == "table":
        render_table(df)
    else:
        figure = build_figure(df, active_type, active_x, active_y, title)
        if figure is not None:
            st.altair_chart(figure, use_container_width=True, key=key)
        else:
            _render_native(df, active_type, active_x, active_y)


def _render_native(df: pd.DataFrame, chart_type: str, x: str | None, y: str | None) -> None:
    x_col = resolve_column(df, x)
    y_col = resolve_column(df, y)
    if not x_col or not y_col:
        render_table(df)
        return

    accent = PALETTE[0]
    if chart_type == "line":
        st.line_chart(df.sort_values(by=x_col), x=x_col, y=y_col,
                      color=accent, height=360)
    elif chart_type == "scatter":
        st.scatter_chart(df, x=x_col, y=y_col, color=accent, height=360)
    elif chart_type in ("bar", "pie", "donut", "hbar", "area"):
        st.bar_chart(df.sort_values(by=y_col, ascending=False),
                     x=x_col, y=y_col, color=accent, height=360)
    else:
        render_table(df)
