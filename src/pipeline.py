import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
import time
import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from src.config import get_engine, RAW_DATA_PATH, logger
from src.extract import extract_raw_claims
from src.transform import clean_claims_data, build_star_schema, validate_unique_policy_numbers
from src.load import (
    init_database_schema,
    create_analytical_views,
    create_outlier_checks,
    create_fraud_risk_score,
    load_to_warehouse,
    verify_warehouse_counts,
)
from src.data_quality import run_data_quality_suite


def wait_for_db(engine: Engine, max_retries: int = 15, delay: int = 2) -> bool:
    """Waits for PostgreSQL to be ready before proceeding."""
    logger.info("Checking PostgreSQL database connection...")
    for attempt in range(1, max_retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1;"))
            logger.info("Database connection established successfully.")
            return True
        except Exception as e:
            logger.warning(f"Connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)
    logger.error("Could not connect to PostgreSQL database after multiple attempts.")
    raise ConnectionError("Database unreachable.")


# --------------------------------------------------------------------------- run log
def _start_run_log(engine: Engine, run_id: str, mode: str, source_file: str) -> None:
    """Insert the RUNNING row in its own (committed) transaction."""
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO audit.pipeline_run_log (run_id, started_at, mode, source_file, status)
            VALUES (CAST(:run_id AS UUID), now(), :mode, :source_file, 'RUNNING')
        """), {"run_id": run_id, "mode": mode, "source_file": source_file})


def _make_finalize(run_id: str, rows_extracted: int, mode: str):
    """Callback run INSIDE the load transaction: marks the run SUCCESS atomically with the data."""
    def finalize(conn: Connection, result: dict) -> None:
        fact = result["fact_claims"]
        details = {"mode": mode, "tables": {k: v for k, v in result.items() if isinstance(v, dict)}}
        conn.execute(text("""
            UPDATE audit.pipeline_run_log
               SET status = 'SUCCESS', finished_at = now(), rows_extracted = :rows,
                   fact_inserted = :ins, fact_updated = :upd, fact_unchanged = :unch,
                   not_in_source = :nis, details = CAST(:details AS JSONB), error_message = NULL
             WHERE run_id = CAST(:run_id AS UUID)
        """), {"run_id": run_id, "rows": rows_extracted, "ins": fact["inserted"], "upd": fact["updated"],
               "unch": fact["unchanged"], "nis": result["not_in_source"], "details": json.dumps(details)})
    return finalize


def _mark_failed(engine: Engine, run_id: str, error: BaseException, load_committed: bool) -> None:
    """Separate transaction, after the load has rolled back: record FAILED and the error message."""
    message = f"{type(error).__name__}: {error}"
    if load_committed:
        message = "Post-load step failed (the load itself was committed). " + message
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE audit.pipeline_run_log
                   SET status = 'FAILED', finished_at = now(), error_message = :msg,
                       details = COALESCE(details, '{}'::jsonb) || CAST(:d AS JSONB)
                 WHERE run_id = CAST(:run_id AS UUID)
            """), {"run_id": run_id, "msg": message, "d": json.dumps({"load_committed": load_committed})})
    except Exception:
        logger.exception("Could not record FAILED status in audit.pipeline_run_log.")


# --------------------------------------------------------------------------- pipeline
def run_pipeline(source_path: Optional[Path] = None, full_refresh: bool = False,
                 engine: Optional[Engine] = None) -> dict:
    """Executes the End-to-End Insurance Claims Analytics Pipeline (incremental by default)."""
    total_start_time = time.time()
    source_path = Path(source_path) if source_path else Path(RAW_DATA_PATH)
    mode = "full_refresh" if full_refresh else "incremental"
    run_id = str(uuid.uuid4())
    own_engine = engine is None
    engine = engine or get_engine()

    logger.info("==========================================================")
    logger.info(f"STARTING INSURANCE CLAIMS ANALYTICS PIPELINE (mode={mode}, run_id={run_id})")
    logger.info(f"Source: {source_path}")
    logger.info("==========================================================")

    run_logged = False
    load_committed = False
    try:
        wait_for_db(engine)

        logger.info("[Step 1/7] Initializing database schemas and tables (idempotent DDL)...")
        init_database_schema(engine)
        # The run is logged BEFORE extraction/validation so that a source that fails validation
        # (missing file, duplicate policy_number, ...) leaves a FAILED row with its error message.
        _start_run_log(engine, run_id, mode, str(source_path))
        run_logged = True

        # Extract, clean and validate in memory: still fails fast BEFORE any warehouse data is
        # touched (staging/core are only modified inside the load transaction below).
        logger.info("[Step 2/7] Extracting and validating source data...")

        df_raw = extract_raw_claims(source_path)
        df_clean = clean_claims_data(df_raw)

        # Record duplicate source rows before the existing fail-fast validation.
        duplicate_rows = df_clean[
            df_clean["policy_number"].duplicated(keep=False)
        ].copy()

        if not duplicate_rows.empty:
            with engine.begin() as reject_conn:
                rejected_records = [
                    {
                        "run_id": run_id,
                        "source_row_number": row["source_row_number"],
                        "policy_number": row["policy_number"],
                        "rejection_reason": "Duplicate policy_number",
                    }
                    for _, row in duplicate_rows.iterrows()
                ]

                reject_conn.execute(
                    text("""
                        INSERT INTO staging.rejected_rows
                        (
                            run_id,
                            source_row_number,
                            policy_number,
                            rejection_reason
                        )
                        VALUES
                        (
                            :run_id,
                            :source_row_number,
                            :policy_number,
                            :rejection_reason
                        )
                    """),
                    rejected_records,
                )

        validate_unique_policy_numbers(df_clean)
        tables_dict = build_star_schema(df_clean)

        # One transaction: (optional reset) + staging + dimensions + fact + run log SUCCESS
        logger.info("[Step 3/7] Loading warehouse in a single transaction...")
        result = load_to_warehouse(
            engine, tables_dict, full_refresh=full_refresh,
            finalize=_make_finalize(run_id, len(df_raw), mode),
        )
        load_committed = True

        # Everything below runs AFTER the load transaction has committed and is NOT part of it: a failure
        # here keeps the committed load and flips the run to FAILED (load_committed=true) in _mark_failed().
        logger.info("[Step 4/7] Creating marts analytical and reporting views...")
        create_analytical_views(engine)

        logger.info("[Step 5/7] Creating outlier-check views and the fraud risk score view...")
        create_outlier_checks(engine)
        create_fraud_risk_score(engine)

        logger.info("[Step 6/7] Executing Data Quality & Anomaly Suite (audit.data_quality_audit)...")
        run_data_quality_suite(engine, run_id=run_id, source_path=source_path)

        logger.info("[Step 7/7] Reconciling warehouse against the source...")
        counts = verify_warehouse_counts(engine)
        with engine.connect() as conn:
            matched = conn.execute(text("""
                SELECT COUNT(*) FROM core.fact_claims f
                JOIN staging.stg_insurance_claims s ON s.policy_number = f.policy_number
            """)).scalar()
            kpis = conn.execute(text("""
                SELECT 
                    COUNT(*) AS total_claims,
                    SUM(total_claim_amount) AS total_incurred,
                    ROUND(AVG(total_claim_amount), 2) AS avg_claim,
                    SUM(fraud_reported_flag) AS fraud_claims,
                    ROUND(100.0 * SUM(fraud_reported_flag) / COUNT(*), 2) AS fraud_rate_pct,
                    ROUND(SUM(total_claim_amount) / NULLIF(SUM(policy_annual_premium), 0), 2) AS claims_to_premium_multiple
                FROM core.fact_claims;
            """)).mappings().first()
        expected_rows = len(df_raw)
        if matched != expected_rows:
            raise RuntimeError(f"Expected {expected_rows:,} source claims in the fact table, found {matched:,}")
        for dim in ("dim_customer", "dim_policy", "dim_incident", "dim_vehicle"):
            if counts[f"core.{dim}"] < expected_rows:
                raise RuntimeError(f"core.{dim} has fewer rows ({counts[f'core.{dim}']:,}) than the source ({expected_rows:,})")
    except BaseException as e:
        if run_logged:
            _mark_failed(engine, run_id, e, load_committed)
        raise
    finally:
        if own_engine:
            engine.dispose()

    logger.info("==========================================================")
    logger.info("PIPELINE EXECUTION COMPLETED SUCCESSFULLY!")
    logger.info(f"Run ID:                {run_id}  (mode={mode})")
    logger.info(f"Total Processing Time: {time.time() - total_start_time:.2f} seconds")
    fc = result["fact_claims"]
    logger.info(f"Fact claims:           inserted={fc['inserted']:,} updated={fc['updated']:,} "
                f"unchanged={fc['unchanged']:,} not_in_source={result['not_in_source']:,}")
    logger.info(f"Loaded Claims:         {kpis['total_claims']:,}")
    logger.info(f"Total Incurred Loss:   ${kpis['total_incurred']:,.2f}")
    logger.info(f"Average Claim Amount:  ${kpis['avg_claim']:,.2f}")
    logger.info(f"Fraudulent Claims:     {kpis['fraud_claims']:,} ({kpis['fraud_rate_pct']}%)")
    logger.info(f"Claims-to-Premium:    {kpis['claims_to_premium_multiple']}x (relative indicator, not a loss ratio)")
    logger.info("==========================================================")
    result["run_id"] = run_id
    return result


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Insurance claims incremental ETL pipeline.")
    parser.add_argument("--source", type=Path, default=None,
                        help=f"Source CSV path (default: configured RAW_DATA_PATH = {RAW_DATA_PATH}).")
    parser.add_argument("--full-refresh", action="store_true",
                        help="Drop staging and core tables (never audit) and rebuild, then load.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        run_pipeline(source_path=args.source, full_refresh=args.full_refresh)
    except Exception as e:
        logger.exception(f"Pipeline failed with error: {e}")
        sys.exit(1)
