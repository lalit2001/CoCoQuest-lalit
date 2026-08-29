-- Creates the FINBI_DEMO.CORE warehouse, schema, and the six card-payment tables.
-- Co-authored with CoCo

USE ROLE ACCOUNTADMIN;

CREATE WAREHOUSE IF NOT EXISTS FINBI_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

CREATE DATABASE IF NOT EXISTS FINBI_DEMO;
CREATE SCHEMA IF NOT EXISTS FINBI_DEMO.CORE;

USE WAREHOUSE FINBI_WH;
USE SCHEMA FINBI_DEMO.CORE;

-- ---------------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE CARD_MACHINE_ACCOUNTS (
    provider                 VARCHAR(40)   NOT NULL,
    merchant_id              VARCHAR(20)   NOT NULL,
    terminal_id              VARCHAR(20)   NOT NULL,
    status                   VARCHAR(20)   NOT NULL,
    contract_start           DATE          NOT NULL,
    contract_end             DATE,
    transaction_fee          NUMBER(6,4)   NOT NULL,
    monthly_fee              NUMBER(10,2)  NOT NULL,
    settlement_bank          VARCHAR(60)   NOT NULL,
    account_manager_contact  VARCHAR(80),
    CONSTRAINT pk_cma PRIMARY KEY (terminal_id)
)
COMMENT = 'One row per physical card machine (terminal) the business operates.';

CREATE OR REPLACE TABLE FEE_SCHEDULE (
    provider         VARCHAR(40)  NOT NULL,
    card_type        VARCHAR(20)  NOT NULL,
    transaction_fee  NUMBER(6,4)  NOT NULL,
    monthly_fee      NUMBER(10,2) NOT NULL,
    chargeback_fee   NUMBER(10,2) NOT NULL,
    fx_surcharge     NUMBER(6,4)  NOT NULL,
    CONSTRAINT pk_fee PRIMARY KEY (provider, card_type)
)
COMMENT = 'Contracted rate card per acquiring provider and card network.';

-- ---------------------------------------------------------------------------
-- Fact tables
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TABLE TRANSACTIONS (
    transaction_id   VARCHAR(24)    NOT NULL,
    direction        VARCHAR(10)    NOT NULL,
    txn_datetime     TIMESTAMP_NTZ  NOT NULL,
    value_date       DATE           NOT NULL,
    provider         VARCHAR(40)    NOT NULL,
    terminal_id      VARCHAR(20)    NOT NULL,
    merchant_id      VARCHAR(20)    NOT NULL,
    card_type        VARCHAR(20)    NOT NULL,
    card_last4       VARCHAR(4)     NOT NULL,
    auth_code        VARCHAR(10),
    category         VARCHAR(30)    NOT NULL,
    gross_amount     NUMBER(12,2)   NOT NULL,
    tip              NUMBER(12,2)   NOT NULL DEFAULT 0,
    total_charged    NUMBER(12,2)   NOT NULL,
    fee              NUMBER(12,2)   NOT NULL,
    net_amount       NUMBER(12,2)   NOT NULL,
    currency         VARCHAR(3)     NOT NULL DEFAULT 'INR',
    status           VARCHAR(20)    NOT NULL,
    settlement_date  DATE,
    notes            VARCHAR(200),
    CONSTRAINT pk_txn PRIMARY KEY (transaction_id)
)
COMMENT = 'One row per card transaction captured at a terminal.';

CREATE OR REPLACE TABLE DISPUTES (
    dispute_id         VARCHAR(24)   NOT NULL,
    orig_txn_id        VARCHAR(24)   NOT NULL,
    date_raised        DATE          NOT NULL,
    response_deadline  DATE          NOT NULL,
    date_resolved      DATE,
    provider           VARCHAR(40)   NOT NULL,
    terminal_id        VARCHAR(20)   NOT NULL,
    merchant_id        VARCHAR(20)   NOT NULL,
    card_type          VARCHAR(20)   NOT NULL,
    card_last4         VARCHAR(4)    NOT NULL,
    dispute_reason     VARCHAR(40)   NOT NULL,
    description        VARCHAR(300),
    disputed_amount    NUMBER(12,2)  NOT NULL,
    chargeback_fee     NUMBER(10,2)  NOT NULL,
    outcome            VARCHAR(20)   NOT NULL,
    evidence_provided  BOOLEAN       NOT NULL,
    cb_debited         BOOLEAN       NOT NULL,
    amount_recovered   NUMBER(12,2)  NOT NULL DEFAULT 0,
    notes              VARCHAR(300),
    CONSTRAINT pk_dispute PRIMARY KEY (dispute_id),
    CONSTRAINT fk_dispute_txn FOREIGN KEY (orig_txn_id) REFERENCES TRANSACTIONS(transaction_id)
)
COMMENT = 'One row per cardholder dispute / chargeback raised against a transaction.';

CREATE OR REPLACE TABLE SETTLEMENTS (
    settlement_id       VARCHAR(24)   NOT NULL,
    provider            VARCHAR(40)   NOT NULL,
    terminal_id         VARCHAR(20)   NOT NULL,
    merchant_id         VARCHAR(20)   NOT NULL,
    settlement_date     DATE          NOT NULL,
    batch_txn_count     NUMBER(8,0)   NOT NULL,
    gross_amount        NUMBER(14,2)  NOT NULL,
    total_fees          NUMBER(14,2)  NOT NULL,
    net_settled_amount  NUMBER(14,2)  NOT NULL,
    bank_account        VARCHAR(40)   NOT NULL,
    payout_reference    VARCHAR(30)   NOT NULL,
    status              VARCHAR(20)   NOT NULL,
    CONSTRAINT pk_settlement PRIMARY KEY (settlement_id)
)
COMMENT = 'One row per payout batch: settled transactions grouped by provider, terminal and settlement date.';

CREATE OR REPLACE TABLE LEDGER (
    ledger_entry_id  VARCHAR(24)    NOT NULL,
    entry_date       DATE           NOT NULL,
    account_code     VARCHAR(10)    NOT NULL,
    account_name     VARCHAR(60)    NOT NULL,
    debit            NUMBER(14,2)   NOT NULL DEFAULT 0,
    credit           NUMBER(14,2)   NOT NULL DEFAULT 0,
    source_type      VARCHAR(20)    NOT NULL,
    source_id        VARCHAR(24)    NOT NULL,
    description      VARCHAR(200),
    running_balance  NUMBER(16,2),
    CONSTRAINT pk_ledger PRIMARY KEY (ledger_entry_id)
)
COMMENT = 'Simplified double-entry general ledger derived from transactions, disputes and settlements.';
