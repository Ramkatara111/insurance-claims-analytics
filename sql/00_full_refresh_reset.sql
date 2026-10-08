-- ==============================================================================
-- Full-refresh reset: drops ONLY the staging and core tables.
-- Never touches the audit schema (audit.data_quality_audit, audit.pipeline_run_log).
-- CASCADE also removes dependent views in marts/analytics; they are recreated on the
-- next run (02/04/05 scripts). Run sql/01_init_schema.sql afterwards to rebuild.
-- ==============================================================================
DROP TABLE IF EXISTS core.fact_claims CASCADE;
DROP TABLE IF EXISTS core.dim_customer CASCADE;
DROP TABLE IF EXISTS core.dim_policy CASCADE;
DROP TABLE IF EXISTS core.dim_incident CASCADE;
DROP TABLE IF EXISTS core.dim_vehicle CASCADE;
DROP TABLE IF EXISTS core.dim_date CASCADE;
DROP TABLE IF EXISTS staging.stg_insurance_claims CASCADE;
