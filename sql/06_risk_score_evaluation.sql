-- =============================================================================
-- 06_risk_score_evaluation.sql
-- Run manually after the pipeline; paste real output into README section 5 and
-- docs/findings_and_insights.md. Results are in-sample (descriptive).
-- =============================================================================

-- A. Per-rule diagnostic: does each rule separate reported fraud from non-fraud?
SELECT 'major_damage' AS rule, r_major_damage AS flagged, COUNT(*) AS claims,
       SUM(fraud_reported_flag) AS fraud_claims,
       ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2) AS fraud_rate_pct
FROM marts.fraud_risk_score GROUP BY r_major_damage
UNION ALL
SELECT 'collision_incident', r_collision_incident, COUNT(*), SUM(fraud_reported_flag),
       ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2)
FROM marts.fraud_risk_score GROUP BY r_collision_incident
UNION ALL
SELECT 'new_policy_under_2y', r_new_policy, COUNT(*), SUM(fraud_reported_flag),
       ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2)
FROM marts.fraud_risk_score GROUP BY r_new_policy
UNION ALL
SELECT 'high_claim_to_premium', r_high_claim_to_premium, COUNT(*), SUM(fraud_reported_flag),
       ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2)
FROM marts.fraud_risk_score GROUP BY r_high_claim_to_premium
ORDER BY rule, flagged;

-- B. Fraud rate and lift by risk band (lift = band fraud rate / overall fraud rate)
WITH overall AS (
    SELECT SUM(fraud_reported_flag)::numeric / COUNT(*) AS overall_rate
    FROM marts.fraud_risk_score
)
SELECT
    s.risk_band,
    COUNT(*)                                                    AS claims,
    SUM(s.fraud_reported_flag)                                  AS fraud_claims,
    ROUND(100.0 * SUM(s.fraud_reported_flag) / COUNT(*), 2)     AS fraud_rate_pct,
    ROUND((SUM(s.fraud_reported_flag)::numeric / COUNT(*)) / o.overall_rate, 2) AS lift
FROM marts.fraud_risk_score s
CROSS JOIN overall o
GROUP BY s.risk_band, o.overall_rate
ORDER BY CASE s.risk_band WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END;

-- C. High band precision and recall against the reported fraud flag
SELECT
    COUNT(*) FILTER (WHERE risk_band = 'High')                          AS high_claims,
    SUM(fraud_reported_flag) FILTER (WHERE risk_band = 'High')          AS high_fraud_claims,
    SUM(fraud_reported_flag)                                            AS total_fraud_claims,
    ROUND(100.0 * SUM(fraud_reported_flag) FILTER (WHERE risk_band = 'High')
          / NULLIF(COUNT(*) FILTER (WHERE risk_band = 'High'), 0), 2)   AS high_precision_pct,
    ROUND(100.0 * SUM(fraud_reported_flag) FILTER (WHERE risk_band = 'High')
          / NULLIF(SUM(fraud_reported_flag), 0), 2)                     AS high_recall_pct
FROM marts.fraud_risk_score;

-- D. Outlier checks vs reported fraud (documents that outliers do not detect fraud here)
SELECT * FROM analytics.outlier_fraud_metrics;
