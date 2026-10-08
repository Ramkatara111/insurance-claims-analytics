import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from src.config import RAW_DATA_PATH
from src.extract import extract_raw_claims
from src.transform import clean_claims_data, build_star_schema, validate_unique_policy_numbers

def test_raw_data_extraction():
    """Verify raw CSV contains 1,000 records and expected columns."""
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    assert df_raw.shape[0] == 1000, f"Expected 1,000 rows, got {df_raw.shape[0]}"
    assert df_raw.shape[1] == 40, f"Expected 40 columns (including _c39), got {df_raw.shape[1]}"
    assert "policy_number" in df_raw.columns

def test_data_cleaning_and_anomalies():
    """Verify data hygiene, anomaly clamping, and renaming rules."""
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    df_clean = clean_claims_data(df_raw)
    
    # 1. _c39 dropped
    assert "_c39" not in df_clean.columns
    
    # 2. Standardized column names
    assert "capital_gains" in df_clean.columns
    assert "capital_loss" in df_clean.columns
    assert "policy_deductible" in df_clean.columns
    assert "capital-gains" not in df_clean.columns
    
    # 3. Umbrella limit anomaly clamped
    assert (df_clean["umbrella_limit"] < 0).sum() == 0
    
    # 4. Misspellings corrected
    assert "Suburu" not in df_clean["auto_make"].values
    assert "Subaru" in df_clean["auto_make"].values
    assert "Accura" not in df_clean["auto_make"].values
    assert "Acura" in df_clean["auto_make"].values
    
    # 5. Missing sentinels replaced
    assert (df_clean["property_damage"] == "?").sum() == 0
    assert (df_clean["police_report_available"] == "?").sum() == 0
    
    # 6. authorities_contacted 'None' preserved
    assert (df_clean["authorities_contacted"] == "None").sum() == 91
    
    # 7. Fraud flag created
    assert set(df_clean["fraud_reported_flag"].unique()) == {0, 1}
    assert df_clean["fraud_reported_flag"].sum() == 247

def test_claim_component_integrity():
    """Verify arithmetic integrity: injury + property + vehicle == total."""
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    df_clean = clean_claims_data(df_raw)
    
    diff = (df_clean["injury_claim"] + df_clean["property_claim"] + df_clean["vehicle_claim"]) - df_clean["total_claim_amount"]
    assert (diff != 0).sum() == 0, "Sum of claim sub-components does not equal total claim amount"

def test_star_schema_natural_key_integrity():
    """Verify star-schema structures: policy_number is the natural key, no Python-made surrogate keys."""
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    df_clean = clean_claims_data(df_raw)
    tables = build_star_schema(df_clean)
    
    fact = tables["fact_claims"]
    dim_cust = tables["dim_customer"]
    dim_pol = tables["dim_policy"]
    dim_inc = tables["dim_incident"]
    dim_veh = tables["dim_vehicle"]
    dim_dt = tables["dim_date"]
    
    # Check row counts
    assert len(fact) == 1000
    assert len(dim_cust) == 1000
    assert len(dim_pol) == 1000
    assert len(dim_inc) == 1000
    assert len(dim_veh) == 1000
    assert len(dim_dt) > 0
    
    # Natural key unique everywhere and 1:1 between fact and every dimension
    for name, tbl in [("fact", fact), ("customer", dim_cust), ("policy", dim_pol),
                      ("incident", dim_inc), ("vehicle", dim_veh)]:
        assert "policy_number" in tbl.columns, f"{name} lacks policy_number"
        assert tbl["policy_number"].is_unique, f"{name} policy_number not unique"
        assert set(tbl["policy_number"]) == set(fact["policy_number"]), f"{name} keys differ from fact"
    
    # Surrogate keys and claim_id are assigned by PostgreSQL, not generated in Python
    forbidden = {"claim_key", "claim_id", "customer_key", "policy_key", "incident_key", "vehicle_key"}
    for name, tbl in tables.items():
        assert not (forbidden & set(tbl.columns)), f"{name} must not carry surrogate keys"
    
    # Date foreign keys are still complete (no orphans)
    assert fact["incident_date_key"].isin(dim_dt["date_key"]).all()
    assert fact["policy_bind_date_key"].isin(dim_dt["date_key"]).all()


def test_source_row_number_is_one_based_file_position():
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    df_clean = clean_claims_data(df_raw)
    assert df_clean["source_row_number"].tolist() == list(range(1, len(df_raw) + 1))
    # policy_number at a given source_row_number matches that row of the raw file
    assert df_clean["policy_number"].iloc[0] == df_raw["policy_number"].iloc[0]
    assert "source_row_number" in build_star_schema(df_clean)["stg_insurance_claims"].columns


def test_duplicate_policy_numbers_fail_fast():
    df_raw = extract_raw_claims(RAW_DATA_PATH)
    df_clean = clean_claims_data(df_raw)
    validate_unique_policy_numbers(df_clean)  # real data is clean: must not raise
    
    dup_value = int(df_clean["policy_number"].iloc[0])
    df_dup = pd.concat([df_clean, df_clean.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError) as exc:
        validate_unique_policy_numbers(df_dup)
    assert str(dup_value) in str(exc.value)


def test_get_engine_accepts_database_name():
    from src import config
    assert not hasattr(config, "engine"), "no module-level engine allowed"
    assert config.get_engine().url.database == config.DB_NAME
    assert config.get_engine("other_db").url.database == "other_db"


def test_database_audit_table_and_reconciliation():
    """Verify live PostgreSQL data quality audit table and idempotency."""
    from sqlalchemy import text
    from src.config import get_engine
    
    engine = get_engine()
    with engine.connect() as conn:
        # 1. Fact claims row count
        fact_count = conn.execute(text("SELECT COUNT(*) FROM core.fact_claims;")).scalar()
        assert fact_count == 1000, f"Expected 1,000 fact claims, got {fact_count}"
        
        # 2. Check audit table existence and evaluated checks
        latest_run = "(SELECT run_id FROM audit.data_quality_audit ORDER BY checked_at DESC, audit_id DESC LIMIT 1)"
        audit_count = conn.execute(text(f"SELECT COUNT(*) FROM audit.data_quality_audit WHERE run_id = {latest_run};")).scalar()
        assert audit_count == 14, f"Expected 14 audit rules in the latest run, found {audit_count}"
        
        # 3. Check that zero critical checks failed
        critical_failures = conn.execute(text("""
            SELECT COUNT(*) FROM audit.data_quality_audit 
            WHERE severity = 'CRITICAL' AND status = 'FAILED'
              AND run_id = (SELECT run_id FROM audit.data_quality_audit ORDER BY checked_at DESC, audit_id DESC LIMIT 1);
        """)).scalar()
        assert critical_failures == 0, f"Critical DQ failures encountered: {critical_failures}"


def test_audit_history_is_retained_with_run_ids():
    """Every pipeline run appends under its own run_id; the audit log is not wiped on re-run."""
    from sqlalchemy import text
    from src.config import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        runs = conn.execute(text("SELECT COUNT(DISTINCT run_id) FROM audit.data_quality_audit;")).scalar()
        tagged_rows = conn.execute(text("SELECT COUNT(*) FROM audit.data_quality_audit WHERE run_id IS NOT NULL;")).scalar()
        assert runs >= 1
        assert tagged_rows >= 14  # at least one full run's checks are stored with a run_id
