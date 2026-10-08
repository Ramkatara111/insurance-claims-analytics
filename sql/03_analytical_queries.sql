-- ==============================================================================
-- Insurance Claims Analytics: Business & Actuarial SQL Queries
-- Portfolio project queries
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Query 1: Executive KPI Dashboard Summary
-- ------------------------------------------------------------------------------
SELECT 
    COUNT(claim_key) AS total_claims_count,
    COUNT(DISTINCT policy_number) AS total_policies_impacted,
    TO_CHAR(SUM(total_claim_amount), '$999,999,999.00') AS total_claim_amount,
    TO_CHAR(AVG(total_claim_amount), '$999,999.00') AS avg_claim_amount,
    TO_CHAR(SUM(policy_annual_premium), '$999,999,999.00') AS total_annual_premium,
    ROUND(SUM(total_claim_amount) / NULLIF(SUM(policy_annual_premium), 0), 2) AS portfolio_claims_to_premium_multiple,
    SUM(fraud_reported_flag) AS total_fraud_claims,
    ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(claim_key), 2) AS overall_fraud_rate_pct,
    TO_CHAR(SUM(CASE WHEN fraud_reported_flag = 1 THEN total_claim_amount ELSE 0 END), '$999,999,999.00') AS fraud_dollar_exposure
FROM core.fact_claims;

-- ------------------------------------------------------------------------------
-- Query 2: Claim Severity vs Fraud Rate & Dollar Impact
-- ------------------------------------------------------------------------------
SELECT 
    i.incident_severity,
    COUNT(f.claim_key) AS claim_count,
    ROUND(100.0 * COUNT(f.claim_key) / SUM(COUNT(f.claim_key)) OVER(), 2) AS pct_of_total_claims,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    TO_CHAR(SUM(f.total_claim_amount), '$99,999,999') AS total_loss,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_size
FROM core.fact_claims f
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY i.incident_severity
ORDER BY SUM(f.total_claim_amount) DESC;

-- ------------------------------------------------------------------------------
-- Query 3: Incident Type & Collision Type Risk Matrix
-- ------------------------------------------------------------------------------
SELECT 
    i.incident_type,
    i.collision_type,
    COUNT(f.claim_key) AS claims_count,
    SUM(f.fraud_reported_flag) AS fraud_count,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_total_claim,
    TO_CHAR(AVG(f.vehicle_claim), '$99,999') AS avg_vehicle_claim
FROM core.fact_claims f
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY i.incident_type, i.collision_type
ORDER BY claims_count DESC;

-- ------------------------------------------------------------------------------
-- Query 4: Impact of Police Report Availability on Fraud Rates
-- ------------------------------------------------------------------------------
SELECT 
    i.police_report_available,
    i.property_damage,
    COUNT(f.claim_key) AS total_claims,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_amount
FROM core.fact_claims f
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY i.police_report_available, i.property_damage
ORDER BY fraud_rate_pct DESC;

-- ------------------------------------------------------------------------------
-- Query 5: Time of Day & Hourly Incident Patterns
-- ------------------------------------------------------------------------------
SELECT 
    i.incident_time_window,
    COUNT(f.claim_key) AS claims_count,
    SUM(f.fraud_reported_flag) AS fraud_count,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_size
FROM core.fact_claims f
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY i.incident_time_window
ORDER BY claims_count DESC;

-- ------------------------------------------------------------------------------
-- Query 6: Claims-to-Premium Multiple & Exposure by Policy State & CSL Limits
-- (total claims / one year of annual premium; relative indicator, not an actuarial loss ratio)
-- ------------------------------------------------------------------------------
SELECT 
    p.policy_state,
    p.policy_csl,
    COUNT(f.claim_key) AS policies_count,
    TO_CHAR(SUM(f.policy_annual_premium), '$99,999,999') AS total_premium,
    TO_CHAR(SUM(f.total_claim_amount), '$99,999,999') AS total_claims_paid,
    ROUND(SUM(f.total_claim_amount) / NULLIF(SUM(f.policy_annual_premium), 0), 2) AS claims_to_premium_multiple,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct
FROM core.fact_claims f
JOIN core.dim_policy p ON f.policy_key = p.policy_key
GROUP BY p.policy_state, p.policy_csl
ORDER BY p.policy_state, claims_to_premium_multiple DESC;

-- ------------------------------------------------------------------------------
-- Query 7: Customer Tenure Cohort Analysis
-- ------------------------------------------------------------------------------
SELECT 
    p.tenure_group,
    COUNT(f.claim_key) AS customer_count,
    ROUND(AVG(c.age), 1) AS avg_customer_age,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_amount,
    SUM(f.fraud_reported_flag) AS fraud_count,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct
FROM core.fact_claims f
JOIN core.dim_policy p ON f.policy_key = p.policy_key
JOIN core.dim_customer c ON f.customer_key = c.customer_key
GROUP BY p.tenure_group
ORDER BY MIN(p.months_as_customer);

-- ------------------------------------------------------------------------------
-- Query 8: Vehicle Make & Model Risk Profiling (Loss Severity vs Volume)
-- ------------------------------------------------------------------------------
SELECT 
    v.auto_make,
    COUNT(f.claim_key) AS total_claims,
    TO_CHAR(SUM(f.total_claim_amount), '$99,999,999') AS total_incurred_loss,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_amount,
    TO_CHAR(AVG(f.vehicle_claim), '$99,999') AS avg_vehicle_claim,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct
FROM core.fact_claims f
JOIN core.dim_vehicle v ON f.vehicle_key = v.vehicle_key
GROUP BY v.auto_make
ORDER BY SUM(f.total_claim_amount) DESC;

-- ------------------------------------------------------------------------------
-- Query 9: Insured Hobby Analysis (Known Benchmark Predictors)
-- ------------------------------------------------------------------------------
SELECT 
    c.insured_hobbies,
    COUNT(f.claim_key) AS policy_count,
    SUM(f.fraud_reported_flag) AS fraud_count,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    TO_CHAR(AVG(f.total_claim_amount), '$99,999') AS avg_claim_amount
FROM core.fact_claims f
JOIN core.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.insured_hobbies
ORDER BY fraud_rate_pct DESC;

-- ------------------------------------------------------------------------------
-- Query 10: Claim Component Share Analysis (Injury, Property, Vehicle)
-- ------------------------------------------------------------------------------
SELECT 
    TO_CHAR(SUM(total_claim_amount), '$99,999,999') AS total_claim_amount,
    TO_CHAR(SUM(vehicle_claim), '$99,999,999') AS total_vehicle_claim,
    ROUND(100.0 * SUM(vehicle_claim) / SUM(total_claim_amount), 2) AS vehicle_claim_pct,
    TO_CHAR(SUM(property_claim), '$99,999,999') AS total_property_claim,
    ROUND(100.0 * SUM(property_claim) / SUM(total_claim_amount), 2) AS property_claim_pct,
    TO_CHAR(SUM(injury_claim), '$99,999,999') AS total_injury_claim,
    ROUND(100.0 * SUM(injury_claim) / SUM(total_claim_amount), 2) AS injury_claim_pct
FROM core.fact_claims;
