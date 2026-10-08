# Insurance Claims Data Dictionary

## 1. Overview & Data Architecture

This repository models the 1,000-record historical auto insurance claims dataset into a Kimball-style dimensional model (star schema) hosted in PostgreSQL.

* **Grain of Fact Table:** One record per insurance claim event.
* **Storage Layers:**
  1. `staging.stg_insurance_claims` (ELT Ingestion & raw audit layer)
  2. `core.*` (Kimball Dimensional Star Schema)
  3. `marts.*` (Aggregated business intelligence and analytical reporting views)
  4. `audit.*` (run log and data quality results; never dropped or truncated, not even by `--full-refresh`)

**Natural key and surrogate keys:** `policy_number` is the natural key of the whole model (one claim per policy in this dataset). It is `NOT NULL` and unique in `dim_customer`, `dim_policy`, `dim_incident`, `dim_vehicle` and `fact_claims`, and the incremental loader matches source rows to warehouse rows on it. Every `*_key` column is a `SERIAL` surrogate key assigned by PostgreSQL.

**Dimension change handling (SCD Type 1):** when a source value changes, the dimension row is overwritten in place, `updated_at` is set and the surrogate key stays the same. No history is kept.

**Timestamps:** every `core` table has `created_at` (set when the row is first inserted) and `updated_at` (set on insert and whenever the loader changes the row). Both are `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.

---

## 2. Core Fact Table (`core.fact_claims`)

| Column Name | SQL Type | Constraint | Description |
| :--- | :--- | :--- | :--- |
| `claim_key` | `SERIAL` | `PK` | Surrogate integer primary key, assigned by PostgreSQL |
| `claim_id` | `VARCHAR(50)` | `UNIQUE, NOT NULL` | Business identifier. Assigned only when a claim is first inserted, continuing after the current maximum (`CLM-10001` to `CLM-11000` for the first load of the 1,000-row file); never rewritten afterwards |
| `policy_number` | `BIGINT` | `NOT NULL, UNIQUE` | Natural key: policy identifier from the carrier system. One claim per policy |
| `incident_date_key` | `INT` | `FK -> dim_date` | Date of claim incident (`YYYYMMDD`) |
| `policy_bind_date_key` | `INT` | `FK -> dim_date` | Date policy originally bound (`YYYYMMDD`) |
| `customer_key` | `INT` | `FK -> dim_customer` | Insured customer surrogate key |
| `policy_key` | `INT` | `FK -> dim_policy` | Policy dimension surrogate key |
| `incident_key` | `INT` | `FK -> dim_incident` | Incident details surrogate key |
| `vehicle_key` | `INT` | `FK -> dim_vehicle` | Insured vehicle surrogate key |
| `total_claim_amount` | `NUMERIC(12,2)` | `NOT NULL` | Total claim payout: injury + property + vehicle ($) |
| `injury_claim` | `NUMERIC(12,2)` | `NOT NULL` | Bodily injury claim payout ($) |
| `property_claim` | `NUMERIC(12,2)` | `NOT NULL` | Third-party property damage payout ($) |
| `vehicle_claim` | `NUMERIC(12,2)` | `NOT NULL` | First-party vehicle collision damage payout ($) |
| `policy_annual_premium` | `NUMERIC(12,2)` | `NOT NULL` | Annual policyholder premium ($) |
| `policy_deductible` | `NUMERIC(12,2)` | `NOT NULL` | Policy deductible tier ($500, $1,000, $2,000) |
| `umbrella_limit` | `NUMERIC(14,2)` | `NOT NULL` | Umbrella liability limit ($0 to $10,000,000) |
| `capital_gains` | `NUMERIC(12,2)` | `NOT NULL` | Insured annual investment capital gains ($) |
| `capital_loss` | `NUMERIC(12,2)` | `NOT NULL` | Insured annual investment capital losses ($) |
| `number_of_vehicles_involved` | `INT` | `NOT NULL` | Count of vehicles involved in incident (1 to 4) |
| `bodily_injuries` | `INT` | `NOT NULL` | Number of injuries sustained (0 to 2) |
| `witnesses` | `INT` | `NOT NULL` | Count of independent witnesses present (0 to 3) |
| `fraud_reported_flag` | `INT` | `NOT NULL` | Binary indicator: `1` for Fraud, `0` for Legitimate |
| `fraud_reported_desc` | `VARCHAR(5)` | `NOT NULL` | Original flag: `'Y'` or `'N'` |
| `claims_to_premium_multiple` | `NUMERIC(10,4)` | `NOT NULL` | `total_claim_amount / policy_annual_premium`. A relative risk indicator, **not** an actuarial loss ratio: the dataset gives one year of annual premium against total claim amounts, so the value is far above real loss ratios (about 42x across the portfolio) |
| `created_at` | `TIMESTAMP` | `DEFAULT CURRENT_TIMESTAMP` | When the row was first inserted |
| `updated_at` | `TIMESTAMP` | `DEFAULT CURRENT_TIMESTAMP` | When the row was last changed by the loader (set on insert and on every UPDATE of a changed row) |

---

## 3. Dimension Tables (`core.*`)

### 3.1 `core.dim_customer`
| Column Name | Type | Description |
| :--- | :--- | :--- |
| `customer_key` | `SERIAL (PK)` | Surrogate key for insured individual |
| `policy_number` | `BIGINT (NOT NULL, UNIQUE)` | Natural key: policy identifier (the source has no customer id, so one customer row exists per policy) |
| `age` | `INT` | Age of insured at incident (19 to 64) |
| `age_group` | `VARCHAR(20)` | Cohort: `<25`, `25-34`, `35-44`, `45-54`, `55+` |
| `insured_sex` | `VARCHAR(10)` | Gender: `MALE`, `FEMALE` |
| `insured_education_level` | `VARCHAR(50)` | Highest education: `High School`, `Associate`, `College`, `Masters`, `JD`, `MD`, `PhD` |
| `insured_occupation` | `VARCHAR(50)` | Primary occupation (14 distinct categories) |
| `insured_hobbies` | `VARCHAR(50)` | Primary leisure hobby (20 distinct categories) |
| `insured_relationship` | `VARCHAR(50)` | Family relation role (`husband`, `wife`, `own-child`, `other-relative`, `not-in-family`, `unmarried`) |
| `insured_zip` | `VARCHAR(20)` | Residence ZIP code |
| `created_at`, `updated_at` | `TIMESTAMP` | Row first inserted / last changed by the loader |

### 3.2 `core.dim_policy`
| Column Name | Type | Description |
| :--- | :--- | :--- |
| `policy_key` | `SERIAL (PK)` | Surrogate key for policy record |
| `policy_number` | `BIGINT (NOT NULL, UNIQUE)` | Natural key: policy number |
| `policy_bind_date` | `DATE` | Inception date of the policy contract |
| `policy_state` | `VARCHAR(10)` | State where policy was underwritten (`OH`, `IL`, `IN`) |
| `policy_csl` | `VARCHAR(20)` | Combined Single Limits bodily/property: `100/300`, `250/500`, `500/1000` |
| `policy_deductible` | `NUMERIC(12,2)` | Deductible threshold ($) |
| `policy_annual_premium` | `NUMERIC(12,2)` | Annual premium charged ($) |
| `months_as_customer` | `INT` | Policyholder relationship tenure in months |
| `tenure_years` | `NUMERIC(6,2)` | Tenure expressed in years |
| `tenure_group` | `VARCHAR(20)` | Tenure cohort: `<2 Years`, `2-5 Years`, `5-10 Years`, `10-20 Years`, `20+ Years` |
| `created_at`, `updated_at` | `TIMESTAMP` | Row first inserted / last changed by the loader |

### 3.3 `core.dim_incident`
| Column Name | Type | Description |
| :--- | :--- | :--- |
| `incident_key` | `SERIAL (PK)` | Surrogate key for incident event |
| `policy_number` | `BIGINT (NOT NULL, UNIQUE)` | Natural key: policy the incident belongs to |
| `incident_type` | `VARCHAR(50)` | `Single Vehicle Collision`, `Multi-vehicle Collision`, `Vehicle Theft`, `Parked Car` |
| `collision_type` | `VARCHAR(50)` | `Front Collision`, `Rear Collision`, `Side Collision`, `Not Applicable` |
| `incident_severity` | `VARCHAR(50)` | `Minor Damage`, `Major Damage`, `Total Loss`, `Trivial Damage` |
| `authorities_contacted` | `VARCHAR(50)` | `Police`, `Fire`, `Ambulance`, `Other`, `None` |
| `incident_state` | `VARCHAR(10)` | Incident jurisdiction (`NY`, `SC`, `WV`, `VA`, `NC`, `PA`, `OH`) |
| `incident_city` | `VARCHAR(50)` | Municipality of accident |
| `incident_location` | `VARCHAR(100)` | Street location |
| `incident_hour_of_the_day` | `INT` | 24-hour incident timestamp (0–23) |
| `incident_time_window` | `VARCHAR(20)` | `Night (00-06)`, `Morning (06-12)`, `Afternoon (12-17)`, `Evening (17-24)` |
| `property_damage` | `VARCHAR(20)` | `YES`, `NO`, `UNKNOWN` |
| `police_report_available` | `VARCHAR(20)` | `YES`, `NO`, `UNKNOWN` |
| `created_at`, `updated_at` | `TIMESTAMP` | Row first inserted / last changed by the loader |

### 3.4 `core.dim_vehicle`
| Column Name | Type | Description |
| :--- | :--- | :--- |
| `vehicle_key` | `SERIAL (PK)` | Surrogate key for insured vehicle |
| `policy_number` | `BIGINT (NOT NULL, UNIQUE)` | Natural key: policy the vehicle is insured under |
| `auto_make` | `VARCHAR(50)` | Standardized vehicle make (`Subaru`, `Acura`, `BMW`, `Saab`, etc.) |
| `auto_model` | `VARCHAR(50)` | Vehicle model (`Forester`, `RAM`, `Wrangler`, `A3`, etc.) |
| `auto_year` | `INT` | Manufacture year (1995–2015) |
| `vehicle_age_at_incident` | `INT` | Vehicle age in years at date of incident |
| `created_at`, `updated_at` | `TIMESTAMP` | Row first inserted / last changed by the loader |

### 3.5 `core.dim_date`
Calendar dimension with a natural integer key; it has no `policy_number` and no sequence. Existing dates are never changed.

| Column Name | Type | Description |
| :--- | :--- | :--- |
| `date_key` | `INT (PK)` | Smart key in `YYYYMMDD` format |
| `full_date` | `DATE (UNIQUE)` | Gregorian calendar date |
| `year` | `INT` | Calendar year |
| `quarter` | `INT` | Quarter (1–4) |
| `month` | `INT` | Month number (1–12) |
| `month_name` | `VARCHAR(20)` | Full month name (e.g., `January`) |
| `day` | `INT` | Day of month (1–31) |
| `day_of_week` | `INT` | Day index (1 = Monday, 7 = Sunday) |
| `day_name` | `VARCHAR(20)` | Full day name (e.g., `Monday`) |
| `is_weekend` | `BOOLEAN` | `TRUE` if Saturday or Sunday |
| `created_at`, `updated_at` | `TIMESTAMP` | Row first inserted / last updated. `dim_date` is insert-only (`ON CONFLICT DO NOTHING`), so `updated_at` equals its insert time |

---

## 4. Staging and Audit Tables

### 4.1 `staging.stg_insurance_claims`
Landing table: truncated and reloaded from the source file at the start of every load transaction, so it always mirrors the file of the latest run (it is not history). It holds the cleaned source columns plus columns derived once in `transform.py` (`age_group`, `tenure_years`, `tenure_group`, `incident_time_window`, `vehicle_age_at_incident`, `claims_to_premium_multiple`, `fraud_reported_flag`, `incident_date_key`, `policy_bind_date_key`) so the SQL loads reproduce them exactly.

| Column Name | SQL Type | Description |
| :--- | :--- | :--- |
| `source_row_number` | `INT` | 1-based position of the row in the source file. The loader inserts new rows in this order, which is what makes `claim_id` assignment follow file order |
| `policy_number` | `BIGINT` | Natural key; must be unique in the file or the run fails before the load |
| `created_at` | `TIMESTAMP` | When the staging row was loaded |

### 4.2 `audit.pipeline_run_log`
One row per pipeline run. Never dropped or truncated.

| Column Name | SQL Type | Description |
| :--- | :--- | :--- |
| `run_id` | `UUID (PK)` | Run identifier; the same value is stored on the run's rows in `audit.data_quality_audit` |
| `started_at` | `TIMESTAMP` | Row inserted (in its own transaction) before extraction, so even a run that fails validation is logged |
| `finished_at` | `TIMESTAMP` | Set when the run is marked `SUCCESS` or `FAILED` |
| `mode` | `TEXT` | `incremental` or `full_refresh` (`CHECK` constraint) |
| `source_file` | `TEXT` | Path of the CSV that was loaded |
| `rows_extracted` | `INT` | Rows read from the source file |
| `fact_inserted`, `fact_updated`, `fact_unchanged` | `INT` | `core.fact_claims` rows inserted / changed / already identical |
| `not_in_source` | `INT` | Fact rows whose `policy_number` is not in the current file (kept, never deleted) |
| `details` | `JSONB` | `mode` plus inserted/updated/unchanged counts for every table; on failure also `load_committed` |
| `status` | `TEXT` | `RUNNING`, `SUCCESS` or `FAILED`. `SUCCESS` is written inside the load transaction, so it only exists together with committed data. If a later post-load step (views, data quality suite, reconciliation) raises, `SUCCESS` is overwritten with `FAILED` and `details.load_committed` is `true`; a failure inside the load transaction rolls back and is recorded as `FAILED` with `load_committed = false` |
| `error_message` | `TEXT` | Error type and message for a `FAILED` run; starts with "Post-load step failed (the load itself was committed)." when the failure happened after the commit |

### 4.3 `audit.data_quality_audit`
Results of the 14 data quality checks, appended on every run with that run's `run_id` (`UUID`, indexed), so history is retained. Columns: `audit_id`, `check_name`, `check_type`, `table_name`, `column_name`, `severity`, `records_evaluated`, `records_failed`, `status`, `details`, `checked_at`, `run_id`.

---

## 5. Analytical Views (`marts.*`)

1. **`marts.vw_claim_summary_by_state`**: State-level claims volume, total incurred loss, average claim size, fraud claim counts, fraud rate %, and claims-to-premium multiples.
2. **`marts.vw_fraud_risk_factors`**: Risk segmentation slicing claims and fraud frequencies by incident type, collision type, incident severity, and time of day.
3. **`marts.vw_vehicle_loss_profile`**: Vehicle loss severity, average vehicle damage claim, and fraud rates across manufacturer makes.
4. **`marts.vw_customer_demographic_analysis`**: Demographic risk segmentation across age brackets, gender, and education tiers.
5. **`marts.vw_claims_to_premium_by_segment`**: Claims-to-premium multiple (total claims / one year of annual premium; not an actuarial loss ratio) by policy state, CSL limit and tenure cohort.
6. **`marts.fraud_risk_score`**: One row per claim with a transparent rule-based fraud risk score (`risk_score` 0-6) and `risk_band` (High / Medium / Low). Rules: Major Damage +3, collision incident +1, tenure under 2 years +1, claims-to-premium multiple above the 75th percentile +1. Hobbies are deliberately excluded (non-causal lifestyle variables). Evaluated in-sample only.

### Outlier checks (`analytics.*`)

`analytics.outlier_mean_3sd`, `analytics.outlier_iqr`, `analytics.outlier_flags` and `analytics.outlier_fraud_metrics` flag unusually large claim amounts. They are **not** a fraud indicator: they flag one claim in this dataset and do not detect reported fraud. No repeat-claimant rule exists because the data has one claim per policy.

---

## 6. Source-to-Target Data Transformation Lineage

| Source Column | Target Column(s) | Transformation / Cleaning Rule Applied |
| :--- | :--- | :--- |
| `_c39` | *(None)* | **Dropped:** Empty trailing column from delimiter artifact |
| `capital-gains` | `fact_claims.capital_gains` | Standardized header from kebab-case to snake_case |
| `capital-loss` | `fact_claims.capital_loss` | Standardized header from kebab-case to snake_case |
| `policy_deductable` | `dim_policy.policy_deductible` | Corrected spelling (`deductable` $\rightarrow$ `deductible`) |
| `umbrella_limit` | `fact_claims.umbrella_limit` | Clamped negative value (`-1000000` $\rightarrow$ `0`) |
| `auto_make` | `dim_vehicle.auto_make` | Standardized typos (`Suburu` $\rightarrow$ `Subaru`, `Accura` $\rightarrow$ `Acura`) |
| `auto_model` | `dim_vehicle.auto_model` | Standardized typos (`Forrestor` $\rightarrow$ `Forester`) |
| `collision_type` | `dim_incident.collision_type` | Replaced `'?'` with `'Not Applicable'` (Theft/Parked) or `'UNKNOWN'` |
| `property_damage` | `dim_incident.property_damage` | Replaced sentinel `'?'` with `'UNKNOWN'` |
| `police_report_available` | `dim_incident.police_report_available` | Replaced sentinel `'?'` with `'UNKNOWN'` |
| `authorities_contacted` | `dim_incident.authorities_contacted` | Preserved literal string `'None'` (prevented coercion to NaN) |
| `fraud_reported` | `fact_claims.fraud_reported_flag` | Derived binary integer (`Y` $\rightarrow$ `1`, `N` $\rightarrow$ `0`) |
| *(Generated)* | `fact_claims.claim_id` | Business key `CLM-` + number, assigned by the loader to new claims only, in source-file order, continuing after the current maximum (`CLM-10001`... on an empty table) |
| *(Generated)* | `stg_insurance_claims.source_row_number` | 1-based position of the row in the source file |
| `policy_number` | all `core` tables except `dim_date` | Natural key; carried unchanged into every dimension and the fact table |
