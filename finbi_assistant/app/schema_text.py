# Pure helpers that turn metadata.json into embed texts, prompt chunks and a staleness fingerprint.
# Co-authored with CoCo
"""
Schema text construction, with no Streamlit or Snowflake dependency.

Kept separate from :mod:`app.metadata_rag` so that the offline SQL generator
(``tools/gen_metadata_sql.py``) and the tests can import exactly the same
chunking logic the running app uses. If these two ever drifted apart, the
embeddings in ``SCHEMA_METADATA`` would silently stop matching the prompts.

Each table has two text representations, and the distinction matters:

``build_embed_text``
    Compact, business-language summary. This is what gets embedded.

``build_chunk``
    Full column-level detail. This is what gets injected into the SQL prompt.

Why they differ: embedding the full chunk measurably breaks retrieval. The
schema-wide conventions block is byte-identical across all six tables, so
including it drags every vector toward a common centroid, and the long
per-column prose dilutes whatever is distinctive about each table. With the full
chunk embedded, "net revenue by provider this year" did not rank TRANSACTIONS in
the top 3 at all; with the compact text it ranks first.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

#: ``metadata.json`` lives next to this module inside ``app/``.
METADATA_PATH = Path(__file__).parent / "metadata.json"


def read_metadata(path: Path = METADATA_PATH) -> dict[str, Any]:
    """Parse and return the metadata document at ``path``."""
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------


def build_embed_text(table: dict[str, Any]) -> str:
    """Build the compact text that gets embedded for retrieval.

    Includes only the discriminative parts: table name, grain, description, bare
    column names, metric definitions and example questions. The metric and
    question lines carry most of the weight, because questions asked in business
    language ("chargeback win rate", "pending payouts") match those far better
    than they match raw column names.

    Deliberately excludes the shared conventions block and the per-column prose
    -- see the module docstring for why.
    """
    columns = ", ".join(col["name"] for col in table["columns"])
    metrics = "\n".join(f"  - {m}" for m in table.get("common_metrics", []))
    questions = "\n".join(f"  - {q}" for q in table.get("example_questions", []))
    return (
        f"TABLE {table['name']}\n"
        f"Grain: {table['grain']}\n"
        f"Description: {table['description']}\n"
        f"Columns: {columns}\n"
        f"Metrics this table answers:\n{metrics}\n"
        f"Questions this table answers:\n{questions}\n"
    )


def build_chunk(table: dict[str, Any], conventions: list[str]) -> str:
    """Build the full detail block for a table, for injection into an LLM prompt.

    Args:
        table: One entry from ``metadata.json``'s ``tables`` array.
        conventions: The schema-wide conventions, repeated per table so that the
            model sees them regardless of which tables were retrieved.
    """
    columns = "\n".join(
        f"  - {col['name']} ({col['type']}): {col['description']}"
        for col in table["columns"]
    )
    relationships = "\n".join(f"  - {rel}" for rel in table.get("relationships", []))
    metrics = "\n".join(f"  - {m}" for m in table.get("common_metrics", []))
    questions = "\n".join(f"  - {q}" for q in table.get("example_questions", []))
    rules = "\n".join(f"  - {c}" for c in conventions)

    return (
        f"TABLE {table['name']}\n"
        f"Grain: {table['grain']}\n"
        f"Description: {table['description']}\n"
        f"Approximate row count: {table.get('row_count', 'unknown')}\n"
        f"Columns:\n{columns}\n"
        f"Relationships:\n{relationships}\n"
        f"Common metrics:\n{metrics}\n"
        f"Example questions:\n{questions}\n"
        f"Schema-wide conventions:\n{rules}\n"
    )


def build_all_embed_texts(metadata: dict[str, Any]) -> dict[str, str]:
    """Return ``{table_name: embed_text}`` for every table in ``metadata``."""
    return {t["name"]: build_embed_text(t) for t in metadata["tables"]}


def format_tables_for_prompt(
    tables: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> str:
    """Render retrieved table metadata as the schema block of an LLM prompt.

    Args:
        tables: The table metadata dicts chosen by retrieval.
        metadata: The full metadata document, only needed for the schema-wide
            conventions. Read from disk when omitted.
    """
    meta = metadata if metadata is not None else read_metadata()
    conventions = meta.get("conventions", [])
    return "\n".join(build_chunk(table, conventions) for table in tables)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


def fingerprint(chunks: dict[str, str]) -> str:
    """Return a stable hash of ``chunks``, used to detect stale embeddings.

    Stored alongside every row in ``SCHEMA_METADATA``. When ``metadata.json``
    changes, the fingerprint changes, and the app re-embeds on next start rather
    than serving retrieval results built from an older schema description.

    The separators are control characters that cannot appear in the JSON text,
    so no two distinct chunk sets can hash to the same value by concatenation.
    """
    joined = "\n\x00\n".join(
        f"{name}\x01{text}" for name, text in sorted(chunks.items())
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
