-- Seeds the reference tables and generates internally consistent transactions, disputes, settlements and ledger entries.
-- Co-authored with CoCo

USE WAREHOUSE FINBI_WH;
USE SCHEMA FINBI_DEMO.CORE;

-- ---------------------------------------------------------------------------
-- 1. Reference data (hand-authored, 5 terminals across 3 providers)
-- ---------------------------------------------------------------------------

TRUNCATE TABLE CARD_MACHINE_ACCOUNTS;
INSERT INTO CARD_MACHINE_ACCOUNTS
    (provider, merchant_id, terminal_id, status, contract_start, contract_end,
     transaction_fee, monthly_fee, settlement_bank, account_manager_contact)
VALUES
    ('RAZORPAY', 'MID10021', 'TERM-RZP-01', 'ACTIVE',   '2024-04-01', '2027-03-31', 0.0180, 499.00, 'HDFC Bank',  'priya.nair@razorpay.example'),
    ('RAZORPAY', 'MID10021', 'TERM-RZP-02', 'ACTIVE',   '2025-01-15', '2028-01-14', 0.0180, 499.00, 'HDFC Bank',  'priya.nair@razorpay.example'),
    ('PINELABS', 'MID20044', 'TERM-PIN-01', 'ACTIVE',   '2024-07-01', '2027-06-30', 0.0175, 399.00, 'ICICI Bank', 'r.deshmukh@pinelabs.example'),
    ('PINELABS', 'MID20044', 'TERM-PIN-02', 'INACTIVE', '2024-07-01', '2026-06-30', 0.0175, 399.00, 'ICICI Bank', 'r.deshmukh@pinelabs.example'),
    ('PAYTM',    'MID30077', 'TERM-PTM-01', 'ACTIVE',   '2025-03-01', '2028-02-29', 0.0195, 599.00, 'Axis Bank',  'support.msme@paytm.example');

TRUNCATE TABLE FEE_SCHEDULE;
INSERT INTO FEE_SCHEDULE
    (provider, card_type, transaction_fee, monthly_fee, chargeback_fee, fx_surcharge)
VALUES
    ('RAZORPAY', 'VISA',       0.0180, 499.00, 350.00, 0.0350),
    ('RAZORPAY', 'MASTERCARD', 0.0185, 499.00, 350.00, 0.0350),
    ('RAZORPAY', 'RUPAY',      0.0090, 499.00, 250.00, 0.0000),
    ('PINELABS', 'VISA',       0.0175, 399.00, 300.00, 0.0325),
    ('PINELABS', 'MASTERCARD', 0.0180, 399.00, 300.00, 0.0325),
    ('PINELABS', 'RUPAY',      0.0085, 399.00, 200.00, 0.0000),
    ('PAYTM',    'VISA',       0.0195, 599.00, 400.00, 0.0375),
    ('PAYTM',    'MASTERCARD', 0.0200, 599.00, 400.00, 0.0375),
    ('PAYTM',    'RUPAY',      0.0100, 599.00, 300.00, 0.0000);

-- ---------------------------------------------------------------------------
-- 2. TRANSACTIONS (350 rows, Jan 1 - Aug 15 2026, seeded so it is reproducible)
--    REFUND rows carry a negative gross_amount so SUM(net_amount) is true net
--    revenue. FAILED rows carry no fee, no net and no settlement date.
-- ---------------------------------------------------------------------------

TRUNCATE TABLE TRANSACTIONS;
INSERT INTO TRANSACTIONS
    (transaction_id, direction, txn_datetime, value_date, provider, terminal_id,
     merchant_id, card_type, card_last4, auth_code, category, gross_amount, tip,
     total_charged, fee, net_amount, currency, status, settlement_date, notes)
WITH acct AS (
    SELECT provider, merchant_id, terminal_id,
           ROW_NUMBER() OVER (ORDER BY terminal_id) AS tno
    FROM CARD_MACHINE_ACCOUNTS
),
gen AS (
    SELECT
        SEQ4() + 1                                                        AS rn,
        UNIFORM(1, 5, RANDOM(101))                                        AS tno,
        UNIFORM(1, 3, RANDOM(102))                                        AS card_no,
        UNIFORM(1, 5, RANDOM(103))                                        AS cat_no,
        UNIFORM(150, 4500, RANDOM(104))                                   AS gross,
        UNIFORM(0, 99, RANDOM(105))                                       AS tip_roll,
        UNIFORM(0, 999, RANDOM(106))                                      AS status_roll,
        UNIFORM(0, 999, RANDOM(107))                                      AS dir_roll,
        UNIFORM(0, 19353600, RANDOM(108))                                 AS sec_off,
        UNIFORM(0, 9999, RANDOM(109))                                     AS l4,
        UNIFORM(1, 3, RANDOM(110))                                        AS lag_days
    FROM TABLE(GENERATOR(ROWCOUNT => 350))
),
shaped AS (
    SELECT
        'TXN' || LPAD(g.rn::VARCHAR, 6, '0')                              AS transaction_id,
        CASE WHEN g.dir_roll < 60 THEN 'REFUND' ELSE 'SALE' END           AS direction,
        DATEADD(second, g.sec_off, '2026-01-01 08:00:00'::TIMESTAMP_NTZ)  AS txn_datetime,
        a.provider, a.terminal_id, a.merchant_id,
        CASE g.card_no WHEN 1 THEN 'VISA' WHEN 2 THEN 'MASTERCARD'
                       ELSE 'RUPAY' END                                   AS card_type,
        LPAD(g.l4::VARCHAR, 4, '0')                                       AS card_last4,
        CASE g.cat_no WHEN 1 THEN 'DINE_IN' WHEN 2 THEN 'TAKEAWAY'
                      WHEN 3 THEN 'DELIVERY' WHEN 4 THEN 'MERCHANDISE'
                      ELSE 'CATERING' END                                 AS category,
        g.gross                                                           AS raw_gross,
        g.tip_roll, g.cat_no, g.status_roll, g.lag_days
    FROM gen g
    JOIN acct a ON a.tno = g.tno
),
signed AS (
    SELECT
        s.*,
        CASE WHEN s.direction = 'REFUND' THEN -s.raw_gross
             ELSE s.raw_gross END::NUMBER(12,2)                           AS gross_amount,
        CASE WHEN s.direction = 'SALE' AND s.cat_no = 1 AND s.tip_roll < 45
             THEN ROUND(s.raw_gross * 0.05, 2) ELSE 0 END::NUMBER(12,2)   AS tip,
        CASE WHEN s.status_roll < 880 THEN 'SETTLED'
             WHEN s.status_roll < 950 THEN 'PENDING'
             ELSE 'FAILED' END                                            AS status
    FROM shaped s
),
priced AS (
    SELECT
        sg.*,
        (sg.gross_amount + sg.tip)::NUMBER(12,2)                          AS total_charged,
        CASE WHEN sg.status = 'FAILED' THEN 0
             ELSE ROUND(ABS(sg.gross_amount + sg.tip) * f.transaction_fee, 2)
        END::NUMBER(12,2)                                                 AS fee
    FROM signed sg
    JOIN FEE_SCHEDULE f
      ON f.provider = sg.provider AND f.card_type = sg.card_type
)
SELECT
    p.transaction_id,
    p.direction,
    p.txn_datetime,
    p.txn_datetime::DATE                                                  AS value_date,
    p.provider,
    p.terminal_id,
    p.merchant_id,
    p.card_type,
    p.card_last4,
    UPPER(SUBSTR(MD5(p.transaction_id), 1, 6))                            AS auth_code,
    p.category,
    p.gross_amount,
    p.tip,
    p.total_charged,
    p.fee,
    CASE WHEN p.status = 'FAILED' THEN 0
         ELSE (p.total_charged - p.fee) END::NUMBER(12,2)                 AS net_amount,
    'INR'                                                                 AS currency,
    p.status,
    CASE WHEN p.status = 'SETTLED'
         THEN DATEADD(day, p.lag_days, p.txn_datetime::DATE) END          AS settlement_date,
    CASE WHEN p.status = 'FAILED'  THEN 'Authorisation declined by issuer'
         WHEN p.direction = 'REFUND' THEN 'Customer refund processed at terminal'
         WHEN p.status = 'PENDING' THEN 'Awaiting provider settlement batch'
    END                                                                   AS notes
FROM priced p;

-- ---------------------------------------------------------------------------
-- 3. DISPUTES (47 rows) - each references a real settled SALE transaction
-- ---------------------------------------------------------------------------

TRUNCATE TABLE DISPUTES;
INSERT INTO DISPUTES
    (dispute_id, orig_txn_id, date_raised, response_deadline, date_resolved,
     provider, terminal_id, merchant_id, card_type, card_last4, dispute_reason,
     description, disputed_amount, chargeback_fee, outcome, evidence_provided,
     cb_debited, amount_recovered, notes)
WITH cand AS (
    SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.transaction_id)) AS pick
    FROM TRANSACTIONS t
    WHERE t.status = 'SETTLED' AND t.direction = 'SALE' AND t.total_charged > 600
),
picked AS (
    SELECT * FROM cand WHERE pick <= 47
),
rolled AS (
    SELECT
        p.*,
        UNIFORM(1, 5, RANDOM(201))   AS reason_no,
        UNIFORM(0, 999, RANDOM(202)) AS outcome_roll,
        UNIFORM(5, 40, RANDOM(203))  AS raise_lag,
        UNIFORM(15, 45, RANDOM(204)) AS resolve_lag,
        UNIFORM(0, 99, RANDOM(205))  AS ev_roll
    FROM picked p
),
decided AS (
    SELECT
        r.*,
        CASE WHEN r.outcome_roll < 400 THEN 'WON'
             WHEN r.outcome_roll < 780 THEN 'LOST'
             ELSE 'PENDING' END      AS outcome,
        (r.ev_roll < 70)             AS evidence_provided,
        CASE r.reason_no
            WHEN 1 THEN 'FRAUD_UNAUTHORISED'
            WHEN 2 THEN 'PRODUCT_NOT_RECEIVED'
            WHEN 3 THEN 'DUPLICATE_CHARGE'
            WHEN 4 THEN 'INCORRECT_AMOUNT'
            ELSE 'SERVICE_QUALITY' END AS dispute_reason
    FROM rolled r
)
SELECT
    'DSP' || LPAD(ROW_NUMBER() OVER (ORDER BY d.date_raised_calc, d.transaction_id)::VARCHAR, 5, '0') AS dispute_id,
    d.transaction_id                                                      AS orig_txn_id,
    d.date_raised_calc                                                    AS date_raised,
    DATEADD(day, 14, d.date_raised_calc)                                  AS response_deadline,
    CASE WHEN d.outcome <> 'PENDING'
         THEN DATEADD(day, d.resolve_lag, d.date_raised_calc) END         AS date_resolved,
    d.provider, d.terminal_id, d.merchant_id, d.card_type, d.card_last4,
    d.dispute_reason,
    CASE d.dispute_reason
        WHEN 'FRAUD_UNAUTHORISED'   THEN 'Cardholder states the card was not present and the charge was not authorised by them.'
        WHEN 'PRODUCT_NOT_RECEIVED' THEN 'Cardholder claims the ordered items were never delivered.'
        WHEN 'DUPLICATE_CHARGE'     THEN 'Cardholder was billed twice for a single order at the same terminal.'
        WHEN 'INCORRECT_AMOUNT'     THEN 'Amount captured at the terminal differs from the amount on the printed bill.'
        ELSE 'Cardholder disputes the quality of the service delivered and requested a reversal.'
    END                                                                   AS description,
    d.total_charged                                                       AS disputed_amount,
    f.chargeback_fee,
    d.outcome,
    d.evidence_provided,
    TRUE                                                                  AS cb_debited,
    CASE WHEN d.outcome = 'WON' THEN d.total_charged ELSE 0 END::NUMBER(12,2) AS amount_recovered,
    CASE d.outcome
        WHEN 'WON'     THEN 'Represented successfully; funds returned by acquirer.'
        WHEN 'LOST'    THEN 'Chargeback upheld in favour of the cardholder.'
        ELSE 'Awaiting issuer decision.'
    END                                                                   AS notes
FROM (
    SELECT dd.*, DATEADD(day, dd.raise_lag, dd.value_date) AS date_raised_calc
    FROM decided dd
) d
JOIN FEE_SCHEDULE f
  ON f.provider = d.provider AND f.card_type = d.card_type;

-- ---------------------------------------------------------------------------
-- 4. SETTLEMENTS - derived: settled transactions grouped into payout batches
--    by provider + terminal + settlement_date.
-- ---------------------------------------------------------------------------

TRUNCATE TABLE SETTLEMENTS;
INSERT INTO SETTLEMENTS
    (settlement_id, provider, terminal_id, merchant_id, settlement_date,
     batch_txn_count, gross_amount, total_fees, net_settled_amount,
     bank_account, payout_reference, status)
WITH batched AS (
    SELECT
        t.provider, t.terminal_id, t.merchant_id, t.settlement_date,
        COUNT(*)                          AS batch_txn_count,
        SUM(t.total_charged)::NUMBER(14,2) AS gross_amount,
        SUM(t.fee)::NUMBER(14,2)           AS total_fees,
        SUM(t.net_amount)::NUMBER(14,2)    AS net_settled_amount
    FROM TRANSACTIONS t
    WHERE t.status = 'SETTLED'
    GROUP BY t.provider, t.terminal_id, t.merchant_id, t.settlement_date
)
SELECT
    'STL' || LPAD(ROW_NUMBER() OVER (ORDER BY b.settlement_date, b.terminal_id)::VARCHAR, 5, '0') AS settlement_id,
    b.provider, b.terminal_id, b.merchant_id, b.settlement_date,
    b.batch_txn_count, b.gross_amount, b.total_fees, b.net_settled_amount,
    a.settlement_bank || ' ****' || SUBSTR(MD5(b.terminal_id), 1, 4)       AS bank_account,
    'PO-' || SUBSTR(b.provider, 1, 3) || '-' ||
        TO_VARCHAR(b.settlement_date, 'YYYYMMDD') || '-' ||
        SUBSTR(MD5(b.terminal_id || b.settlement_date::VARCHAR), 1, 4)     AS payout_reference,
    CASE WHEN b.settlement_date <= '2026-08-10'::DATE THEN 'PAID'
         ELSE 'PENDING' END                                               AS status
FROM batched b
JOIN CARD_MACHINE_ACCOUNTS a ON a.terminal_id = b.terminal_id;

-- ---------------------------------------------------------------------------
-- 5. LEDGER - derived double-entry postings.
--    running_balance is the cumulative (debit - credit) PER ACCOUNT, ordered by
--    entry_date, which is how a real general ledger account balance behaves.
-- ---------------------------------------------------------------------------

TRUNCATE TABLE LEDGER;
INSERT INTO LEDGER
    (ledger_entry_id, entry_date, account_code, account_name, debit, credit,
     source_type, source_id, description, running_balance)
WITH txn_entries AS (
    -- Settled transaction: receivable + processing fee on the debit side,
    -- sales revenue on the credit side. Refunds reverse the sides.
    SELECT t.value_date AS entry_date, '1200' AS account_code, 'Card Receivable' AS account_name,
           GREATEST(t.net_amount, 0)::NUMBER(14,2) AS debit,
           GREATEST(-t.net_amount, 0)::NUMBER(14,2) AS credit,
           'TRANSACTION' AS source_type, t.transaction_id AS source_id,
           t.direction || ' ' || t.category || ' @ ' || t.terminal_id AS description, 1 AS ord
    FROM TRANSACTIONS t WHERE t.status = 'SETTLED'
    UNION ALL
    SELECT t.value_date, '5100', 'Card Processing Fees',
           t.fee::NUMBER(14,2), 0::NUMBER(14,2),
           'TRANSACTION', t.transaction_id,
           'Acquirer fee ' || t.provider || ' ' || t.card_type, 2
    FROM TRANSACTIONS t WHERE t.status = 'SETTLED' AND t.fee > 0
    UNION ALL
    SELECT t.value_date, '4000', 'Sales Revenue',
           GREATEST(-t.total_charged, 0)::NUMBER(14,2),
           GREATEST(t.total_charged, 0)::NUMBER(14,2),
           'TRANSACTION', t.transaction_id,
           t.direction || ' revenue ' || t.category, 3
    FROM TRANSACTIONS t WHERE t.status = 'SETTLED'
    UNION ALL
    -- Dispute: unrecovered amount becomes a loss, written off the receivable.
    SELECT d.date_raised, '5200', 'Chargeback Losses',
           (d.disputed_amount - d.amount_recovered)::NUMBER(14,2), 0::NUMBER(14,2),
           'DISPUTE', d.dispute_id,
           d.outcome || ' dispute (' || d.dispute_reason || ')', 4
    FROM DISPUTES d
    UNION ALL
    SELECT d.date_raised, '1200', 'Card Receivable',
           0::NUMBER(14,2), (d.disputed_amount - d.amount_recovered)::NUMBER(14,2),
           'DISPUTE', d.dispute_id,
           'Receivable written down for dispute ' || d.dispute_id, 5
    FROM DISPUTES d
    UNION ALL
    -- Settlement payout: cash in at the bank, receivable cleared.
    SELECT s.settlement_date, '1010', 'Bank Account',
           s.net_settled_amount, 0::NUMBER(14,2),
           'SETTLEMENT', s.settlement_id,
           'Payout ' || s.payout_reference, 6
    FROM SETTLEMENTS s
    UNION ALL
    SELECT s.settlement_date, '1200', 'Card Receivable',
           0::NUMBER(14,2), s.net_settled_amount,
           'SETTLEMENT', s.settlement_id,
           'Receivable cleared by payout ' || s.payout_reference, 7
    FROM SETTLEMENTS s
),
numbered AS (
    SELECT e.*,
           ROW_NUMBER() OVER (ORDER BY e.entry_date, e.ord, e.source_id) AS seq
    FROM txn_entries e
)
SELECT
    'GL' || LPAD(n.seq::VARCHAR, 7, '0')                                  AS ledger_entry_id,
    n.entry_date, n.account_code, n.account_name, n.debit, n.credit,
    n.source_type, n.source_id, n.description,
    SUM(n.debit - n.credit) OVER (
        PARTITION BY n.account_code
        ORDER BY n.entry_date, n.seq
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )::NUMBER(16,2)                                                       AS running_balance
FROM numbered n;
