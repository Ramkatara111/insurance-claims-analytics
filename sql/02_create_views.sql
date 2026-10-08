-- ==============================================================================
-- Analytical and Reporting Views (Marts Layer)
-- Optimized for Power BI consumption and executive reporting
-- ==============================================================================

-- 1. Claims Summary by State & Geography
DROP VIEW IF EXISTS marts.vw_claim_summary_by_state CASCADE;
CREATE VIEW marts.vw_claim_summary_by_state AS
SELECT 
    p.policy_state,
    i.incident_state,
    COUNT(f.claim_key) AS total_claims,
    SUM(f.total_claim_amount) AS total_incurred_loss,
    ROUND(AVG(f.total_claim_amount), 2) AS avg_claim_amount,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    SUM(f.policy_annual_premium) AS total_annual_premium,
    ROUND(SUM(f.total_claim_amount) / NULLIF(SUM(f.policy_annual_premium), 0), 4) AS segment_claims_to_premium_multiple
FROM core.fact_claims f
JOIN core.dim_policy p ON f.policy_key = p.policy_key
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY p.policy_state, i.incident_state;

-- 2. Fraud Risk Factors & Incident Characteristics
DROP VIEW IF EXISTS marts.vw_fraud_risk_factors CASCADE;
CREATE VIEW marts.vw_fraud_risk_factors AS
SELECT 
    i.incident_type,
    i.collision_type,
    i.incident_severity,
    i.incident_time_window,
    COUNT(f.claim_key) AS total_claims,
    SUM(f.total_claim_amount) AS total_claim_amount,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct,
    ROUND(AVG(f.total_claim_amount), 2) AS avg_claim_amount,
    ROUND(AVG(f.vehicle_claim), 2) AS avg_vehicle_claim
FROM core.fact_claims f
JOIN core.dim_incident i ON f.incident_key = i.incident_key
GROUP BY i.incident_type, i.collision_type, i.incident_severity, i.incident_time_window;

-- 3. Vehicle Risk & Loss Profile
DROP VIEW IF EXISTS marts.vw_vehicle_loss_profile CASCADE;
CREATE VIEW marts.vw_vehicle_loss_profile AS
SELECT 
    v.auto_make,
    COUNT(f.claim_key) AS total_claims,
    SUM(f.total_claim_amount) AS total_incurred_loss,
    ROUND(AVG(f.total_claim_amount), 2) AS avg_claim_amount,
    ROUND(AVG(v.vehicle_age_at_incident), 1) AS avg_vehicle_age,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct
FROM core.fact_claims f
JOIN core.dim_vehicle v ON f.vehicle_key = v.vehicle_key
GROUP BY v.auto_make;

-- 4. Customer Demographics & Segment Analysis
DROP VIEW IF EXISTS marts.vw_customer_demographic_analysis CASCADE;
CREATE VIEW marts.vw_customer_demographic_analysis AS
SELECT 
    c.age_group,
    c.insured_sex,
    c.insured_education_level,
    COUNT(f.claim_key) AS total_claims,
    SUM(f.total_claim_amount) AS total_incurred_loss,
    ROUND(AVG(f.total_claim_amount), 2) AS avg_claim_amount,
    SUM(f.fraud_reported_flag) AS fraud_claims,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_rate_pct
FROM core.fact_claims f
JOIN core.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.age_group, c.insured_sex, c.insured_education_level;

-- 5. Underwriting Portfolio & Claims-to-Premium Analysis
-- (total claims / one year of annual premium: a relative indicator, NOT an actuarial loss ratio)
DROP VIEW IF EXISTS marts.vw_claims_to_premium_by_segment CASCADE;
CREATE VIEW marts.vw_claims_to_premium_by_segment AS
SELECT 
    p.policy_state,
    p.policy_csl,
    p.tenure_group,
    COUNT(f.claim_key) AS total_policies_claimed,
    SUM(f.policy_annual_premium) AS total_annual_premium,
    SUM(f.total_claim_amount) AS total_incurred_claims,
    ROUND(SUM(f.total_claim_amount) / NULLIF(SUM(f.policy_annual_premium), 0), 4) AS claims_to_premium_multiple,
    SUM(f.fraud_reported_flag) AS fraud_count,
    ROUND(100.0 * SUM(f.fraud_reported_flag) / COUNT(f.claim_key), 2) AS fraud_pct
FROM core.fact_claims f
JOIN core.dim_policy p ON f.policy_key = p.policy_key
GROUP BY p.policy_state, p.policy_csl, p.tenure_group;
