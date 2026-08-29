# FinBI Conversational BI Assistant — Design Document

## What this is

A Streamlit in Snowflake app for Theme 2 of the Snowflake + CoCo Quest 2026
hackathon. A small merchant who takes card payments can ask questions about their
business in plain English and get back a chart, a table, or a whole dashboard —
without writing SQL, without a BI analyst, and without any data leaving their
Snowflake account.

Three capabilities, one code path:

1. **Chat** — "What is our net revenue by provider this year?" → a bar chart and
   a one-sentence answer.
2. **One-prompt dashboards** — "Build me a dashboard on settlement reliability"
   → a multi-widget dashboard generated from a JSON spec, editable by follow-up
   prompts ("now split that by provider").
3. **History** — every question and every dashboard is persisted to Snowflake
   tables, survives a page reload, and can be re-run against current data.

---

## The "agentic loop" — what it is and what it actually does

The term "agent loop" describes a pattern where an LLM takes an action, observes
the result, and decides what to do next — rather than being given one shot to get
it right. In this app the loop runs inside `app/pipeline.py` and exists for one
specific reason: **Snowflake compilation errors are not the user's problem**.

Here is what happens when a question is asked:

```
Question
  → retrieve relevant tables (vector similarity over SCHEMA_METADATA)
  → ask Cortex for SQL + chart choice
  → safety guard (read-only? single statement? no forbidden keywords?)
  → execute against Snowflake
  → if it fails:
      ├── record the failed SQL and the error
      ├── diagnose the error against the real schema (app/error_hints.py)
      ├── send Cortex the full history of what already failed + the diagnosis
      ├── check the new SQL through the guard again
      └── execute again (up to MAX_ATTEMPTS = 4 total)
  → resolve the chart type against the actual result shape
  → summarise the answer in one sentence
  → persist the turn to CHAT_MESSAGES
```

This is the loop. It is not general-purpose autonomy — it is a focused retry with
targeted feedback, bounded to 4 attempts, and the feedback is the part that makes
it materially different from "try again".

### Why blind retrying does not work

The case that forced this design: asked "what is the fees and charges", the model
wrote `SUM(fee) FROM FEE_SCHEDULE`. That table has `transaction_fee`,
`monthly_fee` and `chargeback_fee` — `fee` is a column on `TRANSACTIONS`. The
generic repair prompt said "a column may not exist" without specifying which
column or where it lives. Two retries produced the byte-identical error.

What fixed it was `app/error_hints.py`, which:

- Parses the Snowflake error to extract the offending identifier.
- Looks up which tables actually own that column.
- Lists every column of every retrieved table (so the model cannot guess).
- Warns that measures spread across tables need a CTE or UNION ALL, not a
  flat SELECT — and gives an example.

With that hint, the question succeeds on attempt 2, consistently.

### What the loop knows when to stop

- After `MAX_ATTEMPTS` it raises a RuntimeError with the attempt count and the
  last error.
- If the model concludes mid-repair that the tables cannot answer the question,
  it returns a sentinel answer instead of another doomed query, and the pipeline
  raises `UnanswerableError` — a real answer, not a crash.
- The guard re-checks every repaired query. A repair that produces a write
  statement is caught and refused, the same as any other write.

---

## Data model

Six tables in `FINBI_DEMO.CORE`, plus two persistence tables and one RAG index:

| Table | Purpose | Rows |
|---|---|---|
| `TRANSACTIONS` | Every card payment (sales + refunds) | 350 |
| `DISPUTES` | Chargebacks, FK to a transaction | 47 |
| `SETTLEMENTS` | Payout batches, derived from settled transactions | 276 |
| `LEDGER` | Double-entry postings, derived from all three | 1,612 |
| `CARD_MACHINE_ACCOUNTS` | Terminal reference data | 5 |
| `FEE_SCHEDULE` | Contracted rates per provider and card network | 9 |
| `SCHEMA_METADATA` | One embedded text chunk per table (RAG index) | 6 |
| `CHAT_MESSAGES` | Persisted chat turns | grows |
| `DASHBOARDS` | Saved dashboard specs as VARIANT | grows |

**Why six tables, not one flat table?** A single CSV would answer "total revenue"
but fail "show me the ledger impact of disputes lost in May" — that joins
`DISPUTES` → `LEDGER` via `source_id`. The cross-table reasoning is what
demonstrates that the SQL generation is genuinely useful, not a parlour trick.

**Reconciliation.** Settlements and ledger are derived deterministically and
reconcile exactly:

```
settlements net  =  transactions net  =  bank balance  =  600,384.07
ledger debits    =  ledger credits                     =  1,436,944.52
```

---

## Schema RAG — how the right tables are found

Every prompt needs table metadata, but dumping all six tables into every call
wastes tokens and dilutes the model's attention. Instead:

1. Each table has two text representations, built in `app/schema_text.py`:
   - **Embed text** — compact: table name, business-language aliases, metric
     definitions, example questions. This is what gets embedded.
   - **Prompt text** — full: columns with types and descriptions, relationships,
     conventions. This is what the SQL generator sees once a table is selected.

2. The embed texts are stored in `SCHEMA_METADATA` with
   `SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', text)` vectors.

3. On each question, the question is embedded with the same model and ranked by
   `VECTOR_COSINE_SIMILARITY`. The top 3 tables' full prompt texts go into the
   SQL generation call.

**Why the split matters.** Before it was introduced, "net revenue by provider"
ranked `TRANSACTIONS` outside the top 3 — the long column list and the
conventions block (identical in every chunk) flattened the similarity scores.
After the split, `TRANSACTIONS` ranks first at 0.51.

---

## SQL generation and safety

### Generation (`app/nl2sql.py`)

One Cortex call returns a JSON object: `{sql, chart_type, x, y, reason}`. Asking
for the chart in the same call is more reliable than guessing afterwards, because
the model knows what its query returns.

Two models are used:
- `llama3.3-70b` for SQL and dashboard specs (join reasoning, alias management).
- `llama3.1-8b` for summaries and cheap classification calls.

### Safety guard (`app/sql_guard.py`)

The guard is a whitelist on statement *shape*:

1. Must begin with `SELECT` or `WITH`.
2. Exactly one statement (no stacked semicolons).
3. No SQL comments.
4. No write, DDL, permission or session keyword.

String literal contents are blanked before scanning, so
`WHERE notes = 'customer asked to delete the charge'` passes while a real
`DELETE` does not. Unterminated literals are rejected outright — they hide
everything after the opening quote from the keyword scan, which was an actual hole
found by the test suite.

The guard is standalone (no Streamlit, no Snowflake imports) and has 36 test
cases: 12 legitimate queries and 24 hostile or malformed statements. It applies to
every generated query, every dashboard widget, and every re-run from history.

### Prompt-level refusal

The prompt also tells the model to return a fixed sentinel instead of a
destructive query. `app/pipeline.py` detects the sentinel and raises
`UnsafeQueryError`, so the user sees a shield icon and an explanation instead of
a one-cell table containing a sentence. Two independent layers — the prompt and
the guard — means neither can be bypassed alone.

---

## Output selection (`app/output_picker.py`)

The model nominates a chart type alongside its SQL. `resolve_output()` then
checks that nomination against the actual DataFrame:

- Do the named x/y columns exist? (Case-insensitive match — the model writes
  `net_revenue`, Snowflake returns `NET_REVENUE`.)
- Is the row count appropriate? (A pie with 12 slices downgrades to a bar; a bar
  with 60 rows downgrades to a table.)
- Is the result really a single number? (A "kpi" that returned 3 rows is not a
  KPI.)

When the nomination is invalid, deterministic rules take over: one numeric column
→ kpi; date + numeric → line; category + numeric → bar; else table.

---

## Dashboard builder (`app/dashboard_engine.py`)

### The spec

A dashboard is a JSON object: `{title, layout, filters[], widgets[]}`. Each
widget has an `id`, a `type`, a `title`, `sql` with filter placeholders, and
`x`/`y` column names. The renderer (`render_dashboard`) draws whatever this JSON
describes — it never changes, only the JSON does.

### Filter substitution

Filter values are converted to typed SQL literals by `sql_literal()` — dates
become `DATE '2026-04-01'`, strings are single-quoted with embedded quotes
doubled. The substituted SQL is re-checked by the guard before execution. An empty
multiselect produces `(NULL)` rather than a syntax error.

### Validation

`validate_spec()` checks structure, widget types, filter column references against
the real schema, and every widget's SQL through the guard — all before anything
renders. A malformed model response produces error messages and leaves the
previous dashboard intact.

### Conversational editing

The current spec + the user's instruction go to Cortex, which returns the full
updated spec (not a diff). At this scale a full rewrite is more reliable than
patch semantics. The prompt log is saved so a reloaded dashboard can be edited
further.

---

## Persistence

### Chat turns (`CHAT_MESSAGES`)

Every question is written to Snowflake immediately after it is answered. The
result set itself is not stored — only the SQL. Reopening a past answer re-runs
its query, so the figures are current rather than a stale snapshot.

Persistence is best-effort: a failed write is swallowed silently and never
interrupts the answer, because losing history is far less bad than losing the
answer the user is looking at.

### Dashboards (`DASHBOARDS`)

A dashboard is fully described by its spec (stored as `VARIANT`) and its prompt
log (also `VARIANT`). Because the renderer is generic, persisting the JSON is
enough to reconstruct the dashboard exactly, and because the prompt log is stored
with it, a reloaded dashboard is still conversationally editable.

### History tab

The third tab shows every past conversation grouped by session, with outcome
icons, table/chart/attempt metadata, and a "Run this again" button that re-executes
the stored SQL against current data (after re-checking it through the guard).

---

## What was built, in order

| Phase | What | Key files |
|---|---|---|
| 1 | Database, schema, six tables, seeded data, reconciliation check | `sql/01_schema.sql`, `sql/02_seed_data.sql` |
| 2 | Metadata JSON, vector embeddings, schema retrieval | `app/metadata.json`, `app/schema_text.py`, `app/metadata_rag.py`, `sql/03_metadata.sql` |
| 3 | SQL generation, repair prompt, safety guard, 11-question live test | `app/nl2sql.py`, `app/sql_guard.py`, `app/prompts/` |
| 4 | Chart builders, output type selection, case-insensitive axis resolution | `app/charts.py`, `app/output_picker.py` |
| 5 | Dashboard spec generation, validation, conversational editing, rendering | `app/dashboard_engine.py`, `app/prompts/dashboard_*.txt` |
| 6 | Streamlit app shell, `snowflake.yml`, workspace runtime compliance | `streamlit_app.py`, `snowflake.yml` |
| 7 | Iterative repair loop with schema-aware error hints | `app/pipeline.py`, `app/error_hints.py` |
| 8 | Persistence (chat and dashboards), History tab | `app/persistence.py`, `sql/04_persistence.sql` |

---

## Tests

| Test | What it proves | Snowflake needed? |
|---|---|---|
| `test_sql_guard.py` | 36 cases: 12 legitimate queries pass, 24 hostile or malformed statements are refused | No |
| `test_error_hints.py` | Diagnoses are precise: names the real column owner, the column lists, cross-table warnings | No |
| `test_output_picker.py` | Shape rules, Snowflake DATE handling, case-insensitive axis resolution, pie/bar downgrades | No |
| `test_dashboard_engine.py` | Spec validation, injection-safe literal escaping, undeclared-placeholder rejection | No |
| `test_persistence.py` | Chat turns and dashboard specs round-trip through Snowflake, update in place, delete cleanly | Yes |
| `test_nl2sql_live.py` | 11 questions end-to-end: retrieval → generation → guard → execution | Yes |
| `test_chart_wiring.py` | Every chart type the model nominates has resolved, existing axes in the result | Yes |
| `test_dashboard_live.py` | Build a dashboard, then edit it twice, executing every widget | Yes |
| `test_app_smoke.py` | The real app boots, answers, refuses writes, builds a dashboard, persists turns and dashboards | Yes |
| `smoke_test.py` | 11 questions including a refusal, used as a regression gate | Yes |

---

## Approach — what the code does that the roadmap did not

The roadmap described the architecture. Building it surfaced five problems that
the design did not predict, and each one left a permanent mark on the code:

1. **Retrieval was broken for the most important question.** "Net revenue by
   provider" ranked `TRANSACTIONS` outside the top 3. Splitting the embedded text
   (compact business summary) from the prompt text (full column detail) fixed it.

2. **Every chart silently rendered as a table.** The model names axes in
   `lower_snake_case`; Snowflake returns them `UPPERCASED`. Without
   case-insensitive resolution, `build_figure` returns `None` and the fallback is
   always a table. No test caught it because nothing raised.

3. **The repair loop was flying blind.** Snowflake puts the useful half of its
   errors on line 2. Sending only line 1 made every repair attempt fail. And even
   with the full error, telling the model "fix it" makes it guess the same wrong
   column again — it needs the column lists and a pattern for combining measures
   from different tables.

4. **Plotly is not in the default Workspace dependency set.** Adding it to
   `pyproject.toml` requires an external access integration for PyPI. Instead it
   is imported optionally, with native Streamlit chart fallbacks.

5. **Session state does not survive a reload.** Streamlit's `session_state` is
   lost on refresh, on a container restart, and whenever a second person opens the
   app. Chat turns are now written to `CHAT_MESSAGES` and dashboards to
   `DASHBOARDS` immediately, with the prompt log, so a reloaded dashboard is
   still conversationally editable.

---

## File layout

```
finbi_assistant/              the Streamlit in Snowflake project
├── snowflake.yml             entity definition + all 21 artifacts
├── pyproject.toml            unmodified Workspace default (no EAI needed)
├── streamlit_app.py          entry: Ask + Dashboard Builder + History tabs
├── .streamlit/config.toml
└── app/
    ├── metadata.json         table/column descriptions, metrics, aliases
    ├── schema_text.py        chunk builders (dependency-free, shared with tools/)
    ├── metadata_rag.py       embeddings + retrieval inside Snowflake
    ├── nl2sql.py             prompt building, SQL generation, repair
    ├── error_hints.py        turns a Snowflake error into a schema-aware diagnosis
    ├── sql_guard.py          the read-only safety gate (standalone, 36 test cases)
    ├── pipeline.py           one question end to end, with the iterative retry loop
    ├── output_picker.py      chart selection + one-sentence summarisation
    ├── charts.py             Plotly builders + native-Streamlit fallbacks
    ├── dashboard_engine.py   spec validation, generation, editing, generic rendering
    ├── persistence.py        durable storage for chat turns and saved dashboards
    ├── snowflake_utils.py    session, queries, Cortex calls, error summarisation
    └── prompts/              5 prompt templates (SQL, repair, summary, dashboard, edit)
sql/                          01 schema, 02 data, 03 metadata embeddings, 04 persistence
tests/                        10 test files (4 offline, 6 live)
tools/                        metadata SQL generator, smoke test
```
