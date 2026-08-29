# One-prompt dashboard builder: generates, validates, edits and renders a JSON dashboard spec.
# Co-authored with CoCo
"""
The spec-driven dashboard engine.

An instruction becomes a JSON *specification* - which widgets, which SQL, which
filters - and :func:`render_dashboard` draws whatever that JSON describes. The
renderer never changes; only the JSON does. That is what makes "now split it by
provider and add a KPI for pending payouts" work as a conversational edit rather
than a rebuild.

Three things keep it from falling over:

* **Validation.** :func:`validate_spec` checks structure, widget types, filter
  references and every widget's SQL before anything renders, so a malformed model
  response produces an error message and leaves the previous dashboard intact.
* **Typed filter substitution.** Filter values are converted to correctly quoted
  SQL literals by type - never string-concatenated - and the result is re-checked
  by the safety guard before execution.
* **Per-widget isolation.** One failing widget shows an error in its own cell
  instead of taking down the whole page.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from app import charts
from app.metadata_rag import format_tables_for_prompt, load_metadata
from app.nl2sql import extract_json, load_prompt
from app.snowflake_utils import SQL_MODEL, cortex_complete, run_query, summarize_error
from app.sql_guard import check_sql

#: Widget types the renderer can draw. Mirrors the builders in :mod:`app.charts`.
WIDGET_TYPES = ("kpi", "bar", "hbar", "line", "area", "scatter", "pie", "donut", "table")

#: Filter types the renderer can turn into Streamlit controls.
FILTER_TYPES = ("date_range", "select", "multiselect")

#: Allowed layouts, mapped to the number of columns in the grid.
LAYOUTS = {"grid-1col": 1, "grid-2col": 2, "grid-3col": 3}

MAX_WIDGETS = 8
MAX_FILTERS = 3

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: Session-state keys, kept in one place so the app and the engine agree.
SPEC_KEY = "dashboard_spec"
FILTERS_KEY = "dashboard_filters"


# ---------------------------------------------------------------------------
# Schema knowledge (used to validate filter columns against real tables)
# ---------------------------------------------------------------------------


def schema_index() -> dict[str, set[str]]:
    """Return ``{TABLE_NAME: {COLUMN, ...}}`` for every table in the metadata."""
    index: dict[str, set[str]] = {}
    for table in load_metadata()["tables"]:
        index[table["name"].upper()] = {c["name"].upper() for c in table["columns"]}
    return index


def _split_column_ref(reference: str) -> tuple[str, str] | None:
    """Parse a ``TABLE.column`` reference, returning uppercased parts."""
    parts = str(reference).replace('"', "").split(".")
    if len(parts) != 2:
        return None
    table, column = parts[0].strip().upper(), parts[1].strip().upper()
    if not table or not column:
        return None
    return table, column


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_spec(spec: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalise a dashboard spec.

    Deliberately hand-rolled rather than using pydantic: the app runs in
    Streamlit in Snowflake, where pinning an extra dependency version is friction
    we do not need for a six-key schema. It also lets us return *all* the problems
    at once, which is what makes the one-shot repair retry effective.

    Args:
        spec: The parsed JSON from the model.

    Returns:
        ``(normalised_spec, errors)``. When ``errors`` is empty the spec is safe
        to render. When it is not, ``normalised_spec`` should be discarded.
    """
    errors: list[str] = []

    if not isinstance(spec, dict):
        return {}, ["Spec must be a JSON object."]

    title = str(spec.get("title") or "Dashboard").strip()[:120]

    layout = str(spec.get("layout") or "grid-2col").strip().lower()
    if layout not in LAYOUTS:
        layout = "grid-2col"

    index = schema_index()

    # --- filters -----------------------------------------------------------
    raw_filters = spec.get("filters") or []
    if not isinstance(raw_filters, list):
        errors.append("'filters' must be a list.")
        raw_filters = []
    if len(raw_filters) > MAX_FILTERS:
        errors.append(f"At most {MAX_FILTERS} filters are allowed, got {len(raw_filters)}.")
        raw_filters = raw_filters[:MAX_FILTERS]

    filters: list[dict[str, Any]] = []
    seen_filter_ids: set[str] = set()
    for position, item in enumerate(raw_filters):
        if not isinstance(item, dict):
            errors.append(f"filters[{position}] must be an object.")
            continue

        fid = str(item.get("id") or "").strip().lower()
        if not _IDENTIFIER.match(fid):
            errors.append(f"filters[{position}].id {fid!r} must be lower_snake_case.")
            continue
        if fid in seen_filter_ids:
            errors.append(f"Duplicate filter id {fid!r}.")
            continue

        ftype = str(item.get("type") or "").strip().lower()
        if ftype not in FILTER_TYPES:
            errors.append(f"filters[{position}].type {ftype!r} must be one of {FILTER_TYPES}.")
            continue

        reference = _split_column_ref(item.get("column", ""))
        if reference is None:
            errors.append(
                f"filters[{position}].column must be 'TABLE.column', got {item.get('column')!r}."
            )
            continue
        table, column = reference
        if table not in index:
            errors.append(f"filters[{position}] references unknown table {table!r}.")
            continue
        if column not in index[table]:
            errors.append(f"filters[{position}] references unknown column {table}.{column}.")
            continue

        seen_filter_ids.add(fid)
        filters.append(
            {
                "id": fid,
                "label": str(item.get("label") or fid.replace("_", " ").title())[:60],
                "type": ftype,
                "column": f"{table}.{column}",
            }
        )

    # --- widgets -----------------------------------------------------------
    raw_widgets = spec.get("widgets") or []
    if not isinstance(raw_widgets, list) or not raw_widgets:
        errors.append("'widgets' must be a non-empty list.")
        raw_widgets = []
    if len(raw_widgets) > MAX_WIDGETS:
        errors.append(f"At most {MAX_WIDGETS} widgets are allowed, got {len(raw_widgets)}.")
        raw_widgets = raw_widgets[:MAX_WIDGETS]

    widgets: list[dict[str, Any]] = []
    seen_widget_ids: set[str] = set()
    for position, item in enumerate(raw_widgets):
        if not isinstance(item, dict):
            errors.append(f"widgets[{position}] must be an object.")
            continue

        wid = str(item.get("id") or f"w{position + 1}").strip().lower()
        if not _IDENTIFIER.match(wid):
            wid = f"w{position + 1}"
        if wid in seen_widget_ids:
            wid = f"w{position + 1}_{len(seen_widget_ids)}"
        seen_widget_ids.add(wid)

        wtype = str(item.get("type") or "").strip().lower()
        if wtype not in WIDGET_TYPES:
            errors.append(f"widgets[{position}].type {wtype!r} must be one of {WIDGET_TYPES}.")
            continue

        sql = str(item.get("sql") or "").strip().rstrip(";").strip()
        if not sql:
            errors.append(f"widgets[{position}] has no sql.")
            continue

        # Check the template with placeholders neutralised, so the guard sees a
        # realistic statement rather than tripping over "{date_range.start}".
        ok, reason = check_sql(_neutralise_placeholders(sql))
        if not ok:
            errors.append(f"widgets[{position}] ({wid}) rejected: {reason}")
            continue

        unknown = _unknown_placeholders(sql, seen_filter_ids)
        if unknown:
            errors.append(
                f"widgets[{position}] ({wid}) uses undeclared filter placeholder(s): "
                f"{', '.join(sorted(unknown))}."
            )
            continue

        widgets.append(
            {
                "id": wid,
                "type": wtype,
                "title": str(item.get("title") or wid).strip()[:120],
                "sql": sql,
                "x": _none_if_blank(item.get("x")),
                "y": _none_if_blank(item.get("y")),
            }
        )

    if not widgets and not errors:
        errors.append("The spec contained no renderable widgets.")

    # Drop filters that no surviving widget actually references.
    used = set()
    for widget in widgets:
        used |= _referenced_filters(widget["sql"])
    filters = [f for f in filters if f["id"] in used]

    return {"title": title, "layout": layout, "filters": filters, "widgets": widgets}, errors


def _none_if_blank(value: Any) -> str | None:
    """Normalise an x/y hint, mapping placeholders for "nothing" to None."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in ("null", "none", "n/a") else text


_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)(?:\.(start|end))?\}", re.IGNORECASE)


def _referenced_filters(sql: str) -> set[str]:
    """Return the filter ids referenced by placeholders in ``sql``."""
    return {m.group(1).lower() for m in _PLACEHOLDER.finditer(sql)}


def _unknown_placeholders(sql: str, known: Iterable[str]) -> set[str]:
    """Return placeholder ids in ``sql`` that are not declared as filters."""
    known_set = {k.lower() for k in known}
    return {fid for fid in _referenced_filters(sql) if fid not in known_set}


def _neutralise_placeholders(sql: str) -> str:
    """Replace filter placeholders with a harmless literal for guard checking."""
    return _PLACEHOLDER.sub("NULL", sql)


# ---------------------------------------------------------------------------
# Generation and editing
# ---------------------------------------------------------------------------


def _fill_common(template: str, tables: list[dict[str, Any]]) -> str:
    """Substitute the schema block and date context into a prompt template."""
    meta = load_metadata()
    return (
        template.replace("{schema_block}", format_tables_for_prompt(tables))
        .replace("{today}", dt.date.today().isoformat())
        .replace("{date_range}", meta.get("date_range", "2026"))
    )


def _generate_validated(prompt: str, retry_prompt_suffix: str) -> dict[str, Any]:
    """Call Cortex, validate the spec, and retry once with the errors fed back.

    Raises:
        ValueError: If the second attempt is still invalid.
    """
    reply = cortex_complete(prompt, model=SQL_MODEL)
    try:
        spec, errors = validate_spec(extract_json(reply))
    except (ValueError, json.JSONDecodeError) as exc:
        spec, errors = {}, [f"Response was not valid JSON: {exc}"]

    if not errors:
        return spec

    retry = (
        f"{prompt}\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED:\n"
        + "\n".join(f"- {e}" for e in errors[:8])
        + f"\n\n{retry_prompt_suffix}"
    )
    reply = cortex_complete(retry, model=SQL_MODEL)
    try:
        spec, errors = validate_spec(extract_json(reply))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"The model did not return a usable dashboard spec: {exc}") from exc

    if errors:
        raise ValueError(
            "The model did not return a usable dashboard spec:\n"
            + "\n".join(f"- {e}" for e in errors[:5])
        )
    return spec


def generate_dashboard_spec(
    instruction: str,
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a brand-new dashboard spec from a single instruction.

    Args:
        instruction: What the user asked for, e.g. "Build me a dashboard on
            settlement reliability".
        tables: Retrieved table metadata.

    Returns:
        A validated spec dict.

    Raises:
        ValueError: If the model cannot produce a valid spec in two attempts.
    """
    prompt = _fill_common(
        load_prompt("dashboard_spec_prompt.txt"), tables
    ).replace("{instruction}", instruction)
    return _generate_validated(prompt, "Return the corrected JSON object only.\n\nJSON:")


def edit_dashboard_spec(
    current_spec: dict[str, Any],
    instruction: str,
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a conversational edit to an existing spec.

    The model is given the whole current spec and asked for the whole updated
    spec back, rather than a diff. At this size a full rewrite is markedly more
    reliable than patch semantics, and it keeps the renderer stateless.

    Args:
        current_spec: The spec currently on screen.
        instruction: The change to make, e.g. "make it monthly instead".
        tables: Retrieved table metadata.

    Returns:
        A validated spec dict.

    Raises:
        ValueError: If the model cannot produce a valid spec in two attempts.
    """
    prompt = (
        _fill_common(load_prompt("dashboard_edit_prompt.txt"), tables)
        .replace("{current_spec}", json.dumps(current_spec, indent=2))
        .replace("{instruction}", instruction)
    )
    return _generate_validated(
        prompt, "Return the corrected COMPLETE JSON object only.\n\nUPDATED JSON:"
    )


# ---------------------------------------------------------------------------
# Filter values -> SQL literals
# ---------------------------------------------------------------------------


def sql_literal(value: Any) -> str:
    """Render a Python value as a correctly typed, correctly quoted SQL literal.

    This is the only place filter values enter SQL text. Dates become typed
    ``DATE '...'`` literals, numbers pass through unquoted, and strings are
    single-quoted with embedded quotes doubled.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def sql_list(values: Iterable[Any]) -> str:
    """Render an iterable as a parenthesised SQL list for use with ``IN``."""
    rendered = [sql_literal(v) for v in values]
    if not rendered:
        return "(NULL)"  # An empty IN list is a syntax error; match nothing instead.
    return "(" + ", ".join(rendered) + ")"


def build_substitutions(
    filters: list[dict[str, Any]],
    values: dict[str, Any],
) -> dict[str, str]:
    """Map every placeholder to its SQL literal for the current filter values.

    Returns:
        ``{"{date_range.start}": "DATE '2026-04-01'", "{provider}": "'PAYTM'", ...}``
    """
    substitutions: dict[str, str] = {}
    for spec_filter in filters:
        fid = spec_filter["id"]
        value = values.get(fid)

        if spec_filter["type"] == "date_range":
            start, end = _as_date_pair(value)
            substitutions[f"{{{fid}.start}}"] = sql_literal(start)
            substitutions[f"{{{fid}.end}}"] = sql_literal(end)
        elif spec_filter["type"] == "multiselect":
            chosen = value if isinstance(value, (list, tuple, set)) else []
            substitutions[f"{{{fid}}}"] = sql_list(chosen)
        else:
            substitutions[f"{{{fid}}}"] = sql_literal(value)
    return substitutions


def _as_date_pair(value: Any) -> tuple[dt.date, dt.date]:
    """Coerce a date_input value into a (start, end) pair."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return value[0], value[0]
    if isinstance(value, dt.date):
        return value, value
    today = dt.date.today()
    return dt.date(today.year, 1, 1), today


def apply_substitutions(sql: str, substitutions: dict[str, str]) -> str:
    """Replace filter placeholders in ``sql`` with their SQL literals."""
    result = sql
    for placeholder, literal in substitutions.items():
        result = result.replace(placeholder, literal)
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False, ttl=300)
def _distinct_values(table: str, column: str) -> list[Any]:
    """Fetch distinct values for a filter control.

    ``table`` and ``column`` are validated against ``metadata.json`` by
    :func:`validate_spec` before reaching here, so interpolating them is safe -
    they can only ever be names that exist in our own schema.
    """
    index = schema_index()
    if table.upper() not in index or column.upper() not in index[table.upper()]:
        return []
    frame = run_query(
        f"SELECT DISTINCT {column} AS v FROM {table} WHERE {column} IS NOT NULL ORDER BY 1 LIMIT 100"
    )
    return frame["V"].tolist() if "V" in frame.columns else []


@st.cache_data(show_spinner=False, ttl=300)
def _date_bounds(table: str, column: str) -> tuple[dt.date, dt.date]:
    """Fetch the min and max date for a date_range control."""
    index = schema_index()
    if table.upper() not in index or column.upper() not in index[table.upper()]:
        today = dt.date.today()
        return dt.date(today.year, 1, 1), today
    frame = run_query(f"SELECT MIN({column}) AS lo, MAX({column}) AS hi FROM {table}")
    low, high = frame.iloc[0]["LO"], frame.iloc[0]["HI"]
    today = dt.date.today()
    if low is None or high is None:
        return dt.date(today.year, 1, 1), today
    return low, high


def render_filters(spec: dict[str, Any]) -> dict[str, Any]:
    """Draw the spec's filters as Streamlit controls and return their values.

    Values are also stored in ``st.session_state[FILTERS_KEY]`` so they survive
    reruns and so a spec edit does not reset the user's selections.
    """
    filters = spec.get("filters") or []
    if not filters:
        return {}

    stored: dict[str, Any] = st.session_state.setdefault(FILTERS_KEY, {})
    values: dict[str, Any] = {}

    for spec_filter, slot in zip(filters, st.columns(len(filters))):
        fid = spec_filter["id"]
        table, column = spec_filter["column"].split(".")
        with slot:
            if spec_filter["type"] == "date_range":
                low, high = _date_bounds(table, column)
                default = stored.get(fid) or (low, high)
                chosen = st.date_input(
                    spec_filter["label"],
                    value=default,
                    min_value=low,
                    max_value=high,
                    key=f"flt_{fid}",
                )
                values[fid] = chosen
            elif spec_filter["type"] == "multiselect":
                options = _distinct_values(table, column)
                default = [v for v in (stored.get(fid) or options) if v in options]
                values[fid] = st.multiselect(
                    spec_filter["label"], options=options, default=default, key=f"flt_{fid}"
                )
            else:
                options = _distinct_values(table, column)
                previous = stored.get(fid)
                position = options.index(previous) if previous in options else 0
                values[fid] = st.selectbox(
                    spec_filter["label"], options=options, index=position, key=f"flt_{fid}"
                ) if options else None

    st.session_state[FILTERS_KEY] = values
    return values


#: Colors assigned to each widget position for visual variety.
WIDGET_COLORS = [
    "#6C5CE7", "#00B894", "#0984E3", "#E17055", "#E84393",
    "#00CEC9", "#F39C12", "#FDCB6E",
]


def render_widget(
    widget: dict[str, Any],
    substitutions: dict[str, str],
    position: int = 0,
) -> None:
    """Render one widget inside a styled card with colored accent."""
    color = WIDGET_COLORS[position % len(WIDGET_COLORS)]
    st.markdown(
        f"""<div style="
            background: #1A1D23;
            border-radius: 12px;
            border-top: 4px solid {color};
            padding: 16px 14px 10px 14px;
            margin-bottom: 8px;
            border: 1px solid #2A2D35;
            border-top: 4px solid {color};
        ">
        <span style="color: {color}; font-weight: 700; font-size: 0.95rem; letter-spacing: 0.01em;">
        {widget["title"]}</span></div>""",
        unsafe_allow_html=True,
    )

    sql = apply_substitutions(widget["sql"], substitutions)

    # Re-check after substitution. The literals are built by sql_literal, but the
    # guard is cheap and this is the last gate before execution.
    ok, reason = check_sql(sql)
    if not ok:
        st.error(reason)
        with st.expander("Offending SQL"):
            st.code(sql, language="sql")
        return

    try:
        frame = run_query(sql)
    except Exception as exc:  # noqa: BLE001 - isolate this widget's failure
        st.error(f"This widget could not load: {summarize_error(exc)}")
        with st.expander("SQL"):
            st.code(sql, language="sql")
        return

    charts.render(
        frame,
        widget["type"],
        x=widget.get("x"),
        y=widget.get("y"),
        key=f"chart_{widget['id']}",
    )


def render_dashboard(spec: dict[str, Any]) -> None:
    """Render an entire dashboard spec.

    Generic by design: every widget type is dispatched through
    :func:`app.charts.render`, so supporting a new visual means adding a builder
    in :mod:`app.charts` and its name to :data:`WIDGET_TYPES` - this function
    does not change.
    """
    if not spec or not spec.get("widgets"):
        st.info("No dashboard yet. Describe one above and press Build.")
        return

    st.subheader(spec.get("title", "Dashboard"))

    values = render_filters(spec)
    substitutions = build_substitutions(spec.get("filters", []), values)

    widgets = spec["widgets"]
    per_row = LAYOUTS.get(spec.get("layout", "grid-2col"), 2)

    # KPI widgets are compact, so give them their own full-width row at the top.
    kpis = [w for w in widgets if w["type"] == "kpi"]
    others = [w for w in widgets if w["type"] != "kpi"]

    if kpis:
        for idx, (kpi, slot) in enumerate(zip(kpis, st.columns(len(kpis)))):
            with slot:
                render_widget(kpi, substitutions, position=idx)
        st.divider()

    widget_offset = len(kpis)
    for start in range(0, len(others), per_row):
        row = others[start : start + per_row]
        for idx, (widget, slot) in enumerate(zip(row, st.columns(per_row))):
            with slot:
                render_widget(widget, substitutions, position=widget_offset + start + idx)
