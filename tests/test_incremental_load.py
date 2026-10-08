"""End-to-end tests for the idempotent incremental load (PostgreSQL 16).

Every test gets its own throwaway database, insurance_dw_test_<uuid8>, created through the
'postgres' maintenance database (AUTOCOMMIT) and dropped afterwards. The real pipeline
(src.pipeline.run_pipeline) runs against it, so nothing here touches the warehouse configured
in DB_NAME. The tests are skipped if the database cannot be created.

Scenarios
    S1 fresh load                       S5 row removed from the source
    S2 re-run with the same file        S6 duplicate policy_number in the source
    S3 new rows appended to the source  S7 --full-refresh
    S4 one existing row changed
"""
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src import transform
from src.config import RAW_DATA_PATH, get_engine
from src.load import verify_warehouse_counts
from src.pipeline import run_pipeline

# Headline numbers of the unmodified source file (tests/test_pipeline.py checks the raw file itself).
HEADLINE = {
    "claims": 1000,
    "total_claim_amount": Decimal("52761940.00"),
    "fraud_claims": 247,
    "claims_to_premium_multiple": Decimal("41.99"),
}

SNAPSHOT_SQL = "SELECT claim_key, claim_id, policy_number FROM core.fact_claims ORDER BY claim_key"
WIDE_SNAPSHOT_SQL = (
    "SELECT claim_key, claim_id, policy_number, customer_key, policy_key, incident_key, vehicle_key "
    "FROM core.fact_claims ORDER BY claim_key"
)
HEADLINE_SQL = """
    SELECT COUNT(*) AS claims,
           SUM(total_claim_amount) AS total_claim_amount,
           SUM(fraud_reported_flag) AS fraud_claims,
           ROUND(SUM(total_claim_amount) / NULLIF(SUM(policy_annual_premium), 0), 2) AS claims_to_premium_multiple
    FROM core.fact_claims
"""


# --------------------------------------------------------------------------- fixtures
class Warehouse:
    """A throwaway database plus helpers to run the pipeline and query it."""

    def __init__(self, engine: Engine, name: str, workdir: Path):
        self.engine = engine
        self.name = name
        self.workdir = workdir

    def run(self, source: Path, full_refresh: bool = False) -> dict:
        return run_pipeline(source_path=source, full_refresh=full_refresh, engine=self.engine)

    def rows(self, sql: str) -> List[Tuple]:
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql)).fetchall()]

    def headline(self) -> Dict[str, object]:
        with self.engine.connect() as conn:
            return dict(conn.execute(text(HEADLINE_SQL)).mappings().one())

    def counts(self) -> Dict[str, int]:
        return verify_warehouse_counts(self.engine)

    def run_log(self) -> List[Tuple]:
        """(status, mode, error_message) of every run, oldest first."""
        return self.rows(
            "SELECT status, mode, error_message FROM audit.pipeline_run_log ORDER BY started_at, run_id"
        )

    def write_source(self, name: str, df: pd.DataFrame) -> Path:
        path = self.workdir / name
        df.to_csv(path, index=False)
        return path


@pytest.fixture
def wh(tmp_path, monkeypatch):
    # build_star_schema() also writes CSV exports; keep them out of the project's data/processed.
    monkeypatch.setattr(transform, "PROCESSED_DATA_PATH", tmp_path / "processed")

    db_name = f"insurance_dw_test_{uuid.uuid4().hex[:8]}"
    admin = get_engine("postgres").execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except Exception as exc:  # server down, wrong credentials, no CREATEDB privilege, ...
        admin.dispose()
        pytest.skip(f"Cannot create temporary database {db_name}: {exc}")

    engine = get_engine(db_name)
    try:
        yield Warehouse(engine, db_name, tmp_path)
    finally:
        engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        finally:
            admin.dispose()


# --------------------------------------------------------------------------- source-file helpers
def raw_source() -> pd.DataFrame:
    """The raw file as text, exactly as stored (no type inference, 'None' kept as text)."""
    return pd.read_csv(RAW_DATA_PATH, dtype=str, keep_default_na=False)


def source_with_new_rows(n: int = 5, first_policy_number: int = 900001) -> pd.DataFrame:
    """The raw file plus n copies of existing rows (the first n) carrying brand-new policy numbers."""
    df = raw_source()
    new = df.iloc[:n].copy()
    new["policy_number"] = [str(first_policy_number + i) for i in range(n)]
    return pd.concat([df, new], ignore_index=True)


def source_with_changed_total(row: int = 0, delta: int = 1000) -> Tuple[pd.DataFrame, int, int]:
    """Raise total_claim_amount of one row by delta, adding delta to injury_claim so the parts still sum.

    Returns (frame, policy_number of the changed row, its new total_claim_amount).
    """
    df = raw_source()
    new_total = int(df.at[row, "total_claim_amount"]) + delta
    df.at[row, "total_claim_amount"] = str(new_total)
    df.at[row, "injury_claim"] = str(int(df.at[row, "injury_claim"]) + delta)
    assert (int(df.at[row, "injury_claim"]) + int(df.at[row, "property_claim"])
            + int(df.at[row, "vehicle_claim"])) == new_total
    return df, int(df.at[row, "policy_number"]), new_total


def assert_headline(actual: Dict[str, object], expected: Dict[str, object] = HEADLINE) -> None:
    assert actual["claims"] == expected["claims"]
    assert Decimal(actual["total_claim_amount"]) == expected["total_claim_amount"]
    assert actual["fraud_claims"] == expected["fraud_claims"]
    assert Decimal(actual["claims_to_premium_multiple"]) == expected["claims_to_premium_multiple"]


def fact_counts(result: dict) -> Dict[str, int]:
    f = result["fact_claims"]
    return {"inserted": f["inserted"], "updated": f["updated"], "unchanged": f["unchanged"]}


# --------------------------------------------------------------------------- scenarios
def test_s1_fresh_load(wh):
    result = wh.run(RAW_DATA_PATH)

    assert fact_counts(result) == {"inserted": 1000, "updated": 0, "unchanged": 0}
    assert result["not_in_source"] == 0
    assert_headline(wh.headline())


def test_s2_rerun_same_file_is_a_noop(wh):
    wh.run(RAW_DATA_PATH)
    snapshot_s1 = wh.rows(SNAPSHOT_SQL)
    assert len(snapshot_s1) == 1000

    result = wh.run(RAW_DATA_PATH)

    assert fact_counts(result) == {"inserted": 0, "updated": 0, "unchanged": 1000}
    assert wh.rows(SNAPSHOT_SQL) == snapshot_s1


def test_s3_appended_rows_are_inserted_with_next_keys(wh):
    wh.run(RAW_DATA_PATH)
    snapshot_s1 = wh.rows(SNAPSHOT_SQL)

    result = wh.run(wh.write_source("source_plus_5.csv", source_with_new_rows(5)))

    assert fact_counts(result) == {"inserted": 5, "updated": 0, "unchanged": 1000}
    snapshot = wh.rows(SNAPSHOT_SQL)
    assert len(snapshot) == 1005

    # old rows keep their claim_key / claim_id / policy_number
    assert snapshot[:1000] == snapshot_s1

    new_rows = [r for r in snapshot if r[0] > 1000]
    # claim_keys 1001..1005 with no gaps
    assert sorted(r[0] for r in new_rows) == [1001, 1002, 1003, 1004, 1005]
    # claim_ids are exactly CLM-11001..CLM-11005, assigned in source-file order
    assert sorted(r[1] for r in new_rows) == [f"CLM-{11001 + i}" for i in range(5)]
    by_policy = sorted(new_rows, key=lambda r: r[2])
    assert [r[2] for r in by_policy] == [900001 + i for i in range(5)]
    assert [r[1] for r in by_policy] == [f"CLM-{11001 + i}" for i in range(5)]


def test_s4_changed_amount_is_updated_in_place(wh):
    wh.run(RAW_DATA_PATH)
    keys_s1 = wh.rows(WIDE_SNAPSHOT_SQL)
    total_s1 = Decimal(wh.headline()["total_claim_amount"])

    df, policy_number, new_total = source_with_changed_total(row=0, delta=1000)
    result = wh.run(wh.write_source("source_changed.csv", df))

    assert fact_counts(result) == {"inserted": 0, "updated": 1, "unchanged": 999}
    row = wh.rows(
        "SELECT total_claim_amount, injury_claim + property_claim + vehicle_claim "
        f"FROM core.fact_claims WHERE policy_number = {policy_number}"
    )
    assert row == [(Decimal(new_total), Decimal(new_total))]
    assert Decimal(wh.headline()["total_claim_amount"]) == total_s1 + 1000
    # claim_id and every surrogate key are untouched
    assert wh.rows(WIDE_SNAPSHOT_SQL) == keys_s1


def test_s5_row_missing_from_source_is_kept_and_counted(wh):
    wh.run(RAW_DATA_PATH)
    counts_s1 = wh.counts()
    snapshot_s1 = wh.rows(SNAPSHOT_SQL)

    df = raw_source().drop(index=10).reset_index(drop=True)
    assert len(df) == 999
    result = wh.run(wh.write_source("source_minus_1.csv", df))

    assert result["not_in_source"] == 1
    assert fact_counts(result) == {"inserted": 0, "updated": 0, "unchanged": 999}
    counts = wh.counts()
    # staging mirrors the (smaller) source; the warehouse tables keep every row
    assert counts["staging.stg_insurance_claims"] == 999
    for table in ("core.fact_claims", "core.dim_customer", "core.dim_policy",
                  "core.dim_incident", "core.dim_vehicle", "core.dim_date"):
        assert counts[table] == counts_s1[table], table
    assert counts["core.fact_claims"] == 1000
    assert wh.rows(SNAPSHOT_SQL) == snapshot_s1


def test_s6_duplicate_policy_number_fails_and_is_logged(wh):
    wh.run(RAW_DATA_PATH)
    counts_before = wh.counts()
    snapshot_before = wh.rows(SNAPSHOT_SQL)
    log_before = wh.run_log()
    assert [r[0] for r in log_before] == ["SUCCESS"]

    df = raw_source()
    dup_policy_number = df.at[0, "policy_number"]
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)  # same policy_number twice
    source = wh.write_source("source_duplicate.csv", df)

    with pytest.raises(ValueError, match=dup_policy_number):
        wh.run(source)

    log_after = wh.run_log()
    assert len(log_after) == len(log_before) + 1
    status, mode, error_message = log_after[-1]
    assert status == "FAILED"
    assert mode == "incremental"
    assert error_message and "duplicated" in error_message and dup_policy_number in error_message

    assert wh.counts() == counts_before
    assert wh.rows(SNAPSHOT_SQL) == snapshot_before


def test_s7_full_refresh_matches_fresh_load(wh):
    wh.run(RAW_DATA_PATH)  # S1
    headline_s1 = wh.headline()
    snapshot_s1 = wh.rows(SNAPSHOT_SQL)
    assert_headline(headline_s1)

    # Grow the warehouse past the original source so the refresh has something to throw away.
    wh.run(wh.write_source("source_plus_5.csv", source_with_new_rows(5)))
    assert len(wh.rows(SNAPSHOT_SQL)) == 1005

    result = wh.run(RAW_DATA_PATH, full_refresh=True)

    assert fact_counts(result) == {"inserted": 1000, "updated": 0, "unchanged": 0}
    assert result["not_in_source"] == 0
    assert wh.headline() == headline_s1
    assert_headline(wh.headline())
    assert wh.rows(SNAPSHOT_SQL) == snapshot_s1
    # the audit schema survives a refresh: three runs logged, the last one in full_refresh mode
    log = wh.run_log()
    assert [r[0] for r in log] == ["SUCCESS", "SUCCESS", "SUCCESS"]
    assert log[-1][1] == "full_refresh"


# --------------------------------------------------------------------------- follow-up checks
DQ_NONE_SQL = (
    "SELECT status, records_failed, details FROM audit.data_quality_audit "
    "WHERE check_name = 'authorities_contacted_none_preservation' "
    "ORDER BY checked_at DESC, audit_id DESC LIMIT 1"
)


def test_dq_authorities_none_expected_count_comes_from_source_file(wh):
    """The expected number of 'None' authorities is counted in the loaded file, not hard-coded."""
    original = int((raw_source()["authorities_contacted"] == "None").sum())
    df = raw_source()
    idx = df.index[df["authorities_contacted"] != "None"][0]
    df.at[idx, "authorities_contacted"] = "None"
    in_file = int((df["authorities_contacted"] == "None").sum())
    assert in_file == original + 1

    wh.run(wh.write_source("source_one_more_none.csv", df))

    status, failed, details = wh.rows(DQ_NONE_SQL)[0]
    assert (status, failed) == ("PASSED", 0)
    assert f"source file contains {in_file}" in details


def test_dq_authorities_none_check_ignores_rows_not_in_source(wh):
    """A claim kept in the warehouse but absent from the file must not distort the comparison."""
    wh.run(RAW_DATA_PATH)
    df = raw_source()
    idx = df.index[df["authorities_contacted"] == "None"][0]
    wh.run(wh.write_source("source_minus_none_row.csv", df.drop(index=idx).reset_index(drop=True)))

    status, failed, _ = wh.rows(DQ_NONE_SQL)[0]
    assert (status, failed) == ("PASSED", 0)


def test_post_load_failure_keeps_the_load_and_marks_run_failed(wh, monkeypatch):
    """Views / DQ / reconciliation run after the load commits: a failure there does not undo the load."""
    import src.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("dq exploded")

    monkeypatch.setattr(pipeline, "run_data_quality_suite", boom)
    with pytest.raises(RuntimeError, match="dq exploded"):
        wh.run(RAW_DATA_PATH)

    log = wh.rows("SELECT status, error_message, details->>'load_committed' FROM audit.pipeline_run_log")
    assert len(log) == 1
    status, message, load_committed = log[0]
    assert status == "FAILED"
    assert message.startswith("Post-load step failed (the load itself was committed).")
    assert load_committed == "true"
    assert len(wh.rows(SNAPSHOT_SQL)) == 1000  # the committed load is still there
