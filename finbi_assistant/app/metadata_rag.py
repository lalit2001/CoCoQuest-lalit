# Schema-RAG layer: turns metadata.json into in-database embeddings and retrieves the tables relevant to a question.
# Co-authored with CoCo
"""
Schema retrieval, not document retrieval.

Given a natural-language question we only want to show the SQL generator the two
or three tables that actually matter, instead of dumping the whole schema into
every prompt.

Each table gets two text representations, built in :mod:`app.schema_text`:

* ``build_embed_text`` - a compact, business-language summary. This is what gets
  embedded with ``SNOWFLAKE.CORTEX.EMBED_TEXT_768`` and stored in
  ``FINBI_DEMO.CORE.SCHEMA_METADATA``.
* ``build_chunk`` - the full column-level detail, injected into the SQL prompt
  once a table has been selected.

Retrieval is a single ``VECTOR_COSINE_SIMILARITY`` query, so the whole RAG step
runs inside Snowflake.
"""

from __future__ import annotations

from typing import Any

from app.schema_text import (
    METADATA_PATH,
    build_all_embed_texts,
    build_chunk,
    fingerprint,
    format_tables_for_prompt,
    read_metadata,
)
from app.snowflake_utils import (
    DATABASE,
    EMBED_MODEL,
    SCHEMA,
    cache_data,
    cache_resource,
    get_session,
)

__all__ = [
    "EMBEDDING_TABLE",
    "build_all_chunks",
    "ensure_embeddings",
    "format_tables_for_prompt",
    "load_metadata",
    "retrieve_relevant_tables",
    "table_metadata",
]

EMBEDDING_TABLE = f"{DATABASE}.{SCHEMA}.SCHEMA_METADATA"


# ---------------------------------------------------------------------------
# Loading and chunking
# ---------------------------------------------------------------------------


@cache_data
def load_metadata() -> dict[str, Any]:
    """Load and return the parsed contents of ``metadata.json``, cached."""
    return read_metadata(METADATA_PATH)


def build_all_chunks() -> dict[str, str]:
    """Return ``{table_name: embed_text}`` for every table in the metadata."""
    return build_all_embed_texts(load_metadata())


def table_metadata(table_name: str) -> dict[str, Any]:
    """Return the raw metadata dict for a single table."""
    for table in load_metadata()["tables"]:
        if table["name"].upper() == table_name.upper():
            return table
    raise KeyError(f"No metadata for table {table_name!r}")


# ---------------------------------------------------------------------------
# Embedding maintenance
# ---------------------------------------------------------------------------


@cache_resource
def ensure_embeddings() -> str:
    """Create and populate ``SCHEMA_METADATA`` if it is missing or out of date.

    Returns the fingerprint that is now stored in the table. Cached with
    ``cache_resource`` so the (cheap but non-zero) embedding cost is paid at most
    once per app process, and skipped entirely when the table already matches
    ``metadata.json``.
    """
    session = get_session()
    chunks = build_all_chunks()
    current_fp = fingerprint(chunks)

    session.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {EMBEDDING_TABLE} (
            table_name  VARCHAR(60) NOT NULL,
            chunk_text  VARCHAR     NOT NULL,
            fingerprint VARCHAR(64) NOT NULL,
            embedding   VECTOR(FLOAT, 768)
        )
        """
    ).collect()

    current = session.sql(
        f"SELECT DISTINCT fingerprint FROM {EMBEDDING_TABLE}"
    ).collect()
    if len(current) == 1 and current[0]["FINGERPRINT"] == current_fp:
        return current_fp  # Already up to date.

    session.sql(f"TRUNCATE TABLE {EMBEDDING_TABLE}").collect()
    for name, text in chunks.items():
        session.sql(
            f"""
            INSERT INTO {EMBEDDING_TABLE} (table_name, chunk_text, fingerprint, embedding)
            SELECT ?, ?, ?, SNOWFLAKE.CORTEX.EMBED_TEXT_768(?, ?)
            """,
            params=[name, text, current_fp, EMBED_MODEL, text],
        ).collect()
    return current_fp


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def retrieve_relevant_tables(question: str, k: int = 3) -> list[dict[str, Any]]:
    """Return the ``k`` table metadata dicts most relevant to ``question``.

    Ranking happens inside Snowflake: the question is embedded with the same
    model as the chunks and scored against them with
    ``VECTOR_COSINE_SIMILARITY``.

    Args:
        question: The user's natural-language question.
        k: How many tables to return. Clamped to 1..6.

    Returns:
        Table metadata dicts, most relevant first, each with an extra
        ``_similarity`` key. Falls back to every table if retrieval fails.
    """
    k = max(1, min(int(k), 6))
    ensure_embeddings()
    session = get_session()

    try:
        rows = session.sql(
            f"""
            SELECT table_name,
                   VECTOR_COSINE_SIMILARITY(
                       embedding,
                       SNOWFLAKE.CORTEX.EMBED_TEXT_768(?, ?)
                   ) AS similarity
            FROM {EMBEDDING_TABLE}
            ORDER BY similarity DESC
            LIMIT {k}
            """,
            params=[EMBED_MODEL, question],
        ).collect()
    except Exception:  # noqa: BLE001 - retrieval must never break the app
        return [dict(t) for t in load_metadata()["tables"]]

    results: list[dict[str, Any]] = []
    for row in rows:
        meta = dict(table_metadata(row["TABLE_NAME"]))
        meta["_similarity"] = float(row["SIMILARITY"])
        results.append(meta)
    return results


def format_tables_for_prompt(tables: list[dict[str, Any]]) -> str:
    """Render retrieved table metadata as the schema block for an LLM prompt."""
    meta = load_metadata()
    conventions = meta.get("conventions", [])
    blocks = [build_chunk(t, conventions) for t in tables]
    return "\n".join(blocks)
