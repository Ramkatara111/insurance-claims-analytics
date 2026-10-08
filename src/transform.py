import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Tuple
from src.config import PROCESSED_DATA_PATH, logger

def validate_unique_policy_numbers(df: pd.DataFrame, key: str = "policy_number") -> None:
    """
    Fail fast if the source contains duplicate natural keys.
    Pure in-memory check: call it before touching the warehouse.
    Raises ValueError listing the duplicated values and how many times each occurs.
    """
    if key not in df.columns:
        raise ValueError(f"Natural key column '{key}' is missing from the source data.")
    counts = df[key].value_counts()
    dupes = counts[counts > 1]
    if not dupes.empty:
        listing = ", ".join(f"{k} (x{v})" for k, v in dupes.sort_index().items())
        raise ValueError(
            f"Source contains {len(dupes)} duplicated {key} value(s): {listing}. "
            f"Refusing to load; {key} must be unique."
        )

def clean_claims_data(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Applies data hygiene, anomaly corrections, normalization, and business feature derivation.
    """
    df = df_raw.copy()
    
    # 1. Drop junk trailing column _c39 if present
    if "_c39" in df.columns:
        logger.info("Dropping artifact trailing column '_c39'...")
        df = df.drop(columns=["_c39"])
        
    # 2. Rename inconsistent columns to standard snake_case
    rename_map = {
        "capital-gains": "capital_gains",
        "capital-loss": "capital_loss",
        "policy_deductable": "policy_deductible"  # standardize spelling
    }
    df = df.rename(columns=rename_map)
    
    # 3. Clean string whitespace
    str_cols = df.select_dtypes(include=["object", "string"]).columns
    for c in str_cols:
        df[c] = df[c].astype(str).str.strip()
        
    # 4. Correct known categorical misspellings
    make_corrections = {
        "Suburu": "Subaru",
        "Accura": "Acura"
    }
    df["auto_make"] = df["auto_make"].replace(make_corrections)
    
    model_corrections = {
        "Forrestor": "Forester"
    }
    df["auto_model"] = df["auto_model"].replace(model_corrections)
    
    # 5. Handle missing value sentinels ('?')
    # In collision_type, '?' corresponds to Parked Car and Vehicle Theft
    df["collision_type"] = np.where(
        (df["collision_type"] == "?") & (df["incident_type"].isin(["Parked Car", "Vehicle Theft"])),
        "Not Applicable",
        np.where(df["collision_type"] == "?", "UNKNOWN", df["collision_type"])
    )
    df["property_damage"] = df["property_damage"].replace("?", "UNKNOWN")
    df["police_report_available"] = df["police_report_available"].replace("?", "UNKNOWN")
    
    # 6. Correct numeric anomalies
    # Anomaly: umbrella_limit has a single negative value (-1,000,000)
    df["umbrella_limit"] = pd.to_numeric(df["umbrella_limit"], errors="coerce")
    neg_umbrella_count = (df["umbrella_limit"] < 0).sum()
    if neg_umbrella_count > 0:
        logger.info(f"Clamping {neg_umbrella_count} negative umbrella_limit record(s) to 0...")
        df["umbrella_limit"] = df["umbrella_limit"].clip(lower=0)
        
    # Ensure numeric columns have correct types
    numeric_int_cols = [
        "months_as_customer", "age", "policy_number", "policy_deductible",
        "incident_hour_of_the_day", "number_of_vehicles_involved", "bodily_injuries",
        "witnesses", "total_claim_amount", "injury_claim", "property_claim",
        "vehicle_claim", "auto_year", "capital_gains", "capital_loss"
    ]
    for c in numeric_int_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
        
    df["policy_annual_premium"] = pd.to_numeric(df["policy_annual_premium"], errors="coerce").round(2)
    
    # 7. Date parsing
    df["policy_bind_dt"] = pd.to_datetime(df["policy_bind_date"])
    df["incident_dt"] = pd.to_datetime(df["incident_date"])
    
    df["policy_bind_date_key"] = df["policy_bind_dt"].dt.strftime("%Y%m%d").astype(int)
    df["incident_date_key"] = df["incident_dt"].dt.strftime("%Y%m%d").astype(int)
    
    # 8. Derived Business Dimensions & Cohorts
    # Age Groups
    bins_age = [0, 24, 34, 44, 54, 150]
    labels_age = ["<25", "25-34", "35-44", "45-54", "55+"]
    df["age_group"] = pd.cut(df["age"], bins=bins_age, labels=labels_age, right=True)
    
    # Customer Tenure Cohorts
    df["tenure_years"] = (df["months_as_customer"] / 12.0).round(2)
    bins_tenure = [-1, 23, 59, 119, 239, 1000]
    labels_tenure = ["<2 Years", "2-5 Years", "5-10 Years", "10-20 Years", "20+ Years"]
    df["tenure_group"] = pd.cut(df["months_as_customer"], bins=bins_tenure, labels=labels_tenure)
    
    # Incident Time Window
    def categorize_hour(h):
        if 0 <= h < 6:
            return "Night (00-06)"
        elif 6 <= h < 12:
            return "Morning (06-12)"
        elif 12 <= h < 17:
            return "Afternoon (12-17)"
        else:
            return "Evening (17-24)"
    df["incident_time_window"] = df["incident_hour_of_the_day"].apply(categorize_hour)
    
    # Vehicle age at incident
    df["vehicle_age_at_incident"] = df["incident_dt"].dt.year - df["auto_year"]
    
    # Fraud Target
    df["fraud_reported_flag"] = (df["fraud_reported"] == "Y").astype(int)
    df["fraud_reported_desc"] = df["fraud_reported"]
    
    # Claims-to-premium multiple at policy level (NOT an actuarial loss ratio:
    # premium is one year of annual premium, claims are total claim amounts)
    df["claims_to_premium_multiple"] = (df["total_claim_amount"] / df["policy_annual_premium"]).round(4)
    
    # 9. Source lineage: 1-based position of the row in the source file.
    # Surrogate keys and claim_id are NOT generated here; PostgreSQL assigns them (SERIAL).
    df["source_row_number"] = range(1, len(df) + 1)
    
    return df

def build_dim_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    Builds a unified Date dimension spanning all policy bind dates and incident dates.
    """
    dates_bind = pd.to_datetime(df["policy_bind_date"])
    dates_incident = pd.to_datetime(df["incident_date"])
    all_dates = pd.date_range(
        start=min(dates_bind.min(), dates_incident.min()),
        end=max(dates_bind.max(), dates_incident.max()),
        freq="D"
    )
    
    dim_date = pd.DataFrame({"full_date": all_dates})
    dim_date["date_key"] = dim_date["full_date"].dt.strftime("%Y%m%d").astype(int)
    dim_date["year"] = dim_date["full_date"].dt.year
    dim_date["quarter"] = dim_date["full_date"].dt.quarter
    dim_date["month"] = dim_date["full_date"].dt.month
    dim_date["month_name"] = dim_date["full_date"].dt.strftime("%B")
    dim_date["day"] = dim_date["full_date"].dt.day
    dim_date["day_of_week"] = dim_date["full_date"].dt.dayofweek + 1  # 1 = Monday
    dim_date["day_name"] = dim_date["full_date"].dt.strftime("%A")
    dim_date["is_weekend"] = dim_date["day_of_week"].isin([6, 7])
    
    # Reorder columns
    dim_date = dim_date[[
        "date_key", "full_date", "year", "quarter", "month", 
        "month_name", "day", "day_of_week", "day_name", "is_weekend"
    ]]
    return dim_date

def build_star_schema(df_clean: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Splits the cleaned dataset into Kimball dimensional star-schema tables.
    Tables carry the natural key (policy_number) only; surrogate keys (customer_key,
    policy_key, incident_key, vehicle_key, claim_key) are assigned by PostgreSQL and the
    fact table's dimension keys are resolved by the loader.
    """
    logger.info("Generating Kimball Star Schema data structures...")
    
    # 1. Dim Date
    dim_date = build_dim_date(df_clean)
    
    # 2. Dim Customer
    dim_customer = df_clean[[
        "policy_number", "age", "age_group", "insured_sex",
        "insured_education_level", "insured_occupation", "insured_hobbies",
        "insured_relationship", "insured_zip"
    ]].copy()
    dim_customer["insured_zip"] = dim_customer["insured_zip"].astype(str)
    
    # 3. Dim Policy
    dim_policy = df_clean[[
        "policy_number", "policy_bind_date", "policy_state",
        "policy_csl", "policy_deductible", "policy_annual_premium",
        "months_as_customer", "tenure_years", "tenure_group"
    ]].copy()
    dim_policy["policy_bind_date"] = pd.to_datetime(dim_policy["policy_bind_date"]).dt.date
    
    # 4. Dim Incident
    dim_incident = df_clean[[
        "policy_number", "incident_type", "collision_type", "incident_severity",
        "authorities_contacted", "incident_state", "incident_city",
        "incident_location", "incident_hour_of_the_day", "incident_time_window",
        "property_damage", "police_report_available"
    ]].copy()
    
    # 5. Dim Vehicle
    dim_vehicle = df_clean[[
        "policy_number", "auto_make", "auto_model", "auto_year", "vehicle_age_at_incident"
    ]].copy()
    
    # 6. Fact Claims
    fact_claims = df_clean[[
        "policy_number", "incident_date_key", "policy_bind_date_key",
        "total_claim_amount", "injury_claim", "property_claim", "vehicle_claim",
        "policy_annual_premium", "policy_deductible", "umbrella_limit",
        "capital_gains", "capital_loss", "number_of_vehicles_involved",
        "bodily_injuries", "witnesses", "fraud_reported_flag", "fraud_reported_desc",
        "claims_to_premium_multiple"
    ]].copy()
    
    # 7. Cleaned Staging Table (for raw staging ingestion)
    staging_cols = [
        "source_row_number", "months_as_customer", "age", "policy_number", "policy_bind_date", "policy_state",
        "policy_csl", "policy_deductible", "policy_annual_premium", "umbrella_limit",
        "insured_zip", "insured_sex", "insured_education_level", "insured_occupation",
        "insured_hobbies", "insured_relationship", "capital_gains", "capital_loss",
        "incident_date", "incident_type", "collision_type", "incident_severity",
        "authorities_contacted", "incident_state", "incident_city", "incident_location",
        "incident_hour_of_the_day", "number_of_vehicles_involved", "property_damage",
        "bodily_injuries", "witnesses", "police_report_available", "total_claim_amount",
        "injury_claim", "property_claim", "vehicle_claim", "auto_make", "auto_model",
        "auto_year", "fraud_reported",
        # derived columns computed once here so SQL loads reproduce them exactly
        "age_group", "tenure_years", "tenure_group", "incident_time_window",
        "vehicle_age_at_incident", "claims_to_premium_multiple", "fraud_reported_flag",
        "incident_date_key", "policy_bind_date_key"
    ]
    stg_claims = df_clean[staging_cols].copy()
    for _c in ("age_group", "tenure_group", "incident_time_window"):
        stg_claims[_c] = stg_claims[_c].astype(str)
    stg_claims["insured_zip"] = stg_claims["insured_zip"].astype(str)
    stg_claims["policy_bind_date"] = pd.to_datetime(stg_claims["policy_bind_date"]).dt.date
    stg_claims["incident_date"] = pd.to_datetime(stg_claims["incident_date"]).dt.date
    
    # Save processed outputs to disk for auditability and manual Power BI import
    PROCESSED_DATA_PATH.mkdir(parents=True, exist_ok=True)
    fact_claims.to_csv(PROCESSED_DATA_PATH / "fact_claims.csv", index=False)
    dim_customer.to_csv(PROCESSED_DATA_PATH / "dim_customer.csv", index=False)
    dim_policy.to_csv(PROCESSED_DATA_PATH / "dim_policy.csv", index=False)
    dim_incident.to_csv(PROCESSED_DATA_PATH / "dim_incident.csv", index=False)
    dim_vehicle.to_csv(PROCESSED_DATA_PATH / "dim_vehicle.csv", index=False)
    dim_date.to_csv(PROCESSED_DATA_PATH / "dim_date.csv", index=False)
    stg_claims.to_csv(PROCESSED_DATA_PATH / "stg_insurance_claims.csv", index=False)
    logger.info(f"Processed CSV exports written to {PROCESSED_DATA_PATH.resolve()}")
    
    return {
        "stg_insurance_claims": stg_claims,
        "dim_date": dim_date,
        "dim_customer": dim_customer,
        "dim_policy": dim_policy,
        "dim_incident": dim_incident,
        "dim_vehicle": dim_vehicle,
        "fact_claims": fact_claims
    }
