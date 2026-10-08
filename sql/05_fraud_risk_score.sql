-- =============================================================================
-- 05_fraud_risk_score.sql
-- Rule-based fraud risk score (pure SQL, no machine learning).
--
-- Rules were chosen by checking fraud rates in this dataset (see
-- sql/06_risk_score_evaluation.sql, query A). Rules that did not separate
-- fraud were dropped: police_report_available barely differs, and
-- authorities_contacted = 'None' has a LOWER fraud rate than average.
-- Hobbies (chess, cross-fit) are deliberately NOT used: non-causal lifestyle
-- variables should not drive a claims score.
--
-- Points: Major Damage +3, collision incident +1, policy tenure under 2 years +1,
-- claim-to-premium multiple above the 75th percentile +1  (max 6).
-- Bands: High 4+, Medium 2-3, Low 0-1.
--
-- The rules are chosen from the same 1,000 rows they are scored on, so the
-- results are descriptive (in-sample), not a prediction of future performance.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS marts;

DROP VIEW IF EXISTS marts.fraud_risk_score CASCADE;
CREATE VIEW marts.fraud_risk_score AS
WITH base AS (
    SELECT
        f.claim_key,
        f.claim_id,
        f.policy_number,
        f.total_claim_amount,
        f.claims_to_premium_multiple,
        f.fraud_reported_flag,
        p.tenure_group,
        i.incident_severity,
        i.incident_type,
        i.incident_state
    FROM core.fact_claims f
    JOIN core.dim_policy   p ON p.policy_key   = f.policy_key
    JOIN core.dim_incident i ON i.incident_key = f.incident_key
),
thresholds AS (
    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY claims_to_premium_multiple) AS p75
    FROM base
),
flags AS (
    SELECT
        b.*,
        CASE WHEN b.incident_severity = 'Major Damage' THEN 1 ELSE 0 END AS r_major_damage,
        CASE WHEN b.incident_type IN ('Single Vehicle Collision', 'Multi-vehicle Collision')
             THEN 1 ELSE 0 END AS r_collision_incident,
        CASE WHEN b.tenure_group = '<2 Years' THEN 1 ELSE 0 END AS r_new_policy,
        CASE WHEN b.claims_to_premium_multiple > t.p75 THEN 1 ELSE 0 END AS r_high_claim_to_premium
    FROM base b
    CROSS JOIN thresholds t
),
scored AS (
    SELECT
        flags.*,
        3 * r_major_damage + r_collision_incident + r_new_policy + r_high_claim_to_premium AS risk_score
    FROM flags
)
SELECT
    claim_key,
    claim_id,
    policy_number,
    incident_state,
    incident_severity,
    total_claim_amount,
    fraud_reported_flag,
    r_major_damage,
    r_collision_incident,
    r_new_policy,
    r_high_claim_to_premium,
    risk_score,
    CASE
        WHEN risk_score >= 4 THEN 'High'
        WHEN risk_score >= 2 THEN 'Medium'
        ELSE 'Low'
    END AS risk_band
FROM scored;
