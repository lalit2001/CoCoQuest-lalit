# Conversational BI Assistant

**Snowflake + CoCo Quest 2026 — Theme 2**

Ask financial questions about a card-payment business in plain English and get back a
direct answer, an automatically chosen chart, and — from a single prompt — a whole
dashboard you can then reshape by talking to it.

Everything runs inside Snowflake. SQL is written by Cortex, schema retrieval uses
Cortex embeddings stored in a Snowflake table, and the app is Streamlit in Snowflake
using the workspace's embedded identity. No data and no prompts leave the account.

## Demo Video

https://github.com/user-attachments/assets/demo_video.mp4

> **[Watch the demo](demo_video.mp4)** — a walkthrough showing the Ask tab,
> dashboard builder, magic AI popover, chart switching, and the iterative repair loop
> in action.

---

## Architecture

```
Streamlit in Snowflake (finbi_assistant/)
  │
  ├── Ask tab ──────────────► app/pipeline.answer_question()
  │                              │
  │   1. retrieve_relevant_tables()   VECTOR_COSINE_SIMILARITY over SCHEMA_METADATA
  │   2. generate_query_plan()        CORTEX.COMPLETE → {sql, chart_type, x, y}
  │   3. check_sql()                  read-only guard, refuses anything else
  │   4. run_query()                  Snowpark
  │   5. repair loop (up to 4 tries)  every failed attempt + a schema-aware
  │                                   diagnosis fed back until the query runs
  │   6. resolve_output()             verify the chart against the real result
  │   7. summarize_result()           CORTEX.COMPLETE → one-sentence answer
  │
  └── Dashboard tab ───────► app/dashboard_engine.py
         generate_dashboard_spec()    instruction  → validated JSON spec
         edit_dashboard_spec()        spec + edit  → full updated JSON spec
         render_dashboard(spec)       generic renderer, never changes
```

The dashboard is a **JSON specification**, not code. One prompt produces the spec; the
renderer draws whatever it describes; follow-up prompts rewrite the spec. That is why
*"now split that by provider and add a KPI for pending payouts"* reshapes the existing
dashboard instead of rebuilding it.

---

## Data model — `FINBI_DEMO.CORE`

Six related tables, not one flat file, so the assistant has to reason across joins.

| Table | Grain | Rows |
|---|---|---|
| `TRANSACTIONS` | one card transaction | 350 |
| `DISPUTES` | one chargeback, FK to `TRANSACTIONS` | 47 |
| `SETTLEMENTS` | one payout batch (provider + terminal + date) | 276 |
| `LEDGER` | one double-entry posting | 1,612 |
| `CARD_MACHINE_ACCOUNTS` | one terminal | 5 |
| `FEE_SCHEDULE` | one provider + card network | 9 |
| `SCHEMA_METADATA` | one embedded table summary (RAG index) | 6 |

`SETTLEMENTS` and `LEDGER` are **derived** from the transactions and disputes, so the
figures reconcile exactly:

```
SUM(SETTLEMENTS.net_settled_amount)                    = 600,384.07
SUM(TRANSACTIONS.net_amount) WHERE status = 'SETTLED'  = 600,384.07
LEDGER bank account (1010) balance                     = 600,384.07
SUM(LEDGER.debit) - SUM(LEDGER.credit)                 = 0.00
```

Conventions worth knowing (all documented in `finbi_assistant/app/metadata.json`, which
is what the model actually reads):

- `REFUND` rows carry **negative** amounts, so `SUM(net_amount)` is already true net revenue.
- `FAILED` transactions have no fee, no net amount and no settlement date.
- `LEDGER.running_balance` is cumulative **per account**, so any balance chart must
  filter to one `account_code`.

---

## Setup

Run once, in order, from a Snowsight worksheet or the CLI:

```bash
snow sql -f sql/01_schema.sql      # warehouse, database, schema, six tables
snow sql -f sql/02_seed_data.sql   # seeded, reproducible data + derived tables
snow sql -f sql/03_metadata.sql    # embeds metadata.json into SCHEMA_METADATA
```

Then open `finbi_assistant/` in a Snowflake Workspace and click **Run**.

`snowflake.yml` sets `query_warehouse: COMPUTE_WH` and
`compute_pool: SYSTEM_COMPUTE_POOL_CPU`. Change them in **App Settings** if your account
uses different resources.

`sql/03_metadata.sql` is generated. After editing `metadata.json`, regenerate it:

```bash
python3 tools/gen_metadata_sql.py && snow sql -f sql/03_metadata.sql
```

The app also self-heals: `ensure_embeddings()` fingerprints the metadata at startup and
re-embeds if the table is stale, so a forgotten regeneration cannot leave stale vectors.

### Dependencies

`pyproject.toml` is the **unmodified** Workspace default, so no external access
integration is required. Plotly is imported optionally — with it you get styled figures
and donut charts, without it every visual falls back to a native Streamlit chart.

---

## Demo script

| # | Say this | What to point at |
|---|---|---|
| 1 | *What's our net revenue by provider this year?* | Bar chart + a one-sentence INR answer. Open **How this was answered** to show the generated SQL and which tables were retrieved. |
| 2 | *Which disputes are we most likely to lose?* | Loss rate by dispute reason — the model picked the metric, not just the columns. |
| 3 | *Show me the ledger balance trend since April* | Line chart from `LEDGER.running_balance`, correctly filtered to `account_code = '1010'`. |
| 4 | *Build me a dashboard on settlement reliability* | Switch tabs. One sentence → KPI + bar + line + table, with date and provider filters. Open the JSON expander. |
| 5 | *Now split that by provider and add a KPI for pending payouts* | Same dashboard, reshaped. The JSON changed; the renderer did not. |
| 6 | *Delete all transactions* | Refused, with an explanation. Then show `tests/test_sql_guard.py` — 24 hostile statements rejected. |

---

## Safety

Two independent layers, because either alone is insufficient.

**1. The prompt** instructs the model to answer a write request with a fixed sentinel
rather than SQL. `app/pipeline.py` detects that sentinel and raises `UnsafeQueryError`,
which the UI shows as a refusal.

**2. The guard** (`app/sql_guard.py`, no Streamlit or Snowflake imports, so it is
unit-testable on its own) validates *every* statement before execution — in the chat path
and in every dashboard widget:

- must begin with `SELECT` or `WITH`
- exactly one statement — no stacked semicolons
- no SQL comments
- no write, DDL, permission or session keyword
- no unterminated string literal (which would hide the rest of the statement from the scan)

String-literal contents are blanked before scanning, so
`WHERE notes = 'customer asked to delete the charge'` passes while a real `DELETE` does not.

Dashboard filter values never reach SQL by concatenation: `sql_literal()` renders them as
typed literals (`DATE '2026-04-01'`, `'PAYTM'`, quotes doubled), and the substituted
statement is re-checked by the guard before it runs.

---

## Tests

```bash
python3 tests/test_sql_guard.py        # 36 cases, no Snowflake needed
python3 tests/test_error_hints.py      # repair diagnoses, no Snowflake needed
python3 tests/test_output_picker.py    # chart selection rules
python3 tests/test_dashboard_engine.py # spec validation + injection-safe filters
python3 tests/test_nl2sql_live.py      # 11 questions, real Cortex, real tables
python3 tests/test_dashboard_live.py   # build a dashboard, edit it twice, run every widget
python3 tests/test_app_smoke.py        # renders the real app via AppTest
python3 tools/smoke_test.py            # regression gate, includes the refusal case
```

All green as of the last run: 36/36 guard cases, 11/11 live questions first-try, dashboard
built and edited twice with every widget returning rows, and the app boots, answers,
refuses writes and builds a dashboard headlessly.

`tests/harness.py` swaps the embedded Streamlit connection for a CLI connection, so every
module is exercised headlessly against the real database — the tests run the same
`answer_question()` the app does.

---

## Why these choices

**Schema RAG, in-database.** Retrieval embeds a compact business-language summary per
table, not the column list. That matters: with the full column dump and the shared
conventions block included, *"net revenue by provider"* ranked `TRANSACTIONS` **outside
the top 3** — the boilerplate identical to every chunk flattened the similarity scores.
Removing it and adding business aliases moved `TRANSACTIONS` to rank 1 (0.51). All 11 test
questions now retrieve the right table within the top 3.

**Two models.** `llama3.1-8b` for summaries and cheap calls; `llama3.3-70b` for SQL and
dashboard specs. The 8B model measurably failed at join reasoning — ambiguous columns
across joined tables, `ORDER BY` on a non-grouped column, and joining a raw date to a
`DATE_TRUNC`'d month (which silently returns zero rows rather than erroring).

**An iterative repair loop, with a real diagnosis.** When Snowflake rejects a query the
pipeline retries up to `MAX_ATTEMPTS` (4) times, and each retry receives *every* previous
attempt plus a schema-aware explanation of what was wrong.

Two things were needed to make retrying actually work:

1. **Send the useful half of the error.** Snowflake puts `SQL compilation error:` on line 1
   and the actual cause (`ambiguous column name 'PROVIDER'`) on line 2, so
   `summarize_error()` joins and de-noises the whole message. Sending only line 1 made
   repairs fail blind.
2. **Tell it which columns exist.** A generic "fix it" makes the model guess the same wrong
   column again. Asked *"what is the fees and charges"* it wrote `SUM(fee) FROM
   FEE_SCHEDULE`; that column is on `TRANSACTIONS`, and two blind retries returned the
   identical error. `app/error_hints.py` now replies with the column's real owner, the
   actual column list of every offered table, near-miss suggestions, and — the part that
   finally closed it — a warning that measures spread across tables cannot go in one flat
   `SELECT`, with a `UNION ALL` example. That question now succeeds on attempt 2.

The loop is honest about giving up: after the last attempt it reports how many tries it
made and Snowflake's final error, and if the model concludes mid-repair that the tables
cannot answer the question it says so instead of guessing further. The chat's "How this was
answered" panel lists every failed attempt and its error, so the self-correction is visible
rather than hidden.

**Validate the model's chart choice.** The model nominates a chart alongside its SQL,
because it knows what its query returns. `resolve_output()` then checks that nomination
against the actual DataFrame and falls back to deterministic rules when it cannot be
honoured — a KPI that returned 3 rows, a pie with 12 slices, or axes naming columns that
are not in the result. It also matches axis names case-insensitively, since the model
echoes its `lower_snake_case` aliases while Snowflake returns them uppercased.

---

## Deviations from the original plan

All forced by targeting Streamlit in Snowflake:

- No `snowflake_conn.py` with env vars or `python-dotenv` — the connection is embedded
  via `st.connection("snowflake")`.
- No `sentence-transformers` or numpy cosine lookup — `EMBED_TEXT_768` +
  `VECTOR_COSINE_SIMILARITY` keep retrieval inside Snowflake.
- No `pydantic` — spec validation is hand-rolled, which also reports *all* problems at
  once and so makes the one-shot repair retry effective.
- No `tabulate` — `to_markdown()` raises `ImportError` in the deployed app; result
  previews use `to_string()`.
- No CSVs, stage or `COPY INTO` — the data is generated in SQL and reproducible from a seed.
