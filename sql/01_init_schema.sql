-- ==============================================================================
-- Idempotent schema DDL for the Insurance Claims Data Warehouse (PostgreSQL 16)
-- Safe to run on every pipeline run: nothing here drops or truncates a table.
--   * CREATE TABLE IF NOT EXISTS + ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--   * Surrogate keys are SERIAL (assigned by PostgreSQL, never by Python)
--   * Natural key of the whole model: policy_number (one claim per policy)
-- To rebuild from scratch run sql/00_full_refresh_reset.sql first.
-- Note: on a database created by the old (v3) schema, run 00_full_refresh_reset.sql
-- once; ADD COLUMN IF NOT EXISTS cannot back-fill NOT NULL natural keys on old rows.
-- ==============================================================================

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ==============================================================================
-- 1. STAGING (landing zone: truncated and reloaded on every run by the loader)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS staging.stg_insurance_claims (
    source_row_number INT,
    months_as_customer INT,
    age INT,
    policy_number BIGINT,
    policy_bind_date DATE,
    policy_state VARCHAR(10),
    policy_csl VARCHAR(20),
    policy_deductible NUMERIC(12,2),
    policy_annual_premium NUMERIC(12,2),
    umbrella_limit NUMERIC(14,2),
    insured_zip VARCHAR(20),
    insured_sex VARCHAR(10),
    insured_education_level VARCHAR(50),
    insured_occupation VARCHAR(50),
    insured_hobbies VARCHAR(50),
    insured_relationship VARCHAR(50),
    capital_gains NUMERIC(12,2),
    capital_loss NUMERIC(12,2),
    incident_date DATE,
    incident_type VARCHAR(50),
    collision_type VARCHAR(50),
    incident_severity VARCHAR(50),
    authorities_contacted VARCHAR(50),
    incident_state VARCHAR(10),
    incident_city VARCHAR(50),
    incident_location VARCHAR(100),
    incident_hour_of_the_day INT,
    number_of_vehicles_involved INT,
    property_damage VARCHAR(20),
    bodily_injuries INT,
    witnesses INT,
    police_report_available VARCHAR(20),
    total_claim_amount NUMERIC(12,2),
    injury_claim NUMERIC(12,2),
    property_claim NUMERIC(12,2),
    vehicle_claim NUMERIC(12,2),
    auto_make VARCHAR(50),
    auto_model VARCHAR(50),
    auto_year INT,
    fraud_reported VARCHAR(5),
    -- derived in transform.py and landed here so the SQL loads read everything from staging
    age_group VARCHAR(20),
    tenure_years NUMERIC(6,2),
    tenure_group VARCHAR(20),
    incident_time_window VARCHAR(20),
    vehicle_age_at_incident INT,
    claims_to_premium_multiple NUMERIC(10,4),
    fraud_reported_flag INT,
    incident_date_key INT,
    policy_bind_date_key INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS source_row_number INT;  -- 1-based position in the source file
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS age_group VARCHAR(20);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS tenure_years NUMERIC(6,2);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS tenure_group VARCHAR(20);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS incident_time_window VARCHAR(20);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS vehicle_age_at_incident INT;
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS claims_to_premium_multiple NUMERIC(10,4);
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS fraud_reported_flag INT;
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS incident_date_key INT;
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS policy_bind_date_key INT;
ALTER TABLE staging.stg_insurance_claims ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- ==============================================================================
-- 2. DIMENSIONS (core)
-- ==============================================================================

-- Date dimension (natural integer key yyyymmdd, no surrogate needed)
CREATE TABLE IF NOT EXISTS core.dim_date (
    date_key INT PRIMARY KEY,
    full_date DATE NOT NULL UNIQUE,
    year INT NOT NULL,
    quarter INT NOT NULL,
    month INT NOT NULL,
    month_name VARCHAR(20) NOT NULL,
    day INT NOT NULL,
    day_of_week INT NOT NULL,
    day_name VARCHAR(20) NOT NULL,
    is_weekend BOOLEAN NOT NULL
);
ALTER TABLE core.dim_date ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.dim_date ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Customer dimension
CREATE TABLE IF NOT EXISTS core.dim_customer (
    customer_key SERIAL PRIMARY KEY,
    policy_number BIGINT NOT NULL,
    age INT NOT NULL,
    age_group VARCHAR(20) NOT NULL,
    insured_sex VARCHAR(10) NOT NULL,
    insured_education_level VARCHAR(50) NOT NULL,
    insured_occupation VARCHAR(50) NOT NULL,
    insured_hobbies VARCHAR(50) NOT NULL,
    insured_relationship VARCHAR(50) NOT NULL,
    insured_zip VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE core.dim_customer ADD COLUMN IF NOT EXISTS policy_number BIGINT;
ALTER TABLE core.dim_customer ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.dim_customer ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
DROP INDEX IF EXISTS core.idx_dim_customer_policy;  -- old non-unique index, replaced by the unique one below
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_customer_policy_number ON core.dim_customer(policy_number);

-- Policy dimension
CREATE TABLE IF NOT EXISTS core.dim_policy (
    policy_key SERIAL PRIMARY KEY,
    policy_number BIGINT NOT NULL UNIQUE,
    policy_bind_date DATE NOT NULL,
    policy_state VARCHAR(10) NOT NULL,
    policy_csl VARCHAR(20) NOT NULL,
    policy_deductible NUMERIC(12,2) NOT NULL,
    policy_annual_premium NUMERIC(12,2) NOT NULL,
    months_as_customer INT NOT NULL,
    tenure_years NUMERIC(6,2) NOT NULL,
    tenure_group VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE core.dim_policy ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.dim_policy ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Incident dimension
CREATE TABLE IF NOT EXISTS core.dim_incident (
    incident_key SERIAL PRIMARY KEY,
    policy_number BIGINT NOT NULL UNIQUE,
    incident_type VARCHAR(50) NOT NULL,
    collision_type VARCHAR(50) NOT NULL,
    incident_severity VARCHAR(50) NOT NULL,
    authorities_contacted VARCHAR(50) NOT NULL,
    incident_state VARCHAR(10) NOT NULL,
    incident_city VARCHAR(50) NOT NULL,
    incident_location VARCHAR(100) NOT NULL,
    incident_hour_of_the_day INT NOT NULL,
    incident_time_window VARCHAR(20) NOT NULL,
    property_damage VARCHAR(20) NOT NULL,
    police_report_available VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE core.dim_incident ADD COLUMN IF NOT EXISTS policy_number BIGINT;
ALTER TABLE core.dim_incident ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.dim_incident ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_incident_policy_number ON core.dim_incident(policy_number);

-- Vehicle dimension
CREATE TABLE IF NOT EXISTS core.dim_vehicle (
    vehicle_key SERIAL PRIMARY KEY,
    policy_number BIGINT NOT NULL UNIQUE,
    auto_make VARCHAR(50) NOT NULL,
    auto_model VARCHAR(50) NOT NULL,
    auto_year INT NOT NULL,
    vehicle_age_at_incident INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE core.dim_vehicle ADD COLUMN IF NOT EXISTS policy_number BIGINT;
ALTER TABLE core.dim_vehicle ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.dim_vehicle ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_vehicle_policy_number ON core.dim_vehicle(policy_number);

-- ==============================================================================
-- 3. FACT TABLE (core)
-- ==============================================================================
-- claim_key is a SERIAL assigned by PostgreSQL. claim_id is a plain column filled by the loader
-- for NEW rows only (CLM-10001, CLM-10002, ... continuing from the current maximum); existing
-- claim_ids and surrogate keys are never rewritten. Views (04/05) select f.claim_id.
CREATE TABLE IF NOT EXISTS core.fact_claims (
    claim_key SERIAL PRIMARY KEY,
    claim_id VARCHAR(50) NOT NULL,
    policy_number BIGINT NOT NULL UNIQUE,
    incident_date_key INT NOT NULL REFERENCES core.dim_date(date_key),
    policy_bind_date_key INT NOT NULL REFERENCES core.dim_date(date_key),
    customer_key INT NOT NULL REFERENCES core.dim_customer(customer_key),
    policy_key INT NOT NULL REFERENCES core.dim_policy(policy_key),
    incident_key INT NOT NULL REFERENCES core.dim_incident(incident_key),
    vehicle_key INT NOT NULL REFERENCES core.dim_vehicle(vehicle_key),
    total_claim_amount NUMERIC(12,2) NOT NULL,
    injury_claim NUMERIC(12,2) NOT NULL,
    property_claim NUMERIC(12,2) NOT NULL,
    vehicle_claim NUMERIC(12,2) NOT NULL,
    policy_annual_premium NUMERIC(12,2) NOT NULL,
    policy_deductible NUMERIC(12,2) NOT NULL,
    umbrella_limit NUMERIC(14,2) NOT NULL,
    capital_gains NUMERIC(12,2) NOT NULL,
    capital_loss NUMERIC(12,2) NOT NULL,
    number_of_vehicles_involved INT NOT NULL,
    bodily_injuries INT NOT NULL,
    witnesses INT NOT NULL,
    fraud_reported_flag INT NOT NULL,
    fraud_reported_desc VARCHAR(5) NOT NULL,
    claims_to_premium_multiple NUMERIC(10,4) NOT NULL,  -- total claims / annual premium; NOT an actuarial loss ratio
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE core.fact_claims ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE core.fact_claims ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
-- A database created by the Step 1 schema had claim_id as a generated column; make it a plain column (no-op otherwise).
ALTER TABLE core.fact_claims ALTER COLUMN claim_id DROP EXPRESSION IF EXISTS;
ALTER TABLE core.fact_claims ALTER COLUMN claim_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_claims_claim_id ON core.fact_claims(claim_id);
CREATE INDEX IF NOT EXISTS idx_fact_claims_policy ON core.fact_claims(policy_number);
CREATE INDEX IF NOT EXISTS idx_fact_claims_incident_date ON core.fact_claims(incident_date_key);
CREATE INDEX IF NOT EXISTS idx_fact_claims_fraud ON core.fact_claims(fraud_reported_flag);

-- ==============================================================================
-- 4. AUDIT (never dropped, never truncated)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS audit.data_quality_audit (
    audit_id SERIAL PRIMARY KEY,
    check_name VARCHAR(100) NOT NULL,
    check_type VARCHAR(50) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    column_name VARCHAR(100),
    severity VARCHAR(20) NOT NULL,
    records_evaluated INT NOT NULL,
    records_failed INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    details TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE audit.data_quality_audit ADD COLUMN IF NOT EXISTS run_id UUID;
CREATE INDEX IF NOT EXISTS idx_dq_audit_status ON audit.data_quality_audit(status);
CREATE INDEX IF NOT EXISTS idx_dq_audit_run ON audit.data_quality_audit(run_id);

CREATE TABLE IF NOT EXISTS audit.pipeline_run_log (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    mode TEXT NOT NULL CHECK (mode IN ('incremental', 'full_refresh')),
    source_file TEXT,
    rows_extracted INT,
    fact_inserted INT,
    fact_updated INT,
    fact_unchanged INT,
    not_in_source INT,
    details JSONB,
    status TEXT,
    error_message TEXT
);
