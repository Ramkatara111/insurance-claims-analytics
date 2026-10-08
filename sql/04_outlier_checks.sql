-- ============================================================
-- 04_outlier_checks.sql
-- Statistical OUTLIER checks on claim amounts.
--
-- These are NOT the fraud indicator. In this dataset claim amounts are not
-- extreme relative to the portfolio, so these rules flag almost nothing and
-- do not detect the reported fraud (see analytics.outlier_fraud_metrics).
-- The fraud indicator is the rule-based score in 05_fraud_risk_score.sql.
--
-- A repeat-claimant rule is intentionally NOT included: the dataset has
-- exactly one claim per policy, so it could never fire.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS analytics;

-- Clean up views from the earlier anomaly layer, if present
DROP VIEW IF EXISTS analytics.anomaly_fraud_metrics CASCADE;
DROP VIEW IF EXISTS analytics.anomaly_flags CASCADE;
DROP VIEW IF EXISTS analytics.anomaly_repeat_claimant CASCADE;
DROP VIEW IF EXISTS analytics.anomaly_iqr CASCADE;
DROP VIEW IF EXISTS analytics.anomaly_mean_3sd CASCADE;

-- ============================================================
-- CHECK 1: Mean + 3 standard deviations (portfolio-wide threshold)
-- ============================================================
DROP VIEW IF EXISTS analytics.outlier_mean_3sd CASCADE;
CREATE VIEW analytics.outlier_mean_3sd AS
WITH stats AS (
    SELECT
        AVG(total_claim_amount)    AS mean_claim,
        STDDEV(total_claim_amount) AS stddev_claim
    FROM core.fact_claims
)
SELECT
    f.claim_id,
    f.policy_number,
    f.total_claim_amount,
    s.mean_claim,
    s.stddev_claim,
    s.mean_claim + (3 * s.stddev_claim) AS upper_threshold,
    CASE WHEN f.total_claim_amount > s.mean_claim + (3 * s.stddev_claim)
         THEN 1 ELSE 0 END AS mean_3sd_flag
FROM core.fact_claims f
CROSS JOIN stats s;

-- ============================================================
-- CHECK 2: IQR outliers (above Q3 + 1.5 * IQR)
-- ============================================================
DROP VIEW IF EXISTS analytics.outlier_iqr CASCADE;
CREATE VIEW analytics.outlier_iqr AS
WITH quartiles AS (
    SELECT
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY total_claim_amount) AS q1,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY total_claim_amount) AS q3
    FROM core.fact_claims
),
iqr_stats AS (
    SELECT q1, q3, q3 - q1 AS iqr, q3 + (1.5 * (q3 - q1)) AS upper_threshold
    FROM quartiles
)
SELECT
    f.claim_id,
    f.policy_number,
    f.total_claim_amount,
    i.q1,
    i.q3,
    i.iqr,
    i.upper_threshold,
    CASE WHEN f.total_claim_amount > i.upper_threshold THEN 1 ELSE 0 END AS iqr_flag
FROM core.fact_claims f
CROSS JOIN iqr_stats i;

-- ============================================================
-- COMBINED OUTLIER FLAGS
-- ============================================================
DROP VIEW IF EXISTS analytics.outlier_flags CASCADE;
CREATE VIEW analytics.outlier_flags AS
SELECT
    f.claim_id,
    f.policy_number,
    f.total_claim_amount,
    f.fraud_reported_flag,
    m.mean_3sd_flag,
    i.iqr_flag,
    CASE WHEN m.mean_3sd_flag = 1 OR i.iqr_flag = 1 THEN 1 ELSE 0 END AS outlier_flag
FROM core.fact_claims f
JOIN analytics.outlier_mean_3sd m ON f.claim_id = m.claim_id
JOIN analytics.outlier_iqr      i ON f.claim_id = i.claim_id;

-- ============================================================
-- OUTLIER FLAGS VS REPORTED FRAUD (documents that outliers do not detect fraud here)
-- ============================================================
DROP VIEW IF EXISTS analytics.outlier_fraud_metrics CASCADE;
CREATE VIEW analytics.outlier_fraud_metrics AS
WITH metrics AS (
    SELECT
        SUM(CASE WHEN outlier_flag = 1 AND fraud_reported_flag = 1 THEN 1 ELSE 0 END) AS true_positive,
        SUM(CASE WHEN outlier_flag = 1 AND fraud_reported_flag = 0 THEN 1 ELSE 0 END) AS false_positive,
        SUM(CASE WHEN outlier_flag = 0 AND fraud_reported_flag = 1 THEN 1 ELSE 0 END) AS false_negative,
        SUM(CASE WHEN outlier_flag = 0 AND fraud_reported_flag = 0 THEN 1 ELSE 0 END) AS true_negative
    FROM analytics.outlier_flags
)
SELECT
    true_positive,
    false_positive,
    false_negative,
    true_negative,
    ROUND(true_positive::numeric / NULLIF(true_positive + false_positive, 0), 4) AS precision,
    ROUND(true_positive::numeric / NULLIF(true_positive + false_negative, 0), 4) AS recall
FROM metrics;
