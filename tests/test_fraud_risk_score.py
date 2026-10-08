"""Tests for the fraud risk score view, the renamed claims-to-premium multiple and the outlier checks.

Requires the pipeline to have run against a live PostgreSQL database.
Tests are skipped if the database is not reachable.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.config import get_engine

MAX_SCORE = 3 + 1 + 1 + 1  # must match the weights in sql/05_fraud_risk_score.sql
ALLOWED_BANDS = {"High", "Medium", "Low"}


@pytest.fixture(scope="module")
def conn():
    engine = get_engine()
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable")
    yield connection
    connection.close()


def _scalar(conn, sql: str):
    return conn.execute(text(sql)).scalar()


def test_one_row_per_claim(conn):
    fact_rows = _scalar(conn, "SELECT COUNT(*) FROM core.fact_claims")
    view_rows = _scalar(conn, "SELECT COUNT(*) FROM marts.fraud_risk_score")
    distinct_claims = _scalar(conn, "SELECT COUNT(DISTINCT claim_key) FROM marts.fraud_risk_score")
    assert view_rows == fact_rows
    assert distinct_claims == fact_rows


def test_no_nulls_in_score_or_band(conn):
    nulls = _scalar(
        conn,
        "SELECT COUNT(*) FROM marts.fraud_risk_score WHERE risk_score IS NULL OR risk_band IS NULL",
    )
    assert nulls == 0


def test_score_within_bounds(conn):
    low, high = conn.execute(
        text("SELECT MIN(risk_score), MAX(risk_score) FROM marts.fraud_risk_score")
    ).one()
    assert low >= 0
    assert high <= MAX_SCORE


def test_every_claim_has_exactly_one_valid_band(conn):
    bands = {r[0] for r in conn.execute(text("SELECT DISTINCT risk_band FROM marts.fraud_risk_score"))}
    assert bands <= ALLOWED_BANDS
    unbanded = _scalar(
        conn,
        "SELECT COUNT(*) FROM marts.fraud_risk_score WHERE risk_band NOT IN ('High','Medium','Low')",
    )
    assert unbanded == 0


def test_fraud_rate_increases_with_risk_band(conn):
    rows = conn.execute(
        text(
            "SELECT risk_band, AVG(fraud_reported_flag)::float "
            "FROM marts.fraud_risk_score GROUP BY risk_band"
        )
    ).all()
    rate = {band: r for band, r in rows}
    assert rate["High"] > rate["Medium"] > rate["Low"]


def test_claims_to_premium_column_renamed(conn):
    cols = {
        r[0]
        for r in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'core' AND table_name = 'fact_claims'"
            )
        )
    }
    assert "claims_to_premium_multiple" in cols
    assert "loss_ratio" not in cols


def test_outlier_checks_have_no_repeat_claimant_rule(conn):
    views = {
        r[0]
        for r in conn.execute(
            text("SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")
        )
    }
    assert "outlier_flags" in views
    assert not any("repeat" in v for v in views)
