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

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'TRANSACTIONS',
       'TABLE TRANSACTIONS
Grain: One row per card transaction captured at a terminal.
Description: Card payment transactions for a small merchant across three acquiring providers and five terminals. Covers sales and refunds, the fee charged by the acquirer, and whether the money has been settled yet. This is the primary revenue table.
Columns: transaction_id, direction, txn_datetime, value_date, provider, terminal_id, merchant_id, card_type, card_last4, auth_code, category, gross_amount, tip, total_charged, fee, net_amount, currency, status, settlement_date, notes
Metrics this table answers:
  - net revenue = SUM(net_amount)
  - gross sales = SUM(total_charged) WHERE direction = ''SALE''
  - total fees paid = SUM(fee)
  - effective fee rate = SUM(fee) / SUM(ABS(total_charged))
  - failure rate = COUNT_IF(status = ''FAILED'') / COUNT(*)
  - refund rate = SUM(ABS(total_charged)) FILTER on direction=''REFUND'' / SUM(total_charged) FILTER on direction=''SALE''
  - average ticket size = AVG(total_charged) WHERE direction = ''SALE''
Questions this table answers:
  - What is our net revenue by provider this year?
  - Which sales category brings in the most money?
  - How much have we paid in card processing fees per month?
  - What is our transaction failure rate by terminal?
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE TRANSACTIONS
Grain: One row per card transaction captured at a terminal.
Description: Card payment transactions for a small merchant across three acquiring providers and five terminals. Covers sales and refunds, the fee charged by the acquirer, and whether the money has been settled yet. This is the primary revenue table.
Columns: transaction_id, direction, txn_datetime, value_date, provider, terminal_id, merchant_id, card_type, card_last4, auth_code, category, gross_amount, tip, total_charged, fee, net_amount, currency, status, settlement_date, notes
Metrics this table answers:
  - net revenue = SUM(net_amount)
  - gross sales = SUM(total_charged) WHERE direction = ''SALE''
  - total fees paid = SUM(fee)
  - effective fee rate = SUM(fee) / SUM(ABS(total_charged))
  - failure rate = COUNT_IF(status = ''FAILED'') / COUNT(*)
  - refund rate = SUM(ABS(total_charged)) FILTER on direction=''REFUND'' / SUM(total_charged) FILTER on direction=''SALE''
  - average ticket size = AVG(total_charged) WHERE direction = ''SALE''
Questions this table answers:
  - What is our net revenue by provider this year?
  - Which sales category brings in the most money?
  - How much have we paid in card processing fees per month?
  - What is our transaction failure rate by terminal?
');

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'DISPUTES',
       'TABLE DISPUTES
Grain: One row per cardholder dispute (chargeback) raised against a transaction.
Description: Chargebacks and disputes raised by cardholders. Tracks why the dispute was raised, the deadline to respond, whether the merchant submitted evidence, the outcome, and how much money was clawed back or recovered. Use this for chargeback performance, win rates and dispute losses.
Columns: dispute_id, orig_txn_id, date_raised, response_deadline, date_resolved, provider, terminal_id, merchant_id, card_type, card_last4, dispute_reason, description, disputed_amount, chargeback_fee, outcome, evidence_provided, cb_debited, amount_recovered, notes
Metrics this table answers:
  - dispute win rate = COUNT_IF(outcome = ''WON'') / COUNT_IF(outcome <> ''PENDING'')
  - net chargeback loss = SUM(disputed_amount - amount_recovered)
  - total chargeback fees = SUM(chargeback_fee)
  - open disputes = COUNT_IF(outcome = ''PENDING'')
  - overdue disputes = COUNT_IF(outcome = ''PENDING'' AND response_deadline < CURRENT_DATE())
  - days to resolve = AVG(DATEDIFF(''day'', date_raised, date_resolved))
  - dispute rate = COUNT(*) / (SELECT COUNT(*) FROM TRANSACTIONS)
Questions this table answers:
  - Which disputes are we most likely to lose?
  - What is our chargeback win rate by provider?
  - How much have we lost to chargebacks this year?
  - Does providing evidence actually improve our win rate?
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE DISPUTES
Grain: One row per cardholder dispute (chargeback) raised against a transaction.
Description: Chargebacks and disputes raised by cardholders. Tracks why the dispute was raised, the deadline to respond, whether the merchant submitted evidence, the outcome, and how much money was clawed back or recovered. Use this for chargeback performance, win rates and dispute losses.
Columns: dispute_id, orig_txn_id, date_raised, response_deadline, date_resolved, provider, terminal_id, merchant_id, card_type, card_last4, dispute_reason, description, disputed_amount, chargeback_fee, outcome, evidence_provided, cb_debited, amount_recovered, notes
Metrics this table answers:
  - dispute win rate = COUNT_IF(outcome = ''WON'') / COUNT_IF(outcome <> ''PENDING'')
  - net chargeback loss = SUM(disputed_amount - amount_recovered)
  - total chargeback fees = SUM(chargeback_fee)
  - open disputes = COUNT_IF(outcome = ''PENDING'')
  - overdue disputes = COUNT_IF(outcome = ''PENDING'' AND response_deadline < CURRENT_DATE())
  - days to resolve = AVG(DATEDIFF(''day'', date_raised, date_resolved))
  - dispute rate = COUNT(*) / (SELECT COUNT(*) FROM TRANSACTIONS)
Questions this table answers:
  - Which disputes are we most likely to lose?
  - What is our chargeback win rate by provider?
  - How much have we lost to chargebacks this year?
  - Does providing evidence actually improve our win rate?
');

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'SETTLEMENTS',
       'TABLE SETTLEMENTS
Grain: One row per payout batch (provider + terminal + settlement_date).
Description: Money actually paid out to the merchant''s bank account. Each row aggregates all settled transactions for one terminal on one settlement date. Use this for payout reliability, pending payouts and cash-in timing.
Columns: settlement_id, provider, terminal_id, merchant_id, settlement_date, batch_txn_count, gross_amount, total_fees, net_settled_amount, bank_account, payout_reference, status
Metrics this table answers:
  - total settled = SUM(net_settled_amount)
  - pending payout value = SUM(net_settled_amount) WHERE status = ''PENDING''
  - pending payout count = COUNT_IF(status = ''PENDING'')
  - average batch size = AVG(batch_txn_count)
  - fee drag per batch = SUM(total_fees) / SUM(gross_amount)
  - settlement reliability = COUNT_IF(status = ''PAID'') / COUNT(*)
Questions this table answers:
  - Which settlement batches are still pending?
  - How much is owed to us in unpaid payouts?
  - What is the average payout size by provider?
  - Show settled amount per month.
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE SETTLEMENTS
Grain: One row per payout batch (provider + terminal + settlement_date).
Description: Money actually paid out to the merchant''s bank account. Each row aggregates all settled transactions for one terminal on one settlement date. Use this for payout reliability, pending payouts and cash-in timing.
Columns: settlement_id, provider, terminal_id, merchant_id, settlement_date, batch_txn_count, gross_amount, total_fees, net_settled_amount, bank_account, payout_reference, status
Metrics this table answers:
  - total settled = SUM(net_settled_amount)
  - pending payout value = SUM(net_settled_amount) WHERE status = ''PENDING''
  - pending payout count = COUNT_IF(status = ''PENDING'')
  - average batch size = AVG(batch_txn_count)
  - fee drag per batch = SUM(total_fees) / SUM(gross_amount)
  - settlement reliability = COUNT_IF(status = ''PAID'') / COUNT(*)
Questions this table answers:
  - Which settlement batches are still pending?
  - How much is owed to us in unpaid payouts?
  - What is the average payout size by provider?
  - Show settled amount per month.
');

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'LEDGER',
       'TABLE LEDGER
Grain: One row per debit-or-credit posting.
Description: Simplified double-entry general ledger derived from transactions, disputes and settlements. Total debits equal total credits. Use this to trace the accounting impact of any business event, and to chart account balances over time.
Columns: ledger_entry_id, entry_date, account_code, account_name, debit, credit, source_type, source_id, description, running_balance
Metrics this table answers:
  - bank balance trend = SELECT entry_date, MAX(running_balance) FROM LEDGER WHERE account_code = ''1010'' GROUP BY entry_date ORDER BY entry_date
  - total processing fee expense = SUM(debit) WHERE account_code = ''5100''
  - total chargeback loss expense = SUM(debit) WHERE account_code = ''5200''
  - revenue recognised = SUM(credit) - SUM(debit) WHERE account_code = ''4000''
  - outstanding receivable = SUM(debit) - SUM(credit) WHERE account_code = ''1200''
  - ledger is balanced when SUM(debit) = SUM(credit)
Questions this table answers:
  - Show me the bank account balance trend since April.
  - What was the ledger impact of disputes lost in May?
  - How much sits in card receivable right now?
  - Break down our expense accounts by month.
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE LEDGER
Grain: One row per debit-or-credit posting.
Description: Simplified double-entry general ledger derived from transactions, disputes and settlements. Total debits equal total credits. Use this to trace the accounting impact of any business event, and to chart account balances over time.
Columns: ledger_entry_id, entry_date, account_code, account_name, debit, credit, source_type, source_id, description, running_balance
Metrics this table answers:
  - bank balance trend = SELECT entry_date, MAX(running_balance) FROM LEDGER WHERE account_code = ''1010'' GROUP BY entry_date ORDER BY entry_date
  - total processing fee expense = SUM(debit) WHERE account_code = ''5100''
  - total chargeback loss expense = SUM(debit) WHERE account_code = ''5200''
  - revenue recognised = SUM(credit) - SUM(debit) WHERE account_code = ''4000''
  - outstanding receivable = SUM(debit) - SUM(credit) WHERE account_code = ''1200''
  - ledger is balanced when SUM(debit) = SUM(credit)
Questions this table answers:
  - Show me the bank account balance trend since April.
  - What was the ledger impact of disputes lost in May?
  - How much sits in card receivable right now?
  - Break down our expense accounts by month.
');

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'CARD_MACHINE_ACCOUNTS',
       'TABLE CARD_MACHINE_ACCOUNTS
Grain: One row per card machine (terminal).
Description: Reference table describing each physical card machine the merchant operates: which provider supplied it, its contract dates and rates, whether it is still active, and which bank it settles into.
Columns: provider, merchant_id, terminal_id, status, contract_start, contract_end, transaction_fee, monthly_fee, settlement_bank, account_manager_contact
Metrics this table answers:
  - active terminals = COUNT_IF(status = ''ACTIVE'')
  - total monthly rental = SUM(monthly_fee) WHERE status = ''ACTIVE''
  - contracts expiring within 180 days = COUNT_IF(contract_end < DATEADD(''day'', 180, CURRENT_DATE()))
Questions this table answers:
  - Which terminals are inactive?
  - What do we pay in monthly machine rental?
  - Which contracts expire in the next six months?
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE CARD_MACHINE_ACCOUNTS
Grain: One row per card machine (terminal).
Description: Reference table describing each physical card machine the merchant operates: which provider supplied it, its contract dates and rates, whether it is still active, and which bank it settles into.
Columns: provider, merchant_id, terminal_id, status, contract_start, contract_end, transaction_fee, monthly_fee, settlement_bank, account_manager_contact
Metrics this table answers:
  - active terminals = COUNT_IF(status = ''ACTIVE'')
  - total monthly rental = SUM(monthly_fee) WHERE status = ''ACTIVE''
  - contracts expiring within 180 days = COUNT_IF(contract_end < DATEADD(''day'', 180, CURRENT_DATE()))
Questions this table answers:
  - Which terminals are inactive?
  - What do we pay in monthly machine rental?
  - Which contracts expire in the next six months?
');

INSERT INTO SCHEMA_METADATA (table_name, chunk_text, fingerprint, embedding)
SELECT 'FEE_SCHEDULE',
       'TABLE FEE_SCHEDULE
Grain: One row per provider + card network combination.
Description: Contracted rate card. Defines the per-transaction rate, monthly fee, flat chargeback fee and foreign-exchange surcharge for each provider and card network. Use this to compare providers on price or to check whether transactions were charged correctly.
Columns: provider, card_type, transaction_fee, monthly_fee, chargeback_fee, fx_surcharge
Metrics this table answers:
  - cheapest provider per network = MIN(transaction_fee) GROUP BY card_type
  - rate spread = MAX(transaction_fee) - MIN(transaction_fee)
  - blended contracted rate = AVG(transaction_fee)
Questions this table answers:
  - Which provider charges us the most per transaction?
  - Compare RUPAY rates across providers.
  - What is the chargeback fee for each provider?
',
       '55248178cb4cf425693c1de49aaa43a0a025ebeffc24c1727eacacf876534135',
       SNOWFLAKE.CORTEX.EMBED_TEXT_768('snowflake-arctic-embed-m', 'TABLE FEE_SCHEDULE
Grain: One row per provider + card network combination.
Description: Contracted rate card. Defines the per-transaction rate, monthly fee, flat chargeback fee and foreign-exchange surcharge for each provider and card network. Use this to compare providers on price or to check whether transactions were charged correctly.
Columns: provider, card_type, transaction_fee, monthly_fee, chargeback_fee, fx_surcharge
Metrics this table answers:
  - cheapest provider per network = MIN(transaction_fee) GROUP BY card_type
  - rate spread = MAX(transaction_fee) - MIN(transaction_fee)
  - blended contracted rate = AVG(transaction_fee)
Questions this table answers:
  - Which provider charges us the most per transaction?
  - Compare RUPAY rates across providers.
  - What is the chargeback fee for each provider?
');
