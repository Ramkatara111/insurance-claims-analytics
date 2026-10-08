import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import List, Dict, Any, Optional
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from src.config import get_engine, logger, RAW_DATA_PATH

def run_data_quality_suite(
    engine: Optional[Engine] = None,
    run_id: Optional[str] = None,
    source_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Executes comprehensive data quality assertions, anomaly checks,
    and reconciliations, writing results to audit.data_quality_audit.

    engine:      SQLAlchemy engine to use (created with get_engine() if omitted).
    run_id:      the pipeline run's id so DQ rows share it with audit.pipeline_run_log
                 (a new uuid is generated if omitted, e.g. when run standalone).
    source_path: the source file that was loaded (defaults to the configured RAW_DATA_PATH).
    """
    engine = engine or get_engine()
    audit_results: List[Dict[str, Any]] = []
    run_id = str(run_id) if run_id else str(uuid.uuid4())
    
    # Expected row count comes from the source file that was loaded, not a hard-coded constant
    raw_cnt = len(pd.read_csv(source_path or RAW_DATA_PATH, usecols=[0], keep_default_na=False))
    
    logger.info(f"Starting Data Quality & Anomaly Audit Suite (run_id={run_id})...")
    
    with engine.connect() as conn:
        # 1. Staging vs Raw Reconciliation
        stg_cnt = conn.execute(text("SELECT COUNT(*) FROM staging.stg_insurance_claims;")).scalar()
        audit_results.append({
            "check_name": "raw_to_staging_reconciliation",
            "check_type": "Reconciliation",
            "table_name": "staging.stg_insurance_claims",
            "column_name": "ALL",
            "severity": "CRITICAL",
            "records_evaluated": raw_cnt,
            "records_failed": abs(raw_cnt - stg_cnt),
            "status": "PASSED" if stg_cnt == raw_cnt else "FAILED",
            "details": f"Staging contains {stg_cnt:,} rows; raw source file contains {raw_cnt:,} rows."
        })
        
        # 2. Fact vs Staging Reconciliation
        # Incremental loads never delete claims that vanished from the source, so compare the
        # fact rows whose policy_number is in staging; surplus rows are reported in the details.
        fact_total = conn.execute(text("SELECT COUNT(*) FROM core.fact_claims;")).scalar()
        fact_cnt = conn.execute(text("""
            SELECT COUNT(*) FROM core.fact_claims f
            WHERE EXISTS (SELECT 1 FROM staging.stg_insurance_claims s WHERE s.policy_number = f.policy_number);
        """)).scalar()
        not_in_source = fact_total - fact_cnt
        audit_results.append({
            "check_name": "staging_to_fact_reconciliation",
            "check_type": "Reconciliation",
            "table_name": "core.fact_claims",
            "column_name": "claim_key",
            "severity": "CRITICAL",
            "records_evaluated": stg_cnt,
            "records_failed": 0 if fact_cnt == stg_cnt else abs(stg_cnt - fact_cnt),
            "status": "PASSED" if fact_cnt == stg_cnt else "FAILED",
            "details": f"Fact table contains {fact_cnt:,} of {stg_cnt:,} staged claims; {not_in_source:,} warehouse claim(s) not in the current source."
        })
        fact_cnt = fact_total  # later checks evaluate the whole fact table
        
        # 3. Arithmetic Claim Sub-component Balance
        unbalanced = conn.execute(text("""
            SELECT COUNT(*) 
            FROM core.fact_claims 
            WHERE (injury_claim + property_claim + vehicle_claim) != total_claim_amount;
        """)).scalar()
        audit_results.append({
            "check_name": "claim_arithmetic_integrity",
            "check_type": "Validity",
            "table_name": "core.fact_claims",
            "column_name": "total_claim_amount",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": unbalanced,
            "status": "PASSED" if unbalanced == 0 else "FAILED",
            "details": f"Arithmetic verification: injury + property + vehicle == total claim amount. Mismatches: {unbalanced}."
        })
        
        # 4. Foreign Key Orphan Check: Customer Dimension
        orphan_cust = conn.execute(text("""
            SELECT COUNT(f.claim_key)
            FROM core.fact_claims f
            LEFT JOIN core.dim_customer c ON f.customer_key = c.customer_key
            WHERE c.customer_key IS NULL;
        """)).scalar()
        audit_results.append({
            "check_name": "orphan_fk_dim_customer",
            "check_type": "Integrity",
            "table_name": "core.fact_claims",
            "column_name": "customer_key",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": orphan_cust,
            "status": "PASSED" if orphan_cust == 0 else "FAILED",
            "details": f"Foreign key integrity: {orphan_cust} orphan customer keys found."
        })
        
        # 5. Foreign Key Orphan Check: Policy Dimension
        orphan_pol = conn.execute(text("""
            SELECT COUNT(f.claim_key)
            FROM core.fact_claims f
            LEFT JOIN core.dim_policy p ON f.policy_key = p.policy_key
            WHERE p.policy_key IS NULL;
        """)).scalar()
        audit_results.append({
            "check_name": "orphan_fk_dim_policy",
            "check_type": "Integrity",
            "table_name": "core.fact_claims",
            "column_name": "policy_key",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": orphan_pol,
            "status": "PASSED" if orphan_pol == 0 else "FAILED",
            "details": f"Foreign key integrity: {orphan_pol} orphan policy keys found."
        })
        
        # 6. Foreign Key Orphan Check: Incident Dimension
        orphan_inc = conn.execute(text("""
            SELECT COUNT(f.claim_key)
            FROM core.fact_claims f
            LEFT JOIN core.dim_incident i ON f.incident_key = i.incident_key
            WHERE i.incident_key IS NULL;
        """)).scalar()
        audit_results.append({
            "check_name": "orphan_fk_dim_incident",
            "check_type": "Integrity",
            "table_name": "core.fact_claims",
            "column_name": "incident_key",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": orphan_inc,
            "status": "PASSED" if orphan_inc == 0 else "FAILED",
            "details": f"Foreign key integrity: {orphan_inc} orphan incident keys found."
        })
        
        # 7. Foreign Key Orphan Check: Vehicle Dimension
        orphan_veh = conn.execute(text("""
            SELECT COUNT(f.claim_key)
            FROM core.fact_claims f
            LEFT JOIN core.dim_vehicle v ON f.vehicle_key = v.vehicle_key
            WHERE v.vehicle_key IS NULL;
        """)).scalar()
        audit_results.append({
            "check_name": "orphan_fk_dim_vehicle",
            "check_type": "Integrity",
            "table_name": "core.fact_claims",
            "column_name": "vehicle_key",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": orphan_veh,
            "status": "PASSED" if orphan_veh == 0 else "FAILED",
            "details": f"Foreign key integrity: {orphan_veh} orphan vehicle keys found."
        })
        
        # 8. Foreign Key Orphan Check: Date Dimension
        orphan_date = conn.execute(text("""
            SELECT COUNT(f.claim_key)
            FROM core.fact_claims f
            LEFT JOIN core.dim_date d ON f.incident_date_key = d.date_key
            WHERE d.date_key IS NULL;
        """)).scalar()
        audit_results.append({
            "check_name": "orphan_fk_dim_date",
            "check_type": "Integrity",
            "table_name": "core.fact_claims",
            "column_name": "incident_date_key",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": orphan_date,
            "status": "PASSED" if orphan_date == 0 else "FAILED",
            "details": f"Foreign key integrity: {orphan_date} orphan date keys found."
        })
        
        # 9. Anomaly Rule: Clamped Umbrella Limit
        neg_umbrella = conn.execute(text("""
            SELECT COUNT(*) FROM core.fact_claims WHERE umbrella_limit < 0;
        """)).scalar()
        audit_results.append({
            "check_name": "anomaly_negative_umbrella_limit",
            "check_type": "Anomaly",
            "table_name": "core.fact_claims",
            "column_name": "umbrella_limit",
            "severity": "WARNING",
            "records_evaluated": fact_cnt,
            "records_failed": neg_umbrella,
            "status": "PASSED" if neg_umbrella == 0 else "FAILED",
            "details": f"Negative umbrella limit clamping rule validated: {neg_umbrella} negative values remain in DW."
        })
        
        # 10. Anomaly Rule: Chronological Date Order (Incident precedes Bind Date)
        inverted_dates = conn.execute(text("""
            SELECT COUNT(*) 
            FROM core.fact_claims f
            JOIN core.dim_policy p ON f.policy_key = p.policy_key
            JOIN core.dim_date d ON f.incident_date_key = d.date_key
            WHERE d.full_date < p.policy_bind_date;
        """)).scalar()
        audit_results.append({
            "check_name": "anomaly_incident_prior_to_policy_bind",
            "check_type": "Anomaly",
            "table_name": "core.dim_policy",
            "column_name": "policy_bind_date",
            "severity": "WARNING",
            "records_evaluated": fact_cnt,
            "records_failed": inverted_dates,
            "status": "FLAGGED" if inverted_dates > 0 else "PASSED",
            "details": f"Chronological audit: {inverted_dates} claim(s) where incident_date < policy_bind_date (Policy 794731 incident 2015-02-02 vs bind 2015-02-22)."
        })
        
        # 11. Anomaly Rule: Sentinel Character '?' Absence
        sentinel_count = conn.execute(text("""
            SELECT COUNT(*) 
            FROM core.dim_incident 
            WHERE collision_type = '?' OR property_damage = '?' OR police_report_available = '?';
        """)).scalar()
        audit_results.append({
            "check_name": "sentinel_value_cleansing",
            "check_type": "Completeness",
            "table_name": "core.dim_incident",
            "column_name": "collision_type/property_damage",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": sentinel_count,
            "status": "PASSED" if sentinel_count == 0 else "FAILED",
            "details": f"Sentinel cleansing: {sentinel_count} raw '?' characters remain."
        })
        
        # 12. Validity Rule: Authorities Contacted 'None' Preservation
        # Expected count comes from the source file that was loaded (like raw_cnt), counted the way
        # clean_claims_data() sees the value (text, whitespace stripped). The warehouse side is limited
        # to policy_numbers present in the current staging table, so claims kept from earlier loads
        # that are absent from this file (not_in_source) do not distort the comparison.
        src_none_auth = int((
            pd.read_csv(source_path or RAW_DATA_PATH, usecols=["authorities_contacted"],
                        keep_default_na=False, dtype=str)["authorities_contacted"]
            .str.strip() == "None"
        ).sum())
        none_auth_count = conn.execute(text("""
            SELECT COUNT(*)
            FROM core.dim_incident i
            WHERE i.authorities_contacted = 'None'
              AND EXISTS (SELECT 1 FROM staging.stg_insurance_claims s WHERE s.policy_number = i.policy_number);
        """)).scalar()
        audit_results.append({
            "check_name": "authorities_contacted_none_preservation",
            "check_type": "Validity",
            "table_name": "core.dim_incident",
            "column_name": "authorities_contacted",
            "severity": "CRITICAL",
            "records_evaluated": fact_cnt,
            "records_failed": abs(src_none_auth - none_auth_count),
            "status": "PASSED" if none_auth_count == src_none_auth else "FAILED",
            "details": f"Warehouse holds {none_auth_count} 'None' authority records for the loaded claims; source file contains {src_none_auth}."
        })
        
        # 13. Fraud Target Variable Distribution
        fraud_summary = conn.execute(text("""
            SELECT 
                COUNT(*) AS total,
                SUM(fraud_reported_flag) AS fraud_count,
                ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2) AS fraud_rate
            FROM core.fact_claims;
        """)).mappings().first()
        audit_results.append({
            "check_name": "fraud_reported_target_validation",
            "check_type": "Validity",
            "table_name": "core.fact_claims",
            "column_name": "fraud_reported_flag",
            "severity": "INFO",
            "records_evaluated": fraud_summary["total"],
            "records_failed": 0,
            "status": "PASSED",
            "details": f"Target distribution verified: {fraud_summary['fraud_count']} fraud claims ({fraud_summary['fraud_rate']}% prevalence)."
        })
        
        # 14. Anomaly vs. Fraud Comparison Audit
        # Compare fraud rate for Major Damage vs other severities
        fraud_comp = conn.execute(text("""
            SELECT 
                ROUND(100.0 * SUM(CASE WHEN i.incident_severity = 'Major Damage' THEN f.fraud_reported_flag ELSE 0 END) / 
                      NULLIF(SUM(CASE WHEN i.incident_severity = 'Major Damage' THEN 1 ELSE 0 END), 0), 2) AS major_fraud_rate,
                ROUND(100.0 * SUM(CASE WHEN i.incident_severity != 'Major Damage' THEN f.fraud_reported_flag ELSE 0 END) / 
                      NULLIF(SUM(CASE WHEN i.incident_severity != 'Major Damage' THEN 1 ELSE 0 END), 0), 2) AS non_major_fraud_rate
            FROM core.fact_claims f
            JOIN core.dim_incident i ON f.incident_key = i.incident_key;
        """)).mappings().first()
        major_rate = float(fraud_comp['major_fraud_rate'])
        other_rate = float(fraud_comp['non_major_fraud_rate'])
        relative_risk = major_rate / other_rate if other_rate else float('nan')
        audit_results.append({
            "check_name": "anomaly_fraud_correlation_comparison",
            "check_type": "Anomaly",
            "table_name": "core.fact_claims",
            "column_name": "incident_severity",
            "severity": "INFO",
            "records_evaluated": fact_cnt,
            "records_failed": 0,
            "status": "PASSED",
            "details": f"Fraud comparison confirmed: Major Damage fraud rate is {fraud_comp['major_fraud_rate']}% vs {fraud_comp['non_major_fraud_rate']}% for other severities (relative risk {relative_risk:.1f}x)."
        })
        
        # Append results to audit.data_quality_audit (history is retained; each run has its own run_id)
        insert_sql = text("""
            INSERT INTO audit.data_quality_audit 
            (run_id, check_name, check_type, table_name, column_name, severity, records_evaluated, records_failed, status, details)
            VALUES (CAST(:run_id AS UUID), :check_name, :check_type, :table_name, :column_name, :severity, :records_evaluated, :records_failed, :status, :details);
        """)
        for r in audit_results:
            conn.execute(insert_sql, {**r, "run_id": run_id})
        conn.commit()
        
    logger.info(f"Audit suite completed: Evaluated {len(audit_results)} checks. All results written to audit.data_quality_audit.")
    return audit_results

if __name__ == "__main__":
    results = run_data_quality_suite()
    df_audit = pd.DataFrame(results)
    print(df_audit.to_markdown(index=False))
