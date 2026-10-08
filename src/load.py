"""
Idempotent incremental load into PostgreSQL 16.

Everything in load_to_warehouse() runs in ONE transaction (engine.begin()): any failure rolls
the whole load back. Natural key of the model is policy_number.

Per dimension and for the fact table, two statements are run:
  1. UPDATE ... FROM staging  -> only rows whose attributes changed (IS DISTINCT FROM), SCD Type 1
  2. INSERT ... SELECT ... WHERE NOT EXISTS -> only new rows, in source file order
INSERT ... ON CONFLICT DO UPDATE is deliberately NOT used with SERIAL columns: it burns a sequence
value for every existing row and leaves gaps in the surrogate keys.

Rows already in the warehouse whose policy_number is absent from the source are never deleted;
they are only counted (not_in_source).
"""
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from src.config import SQL_DIR, logger

STAGING = "staging.stg_insurance_claims"

# (target column, expression over staging alias "s") for each dimension. The natural key
# policy_number is handled separately. Column names match the staging table.
DIMENSIONS: Dict[str, List[Tuple[str, str]]] = {
    "dim_customer": [(c, f"s.{c}") for c in (
        "age", "age_group", "insured_sex", "insured_education_level", "insured_occupation",
        "insured_hobbies", "insured_relationship", "insured_zip")],
    "dim_policy": [(c, f"s.{c}") for c in (
        "policy_bind_date", "policy_state", "policy_csl", "policy_deductible",
        "policy_annual_premium", "months_as_customer", "tenure_years", "tenure_group")],
    "dim_incident": [(c, f"s.{c}") for c in (
        "incident_type", "collision_type", "incident_severity", "authorities_contacted",
        "incident_state", "incident_city", "incident_location", "incident_hour_of_the_day",
        "incident_time_window", "property_damage", "police_report_available")],
    "dim_vehicle": [(c, f"s.{c}") for c in (
        "auto_make", "auto_model", "auto_year", "vehicle_age_at_incident")],
}

# Fact columns: dimension surrogate keys are resolved by joining the dimensions on policy_number.
FACT_COLUMNS: List[Tuple[str, str]] = (
    [("incident_date_key", "s.incident_date_key"),
     ("policy_bind_date_key", "s.policy_bind_date_key"),
     ("customer_key", "c.customer_key"),
     ("policy_key", "p.policy_key"),
     ("incident_key", "i.incident_key"),
     ("vehicle_key", "v.vehicle_key")]
    + [(c, f"s.{c}") for c in (
        "total_claim_amount", "injury_claim", "property_claim", "vehicle_claim",
        "policy_annual_premium", "policy_deductible", "umbrella_limit", "capital_gains",
        "capital_loss", "number_of_vehicles_involved", "bodily_injuries", "witnesses",
        "fraud_reported_flag")]
    + [("fraud_reported_desc", "s.fraud_reported"),
       ("claims_to_premium_multiple", "s.claims_to_premium_multiple")]
)

FACT_FROM = (
    f"{STAGING} s "
    "JOIN core.dim_customer c ON c.policy_number = s.policy_number "
    "JOIN core.dim_policy   p ON p.policy_number = s.policy_number "
    "JOIN core.dim_incident i ON i.policy_number = s.policy_number "
    "JOIN core.dim_vehicle  v ON v.policy_number = s.policy_number"
)

DIM_DATE_COLUMNS = ["date_key", "full_date", "year", "quarter", "month",
                    "month_name", "day", "day_of_week", "day_name", "is_weekend"]
DIM_DATE_TYPES = ["int", "date", "int", "int", "int", "text", "int", "int", "text", "boolean"]


# --------------------------------------------------------------------------- SQL file helpers
def _run_sql_file(conn: Connection, file_path: Path) -> None:
    logger.info(f"Executing SQL script: {Path(file_path).name}...")
    conn.execute(text(Path(file_path).read_text(encoding="utf-8")))


def execute_sql_file(engine: Engine, file_path: Path) -> None:
    """Executes a SQL script file (possibly many statements) in its own transaction."""
    with engine.begin() as conn:
        _run_sql_file(conn, file_path)
    logger.info(f"Successfully executed {Path(file_path).name}.")


def init_database_schema(engine: Engine) -> None:
    """Idempotent DDL from sql/01_init_schema.sql."""
    execute_sql_file(engine, SQL_DIR / "01_init_schema.sql")


def create_analytical_views(engine: Engine) -> None:
    """Builds marts analytical views from sql/02_create_views.sql."""
    execute_sql_file(engine, SQL_DIR / "02_create_views.sql")


def create_outlier_checks(engine: Engine) -> None:
    """Builds the statistical outlier-check views from sql/04_outlier_checks.sql (not a fraud indicator)."""
    execute_sql_file(engine, SQL_DIR / "04_outlier_checks.sql")


def create_fraud_risk_score(engine: Engine) -> None:
    """Builds marts.fraud_risk_score (rule-based fraud risk score) from sql/05_fraud_risk_score.sql."""
    execute_sql_file(engine, SQL_DIR / "05_fraud_risk_score.sql")


# --------------------------------------------------------------------------- SQL builders
def _update_sql(target: str, cols: List[Tuple[str, str]], from_clause: str) -> str:
    sets = ", ".join(f"{c} = {e}" for c, e in cols) + ", updated_at = now()"
    lhs = ", ".join(f"t.{c}" for c, _ in cols)
    rhs = ", ".join(e for _, e in cols)
    return (
        f"UPDATE {target} t SET {sets} FROM {from_clause} "
        f"WHERE t.policy_number = s.policy_number "
        f"AND ({lhs}) IS DISTINCT FROM ({rhs}) "
        f"RETURNING t.policy_number"
    )


def _insert_sql(target: str, cols: List[Tuple[str, str]], from_clause: str,
                extra_leading: Optional[Tuple[str, str]] = None) -> str:
    names = ["policy_number"] + [c for c, _ in cols]
    exprs = ["s.policy_number"] + [e for _, e in cols]
    if extra_leading:  # e.g. claim_id, computed only for new rows
        names.insert(0, extra_leading[0])
        exprs.insert(0, extra_leading[1])
    return (
        f"INSERT INTO {target} ({', '.join(names)}) "
        f"SELECT {', '.join(exprs)} FROM {from_clause} "
        f"WHERE NOT EXISTS (SELECT 1 FROM {target} t WHERE t.policy_number = s.policy_number) "
        f"ORDER BY s.source_row_number "
        f"RETURNING policy_number"
    )


# claim_id for NEW rows only, continuing after the current maximum (CLM-10001 on an empty table).
CLAIM_ID_EXPR = (
    "'CLM-' || ((SELECT COALESCE(MAX(substring(claim_id from 5)::int), 10000) FROM core.fact_claims)"
    " + ROW_NUMBER() OVER (ORDER BY s.source_row_number))::text"
)


# --------------------------------------------------------------------------- load steps
def _load_staging(conn: Connection, df_stg: pd.DataFrame) -> int:
    """Landing zone: truncate and reload every run (inside the load transaction)."""
    conn.execute(text(f"TRUNCATE TABLE {STAGING};"))
    df_stg.to_sql(name="stg_insurance_claims", schema="staging", con=conn,
                  if_exists="append", index=False, chunksize=500, method="multi")
    n = conn.execute(text(f"SELECT COUNT(*) FROM {STAGING}")).scalar()
    if n != len(df_stg):
        raise RuntimeError(f"Staging holds {n} rows but {len(df_stg)} were loaded.")
    return int(n)


def _load_dim_date(conn: Connection, dim_date: pd.DataFrame) -> Dict[str, int]:
    """INSERT ... ON CONFLICT (date_key) DO NOTHING; dim_date has no SERIAL, so no gaps arise."""
    params = {
        "date_key": dim_date["date_key"].tolist(),
        "full_date": dim_date["full_date"].dt.date.tolist(),
        "year": dim_date["year"].tolist(),
        "quarter": dim_date["quarter"].tolist(),
        "month": dim_date["month"].tolist(),
        "month_name": dim_date["month_name"].tolist(),
        "day": dim_date["day"].tolist(),
        "day_of_week": dim_date["day_of_week"].tolist(),
        "day_name": dim_date["day_name"].tolist(),
        "is_weekend": [bool(x) for x in dim_date["is_weekend"].tolist()],
    }
    args = ", ".join(f"CAST(:{c} AS {t}[])" for c, t in zip(DIM_DATE_COLUMNS, DIM_DATE_TYPES))
    cols = ", ".join(DIM_DATE_COLUMNS)
    sql = (f"INSERT INTO core.dim_date ({cols}) "
           f"SELECT {cols} FROM unnest({args}) AS u({cols}) "
           f"ON CONFLICT (date_key) DO NOTHING RETURNING date_key")
    inserted = len(conn.execute(text(sql), params).fetchall())
    return {"inserted": inserted, "updated": 0, "unchanged": len(dim_date) - inserted}


def _upsert(conn: Connection, target: str, update_sql: str, insert_sql: str, n_source: int) -> Dict[str, int]:
    updated = len(conn.execute(text(update_sql)).fetchall())
    inserted = len(conn.execute(text(insert_sql)).fetchall())
    unchanged = n_source - updated - inserted
    if unchanged < 0:
        raise RuntimeError(f"{target}: inserted+updated ({inserted + updated}) exceeds source rows ({n_source}).")
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def _count_not_in_source(conn: Connection) -> int:
    return int(conn.execute(text(
        f"SELECT COUNT(*) FROM core.fact_claims t "
        f"WHERE NOT EXISTS (SELECT 1 FROM {STAGING} s WHERE s.policy_number = t.policy_number)"
    )).scalar())


def load_to_warehouse(
    engine: Engine,
    tables: Dict[str, pd.DataFrame],
    full_refresh: bool = False,
    finalize: Optional[Callable[[Connection, dict], None]] = None,
) -> dict:
    """
    Incremental, idempotent load in a single transaction.

    full_refresh: inside the same transaction, first run sql/00_full_refresh_reset.sql (staging and
                  core tables only, never audit) and sql/01_init_schema.sql, so a failed refresh
                  rolls back to the previous warehouse instead of leaving it empty.
    finalize:     optional callback finalize(conn, result) executed inside the SAME transaction after
                  the load, used by the pipeline to mark the run SUCCESS atomically with the data.

    Returns {table: {inserted, updated, unchanged}, ..., "not_in_source": int, "source_rows": int}.
    """
    result: dict = {}
    with engine.begin() as conn:
        if full_refresh:
            logger.info("Full refresh: dropping staging and core tables (audit untouched)...")
            _run_sql_file(conn, SQL_DIR / "00_full_refresh_reset.sql")
            _run_sql_file(conn, SQL_DIR / "01_init_schema.sql")

        n_source = _load_staging(conn, tables["stg_insurance_claims"])
        logger.info(f"Staging reloaded: {n_source:,} rows.")

        result["dim_date"] = _load_dim_date(conn, tables["dim_date"])

        for dim, cols in DIMENSIONS.items():
            target = f"core.{dim}"
            result[dim] = _upsert(
                conn, target,
                _update_sql(target, cols, f"{STAGING} s"),
                _insert_sql(target, cols, f"{STAGING} s"),
                n_source,
            )

        result["fact_claims"] = _upsert(
            conn, "core.fact_claims",
            _update_sql("core.fact_claims", FACT_COLUMNS, FACT_FROM),
            _insert_sql("core.fact_claims", FACT_COLUMNS, FACT_FROM,
                        extra_leading=("claim_id", CLAIM_ID_EXPR)),
            n_source,
        )

        result["not_in_source"] = _count_not_in_source(conn)
        result["source_rows"] = n_source

        for name, c in result.items():
            if isinstance(c, dict):
                logger.info(f"  {name}: inserted={c['inserted']:,} updated={c['updated']:,} unchanged={c['unchanged']:,}")
        if result["not_in_source"]:
            logger.warning(f"{result['not_in_source']:,} warehouse claim(s) are absent from the source; kept, not deleted.")
        else:
            logger.info("not_in_source: 0")

        if finalize is not None:
            finalize(conn, result)
    return result


def verify_warehouse_counts(engine: Engine) -> Dict[str, int]:
    """Queries and returns row counts for all warehouse tables."""
    tables = [
        "staging.stg_insurance_claims", "core.dim_date", "core.dim_customer",
        "core.dim_policy", "core.dim_incident", "core.dim_vehicle", "core.fact_claims",
    ]
    counts = {}
    with engine.connect() as conn:
        for t in tables:
            res = conn.execute(text(f"SELECT COUNT(*) FROM {t};")).scalar()
            counts[t] = res
            logger.info(f"Verification: {t} -> {res:,} rows")
    return counts
