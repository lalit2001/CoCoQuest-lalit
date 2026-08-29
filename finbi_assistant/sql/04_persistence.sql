-- Creates the tables that persist chat history and saved dashboards across sessions.
-- Co-authored with CoCo

USE WAREHOUSE FINBI_WH;
USE SCHEMA FINBI_DEMO.CORE;

-- ---------------------------------------------------------------------------
-- CHAT_MESSAGES - one row per assistant turn.
--
-- The result set itself is deliberately NOT stored. Only the SQL is kept, so
-- reopening a past answer re-runs the query and shows current figures rather
-- than a stale snapshot. It also keeps this table small regardless of how large
-- the answers were.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS CHAT_MESSAGES (
    message_id    VARCHAR(36)    NOT NULL,
    session_id    VARCHAR(36)    NOT NULL,
    user_name     VARCHAR(120)   NOT NULL,
    turn_index    NUMBER(6,0)    NOT NULL,
    asked_at      TIMESTAMP_LTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    question      VARCHAR         NOT NULL,
    outcome       VARCHAR(20)     NOT NULL,  -- answered | refused | unanswerable | failed
    summary       VARCHAR,
    sql_text      VARCHAR,
    chart_type    VARCHAR(20),
    x_column      VARCHAR(120),
    y_column      VARCHAR(120),
    tables_used   VARCHAR(400),
    attempts      NUMBER(4,0),
    row_count     NUMBER(12,0),
    detail        VARCHAR,                   -- refusal reason or error message
    CONSTRAINT pk_chat_messages PRIMARY KEY (message_id)
)
COMMENT = 'Persisted chat turns. Stores the generated SQL, not the result set, so history re-runs against live data.';

-- ---------------------------------------------------------------------------
-- DASHBOARDS - one row per saved dashboard spec.
--
-- The spec is the whole artifact: the renderer is generic, so storing the JSON
-- is enough to reconstruct the dashboard exactly. prompt_log keeps the
-- conversation that produced it, which is what makes a saved dashboard editable
-- again rather than frozen.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DASHBOARDS (
    dashboard_id  VARCHAR(36)    NOT NULL,
    user_name     VARCHAR(120)   NOT NULL,
    name          VARCHAR(200)   NOT NULL,
    spec          VARIANT        NOT NULL,
    prompt_log    VARIANT,
    widget_count  NUMBER(4,0),
    created_at    TIMESTAMP_LTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    updated_at    TIMESTAMP_LTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_dashboards PRIMARY KEY (dashboard_id)
)
COMMENT = 'Saved dashboard specifications. The generic renderer rebuilds the dashboard from the spec alone.';
