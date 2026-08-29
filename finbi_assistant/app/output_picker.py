# Chooses how to display a result set and writes the one-line natural-language answer.
# Co-authored with CoCo
"""
Output-type selection and result summarisation.

The model already nominates a ``chart_type`` alongside its SQL, because it knows
what its own query returns. This module's job is to *verify* that nomination
against the DataFrame that actually came back and fall back to a deterministic
choice when the nomination cannot be honoured - for example when the model says
"bar" but names an x column that is not in the result.

That ordering matters: trusting the model blindly produces broken charts, and
ignoring it entirely loses information the deterministic rules cannot recover
(such as "these six rows are parts of one whole, draw a pie").
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.nl2sql import VALID_CHART_TYPES, load_prompt
from app.snowflake_utils import DEFAULT_MODEL, cortex_complete

#: Above this many rows a categorical bar chart becomes unreadable.
MAX_BAR_ROWS = 25

#: Above this many slices a pie chart becomes unreadable.
MAX_PIE_SLICES = 8


def numeric_columns(df: pd.DataFrame) -> list[str]:
    """Return the names of numeric columns in ``df``."""
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def temporal_columns(df: pd.DataFrame) -> list[str]:
    """Return columns that hold dates, whether typed as datetime or as objects.

    Snowflake DATE columns arrive as ``object`` dtype holding ``datetime.date``
    values, so a dtype check alone misses them and every trend would render as a
    bar chart. Falls back to inspecting the first non-null value.
    """
    found: list[str] = []
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            found.append(col)
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        sample = series.iloc[0]
        if hasattr(sample, "year") and hasattr(sample, "month"):
            found.append(col)
    return found


def categorical_columns(df: pd.DataFrame) -> list[str]:
    """Return columns that are neither numeric nor temporal."""
    excluded = set(numeric_columns(df)) | set(temporal_columns(df))
    return [c for c in df.columns if c not in excluded]


def pick_output_type(df: pd.DataFrame) -> str:
    """Choose a display type from the shape of ``df`` alone.

    Rules, in order:

    * empty -> ``table`` (the caller shows an explanatory message instead)
    * one row, one numeric column -> ``kpi``
    * a date column plus a numeric column -> ``line``
    * one categorical plus one numeric, few rows -> ``bar``
    * two numeric columns, many rows -> ``scatter``
    * anything else -> ``table``
    """
    if df is None or df.empty:
        return "table"

    numeric = numeric_columns(df)
    temporal = temporal_columns(df)
    categorical = categorical_columns(df)

    if len(df) == 1 and len(df.columns) == 1 and numeric:
        return "kpi"
    if temporal and numeric:
        return "line"
    if len(categorical) == 1 and len(numeric) == 1 and len(df) <= MAX_BAR_ROWS:
        return "bar"
    if not categorical and not temporal and len(numeric) >= 2 and len(df) > MAX_BAR_ROWS:
        return "scatter"
    return "table"


def resolve_output(df: pd.DataFrame, plan: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the model's chart nomination with the actual result shape.

    Args:
        df: The executed result.
        plan: The query plan from :func:`app.nl2sql.generate_query_plan`.

    Returns:
        A dict with ``chart_type``, ``x``, ``y`` and ``source`` (either
        ``"model"`` when the nomination was honoured, or ``"rules"`` when it was
        overridden). ``x`` and ``y`` are guaranteed to exist in ``df`` for any
        chart type that needs them.
    """
    fallback = {
        "chart_type": pick_output_type(df),
        "x": None,
        "y": None,
        "source": "rules",
    }
    fallback.update(_default_axes(df, fallback["chart_type"]))

    if df is None or df.empty:
        return {"chart_type": "table", "x": None, "y": None, "source": "rules"}

    requested = str(plan.get("chart_type", "")).lower()
    if requested not in VALID_CHART_TYPES:
        return fallback

    # KPI and table need no axes, but a KPI must really be a single number.
    if requested == "kpi":
        if len(df) == 1 and len(numeric_columns(df)) >= 1:
            return {"chart_type": "kpi", "x": None, "y": None, "source": "model"}
        return fallback
    if requested == "table":
        return {"chart_type": "table", "x": None, "y": None, "source": "model"}

    x = _match_column(df, plan.get("x"))
    y = _match_column(df, plan.get("y"))
    if x is None or y is None:
        return fallback  # The model named axes that are not in its own result.

    # A pie with too many slices is unreadable; downgrade rather than render it.
    if requested in ("pie", "donut") and len(df) > MAX_PIE_SLICES:
        return {"chart_type": "bar", "x": x, "y": y, "source": "rules"}
    if requested == "bar" and len(df) > MAX_BAR_ROWS:
        return {"chart_type": "table", "x": None, "y": None, "source": "rules"}

    return {"chart_type": requested, "x": x, "y": y, "source": "model"}


def _match_column(df: pd.DataFrame, name: Any) -> str | None:
    """Resolve a model-supplied column name against the DataFrame, case-insensitively.

    Snowflake returns column names uppercased, while the model tends to echo the
    lower_snake_case alias it wrote, so an exact match usually fails.
    """
    if not name or df is None:
        return None
    wanted = str(name).strip().strip('"').lower()
    for col in df.columns:
        if str(col).lower() == wanted:
            return col
    return None


def _default_axes(df: pd.DataFrame, chart_type: str) -> dict[str, Any]:
    """Pick sensible x/y columns for a rules-chosen chart type."""
    if df is None or df.empty or chart_type in ("kpi", "table"):
        return {"x": None, "y": None}

    numeric = numeric_columns(df)
    temporal = temporal_columns(df)
    categorical = categorical_columns(df)

    if chart_type in ("line", "area") and temporal and numeric:
        return {"x": temporal[0], "y": numeric[0]}
    if chart_type in ("bar", "hbar", "pie", "donut") and categorical and numeric:
        return {"x": categorical[0], "y": numeric[0]}
    if chart_type == "scatter" and len(numeric) >= 2:
        return {"x": numeric[0], "y": numeric[1]}
    return {"x": None, "y": None}


def _preview_text(df: pd.DataFrame, rows: int = 10) -> str:
    """Render the first ``rows`` of ``df`` as plain text for a prompt.

    Uses ``to_string`` rather than ``to_markdown`` on purpose: ``to_markdown``
    requires the optional ``tabulate`` package, which is not in the default
    Streamlit-in-Snowflake dependency set, so it raises ImportError in the
    deployed app. ``to_string`` is built into pandas and reads just as well to a
    language model.
    """
    return df.head(rows).to_string(index=False, max_colwidth=40)


def _describe_text(df: pd.DataFrame) -> str:
    """Render numeric summary statistics as plain text, or a note if there are none."""
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return "(no numeric columns)"
    try:
        return numeric.describe().to_string()
    except (ValueError, TypeError):
        return "(summary unavailable)"


def summarize_result(question: str, df: pd.DataFrame) -> str:
    """Write a 1-2 sentence natural-language answer for a result set.

    Only a preview and a numeric summary are sent to the model, never the whole
    DataFrame - that keeps the prompt small and avoids shipping hundreds of rows
    into a completion call.

    Args:
        question: The question that produced ``df``.
        df: The result set.

    Returns:
        A short answer, or a plain fallback sentence if the Cortex call fails.
    """
    if df is None or df.empty:
        return "No records matched that question."

    shape = f"{len(df)} row(s), {len(df.columns)} column(s): {', '.join(map(str, df.columns))}"
    prompt = (
        load_prompt("summary_prompt.txt")
        .replace("{question}", question)
        .replace("{shape}", shape)
        .replace("{preview}", _preview_text(df))
        .replace("{describe}", _describe_text(df))
    )

    try:
        reply = cortex_complete(prompt, model=DEFAULT_MODEL)
    except Exception:  # noqa: BLE001 - a missing summary must not break the answer
        return _fallback_summary(df)
    return reply.strip() or _fallback_summary(df)


def _fallback_summary(df: pd.DataFrame) -> str:
    """Describe a result without calling the model."""
    if len(df) == 1 and len(df.columns) == 1:
        return f"Result: {df.iloc[0, 0]}"
    return f"Returned {len(df)} row(s) across {len(df.columns)} column(s)."
