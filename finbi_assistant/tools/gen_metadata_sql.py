# Regenerates sql/03_metadata.sql from app/metadata.json using the app's own chunking logic.
# Co-authored with CoCo
"""
Offline generator for ``sql/03_metadata.sql``.

The running app can populate ``SCHEMA_METADATA`` itself via
``metadata_rag.ensure_embeddings()``, but having a checked-in SQL script means
the environment can be built from scratch (and reviewed in a diff) without
starting Streamlit first.

This imports :mod:`app.schema_text` rather than reimplementing chunking, so the
embeddings written by this script are byte-identical to the ones the app would
write. Run it after any edit to ``app/metadata.json``::

    python tools/gen_metadata_sql.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT
sys.path.insert(0, str(APP_ROOT))

from app.schema_text import (  # noqa: E402  - needs the sys.path tweak above
    build_all_embed_texts,
    fingerprint,
    read_metadata,
)

OUTPUT_PATH = REPO_ROOT / "sql" / "03_metadata.sql"
EMBED_MODEL = "snowflake-arctic-embed-m"

HEADER = """\
-- Creates and populates the SCHEMA_METADATA vector table used for schema retrieval.
-- Co-authored with CoCo
--
-- GENERATED FILE - do not edit by hand.
-- Regenerate with:  python tools/gen_metadata_sql.py
-- Source of truth:  app/metadata.json

USE WAREHOUSE FINBI_WH;
USE SCHEMA FINBI_DEMO.CORE;

CREATE TABLE IF NOT EXISTS SCHEMA_METADATA (
    table_name  VARCHAR(60) NOT NULL,
    chunk_text  VARCHAR     NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    embedding   VECTOR(FLOAT, 768)
)
COMMENT = 'One embedded text chunk per business table, used for schema RAG.';

TRUNCATE TABLE SCHEMA_METADATA;
"""


def sql_literal(value: str) -> str:
    """Quote ``value`` as a SQL string literal, escaping embedded quotes."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def main() -> None:
    metadata = read_metadata()
    chunks = build_all_embed_texts(metadata)
    current_fp = fingerprint(chunks)

    parts = [HEADER]
    for name, text in chunks.items():
        parts.append(
            "INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)\n"
            f"SELECT {sql_literal(name)},\n"
            f"       {sql_literal(text)},\n"
            f"       {sql_literal(current_fp)},\n"
            f"       SNOWFLAKE.CORTEX.EMBED_TEXT_768("
            f"{sql_literal(EMBED_MODEL)}, {sql_literal(text)});\n"
        )

    OUTPUT_PATH.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  tables:      {len(chunks)}")
    print(f"  fingerprint: {current_fp}")


if __name__ == "__main__":
    main()
