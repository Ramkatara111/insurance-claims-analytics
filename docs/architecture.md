# Architecture & Technical Design

## 1. System Architecture Diagram

```
+---------------------------------------------------------------------------------------+
|                                    DOCKER NETWORK                                     |
|                                                                                       |
|   +--------------------------+                         +--------------------------+   |
|   |                          |                         |                          |   |
|   |   pipeline Container     |  SQLAlchemy Bulk Insert |    PostgreSQL 16 DW      |   |
|   |   (Python 3.11-slim)     +------------------------>+    Container             |   |
|   |                          |    (Port 5432 Internal) |    (postgres:16-alpine)  |   |
|   +------------^-------------+                         +------------+-------------+   |
|                |                                                    |                 |
+----------------|----------------------------------------------------|-----------------+
                 |                                                    |
         Host Mount Volume                                    Host Exposed Port
                 |                                                    | (5433:5432)
+----------------+-------------+                         +------------v-------------+
| Local File System            |                         | External BI & Analysts   |
|  - data/raw/insurance_claims |                         |  - Power BI Desktop      |
|  - data/processed/           |                         |  - DBeaver / pgAdmin     |
|  - sql/*.sql                 |                         |  - Custom SQL Client     |
+------------------------------+                         +--------------------------+
```

---

## 2. Kimball Dimensional Star Schema

```
                             +------------------------+
                             |     core.dim_date      |
                             +------------------------+
                             | PK  date_key           |
                             |     full_date          |
                             |     year               |
                             |     quarter            |
                             |     month              |
                             |     month_name         |
                             |     day                |
                             |     day_of_week        |
                             |     day_name           |
                             |     is_weekend         |
                             +-----------+------------+
                                         |
                                         | (incident_date_key)
                                         |
+------------------------+               |               +------------------------+
|   core.dim_customer    |               |               |    core.dim_policy     |
+------------------------+               |               +------------------------+
| PK  customer_key       |               |               | PK  policy_key         |
|     policy_number      |               |               |     policy_number      |
|     age                |               |               |     policy_bind_date   |
|     age_group          |               |               |     policy_state       |
|     insured_sex        |               |               |     policy_csl         |
|     insured_education  |               |               |     policy_deductible  |
|     insured_occupation |               |               |     policy_annual_prem |
|     insured_hobbies    |               |               |     months_as_customer |
|     insured_rel        |               |               |     tenure_years       |
|     insured_zip        |               |               |     tenure_group       |
+-----------+------------+               |               +-----------+------------+
            |                            |                           |
            | (customer_key)             |                           | (policy_key)
            |                            |                           |
            +------------------->+-------v--------+<-----------------+
                                 |core.fact_claims|
                                 +----------------+
                                 | PK  claim_key                     |
                                 |     claim_id                      |
                                 |     policy_number                 |
                                 | FK  incident_date_key             |
                                 | FK  policy_bind_date_key          |
                                 | FK  customer_key                  |
                                 | FK  policy_key                    |
                                 | FK  incident_key                  |
                                 | FK  vehicle_key                   |
                                 |     total_claim_amount            |
                                 |     injury_claim                  |
                                 |     property_claim                |
                                 |     vehicle_claim                 |
                                 |     policy_annual_premium         |
                                 |     policy_deductible             |
                                 |     umbrella_limit                |
                                 |     capital_gains                 |
                                 |     capital_loss                  |
                                 |     number_of_vehicles_involved   |
                                 |     bodily_injuries               |
                                 |     witnesses                     |
                                 |     fraud_reported_flag           |
                                 |     fraud_reported_desc           |
                                 |     claims_to_premium_multiple    |
                                 +-------^--------^------------------+
                                         |        |
                          (incident_key) |        | (vehicle_key)
                                         |        |
             +---------------------------+        +---------------------------+
             |                                                                |
+------------+-----------+                                       +------------+-----------+
|   core.dim_incident    |                                       |    core.dim_vehicle    |
+------------------------+                                       +------------------------+
| PK  incident_key       |                                       | PK  vehicle_key        |
|     policy_number (NK) |                                       |     policy_number (NK) |
|     incident_type      |                                       |     auto_make          |
|     collision_type     |                                       |     auto_model         |
|     incident_severity  |                                       |     auto_year          |
|     authorities_cont   |                                       |     vehicle_age_at_inc |
|     incident_state     |                                       +------------------------+
|     incident_city      |
|     incident_location  |
|     incident_hour      |
|     incident_window    |
|     property_damage    |
|     police_report_avlb |
+------------------------+
```

`policy_number` is the **natural key (NK)** of the whole model: it is present, `NOT NULL` and unique in `dim_customer`, `dim_policy`, `dim_incident`, `dim_vehicle` and `fact_claims` (one claim per policy in this dataset). The loader matches source rows to warehouse rows on it. The `*_key` columns are `SERIAL` surrogate keys assigned by PostgreSQL. Every `core` table also carries `created_at` and `updated_at` (not drawn). See the [data dictionary](data_dictionary.md).

---

## 3. Data Processing Lifecycle

```
[Raw CSV File]  (default data/raw/insurance_claims.csv, or --source PATH)
      │
      ▼ (extract.py)
[Pandas Ingestion & Integrity Validation (1,000 rows x 40 cols)]
      │
      ▼ (transform.py)
[Data Hygiene & Transformation Pipeline]
      ├── 1. Drop delimiter artifact column (_c39)
      ├── 2. Standardize column naming convention (snake_case)
      ├── 3. Correct spelling errors (Suburu -> Subaru, Accura -> Acura, Forrestor -> Forester)
      ├── 4. Clamp erroneous values (umbrella_limit -1M -> 0)
      ├── 5. Replace sentinels ('?' -> UNKNOWN / Not Applicable)
      ├── 6. Preserve string 'None' for uncontacted authorities
      ├── 7. Derive business cohorts (Age brackets, Tenure groups, Time windows)
      ├── 8. Add source_row_number (1-based position in the file); no keys are generated in Python
      ├── 9. Split into Kimball Star Schema DataFrames & CSV Exports
      └── 10. validate_unique_policy_numbers(): duplicated policy_number -> fail before the load
      │
      ▼ (pipeline.py writes a RUNNING row to audit.pipeline_run_log after the idempotent DDL, before extraction)
      │
      ▼ (load.py: ONE transaction)
[PostgreSQL Incremental Load]
      ├── (--full-refresh only) 00_full_refresh_reset.sql: drop staging + core tables, then 01_init_schema.sql
      ├── staging.stg_insurance_claims: TRUNCATE + reload (landing table)
      ├── core.dim_date: INSERT ... ON CONFLICT (date_key) DO NOTHING
      ├── core.dim_customer / dim_policy / dim_incident / dim_vehicle:
      │       UPDATE changed rows (SCD Type 1), then INSERT new rows (in source order)
      ├── core.fact_claims: UPDATE changed rows, then INSERT new rows
      │       (dimension keys joined on policy_number; claim_id only for new rows)
      ├── count rows present in the warehouse but absent from the source (not_in_source)
      └── mark the run SUCCESS in audit.pipeline_run_log (same transaction)
      │
      ▼ (after COMMIT)
      ├── Analytical Mart Views: 02_create_views.sql
      ├── Outlier-check views (not a fraud indicator): 04_outlier_checks.sql
      ├── Rule-based fraud risk score view: 05_fraud_risk_score.sql
      ├── Data quality suite (14 checks) -> audit.data_quality_audit (same run_id)
      └── Reconciliation of the fact and dimension tables against the source
      │
      ▼ (tests/ & sql/03_analytical_queries.sql)
[Automated Reconciliation & Business Intelligence Delivery]
```

For the unmodified source file the first run reports `inserted=1000` for the fact table and a second run reports `unchanged=1000`. `core.dim_date` holds 9,184 rows after the first load (one per calendar day from the earliest policy bind date to the latest incident date).

---

## 4. Key Architectural Decisions

1. **Dual Port Allocation (5433:5432):**
   * Exposed port `5433` on the host machine avoids collisions with pre-existing PostgreSQL installations on developer workstations, while retaining standard internal port `5432` within the Docker virtual bridge network.
2. **Kimball Star Schema vs. Flat OLAP:**
   * While 1,000 rows can easily fit in memory, organizing into dimensions and a fact table follows Kimball dimensional modelling and gives Power BI clean filter propagation. Because the data has one claim per policy, the customer and policy dimensions are 1:1 with the fact table; the separation is a modelling choice that would pay off with multi-claim data.
3. **Natural Key and Surrogate Keys:**
   * The raw dataset had no native `claim_id` or `customer_id`. `policy_number` is the natural key of the model. Surrogate integer keys are `SERIAL` columns assigned by PostgreSQL, so Python never invents a key. `claim_id` (`CLM-10001`, `CLM-10002`, ...) is a business-friendly label filled for new claims only, continuing after the current maximum; an existing claim's `claim_id` is never rewritten.
4. **Preservation of Raw vs. Clean Layers:**
   * The staging schema (`staging.stg_insurance_claims`) stores cleaned records in flat structure for audit tracing and ELT comparisons, while `core` provides the single source of truth.
5. **Idempotent Incremental Load (UPDATE then INSERT):**
   * Each dimension and the fact table is loaded with two statements: an `UPDATE ... FROM staging` that only touches rows whose values differ (`IS DISTINCT FROM`) and sets `updated_at`, then an `INSERT ... WHERE NOT EXISTS` for new `policy_number` values in source-file order. Re-running the same file therefore changes nothing.
   * `INSERT ... ON CONFLICT DO UPDATE` is deliberately not used for `SERIAL` keys: PostgreSQL takes a sequence value before it checks for a conflict, so every existing row would burn a value and leave gaps in the surrogate keys. `core.dim_date` has no sequence and uses `ON CONFLICT DO NOTHING`.
6. **SCD Type 1 for Dimensions:**
   * When an attribute changes in the source, the dimension row is overwritten in place and `updated_at` is set. Surrogate keys stay the same, so existing fact rows keep pointing at the same dimension row. No history is kept; a Type 2 design (effective dates, current flag, new surrogate key per version) would be needed to report on past attribute values.
7. **Transaction Boundaries:**
   * Extraction, cleaning and the duplicate-key check happen in memory first. The warehouse load (staging, dimensions, fact, and the `SUCCESS` update of the run log) is one transaction: if anything in it fails, staging, `core` and the run log roll back to their previous state, and the `FAILED` row is written afterwards in a **separate** transaction (`details.load_committed = false`). With `--full-refresh` the table drop and rebuild are inside the same transaction.
   * Views, the data quality suite and the reconciliation run **after** the commit and are not part of that transaction, so the pipeline as a whole is not atomic. If one of them raises, the committed load is kept, the run's `SUCCESS` is overwritten with `FAILED`, `details.load_committed` is `true` and the error message starts with "Post-load step failed (the load itself was committed)." Data quality checks that report `FAILED` in `audit.data_quality_audit` do not raise and do not change the run status. If the process is killed between the commit and the end of these steps, the run stays `SUCCESS` without them having run; re-running repeats them.
8. **Audit Schema (`audit`):**
   * `audit.pipeline_run_log` has one row per run: `run_id`, `started_at`, `finished_at`, `mode` (`incremental` or `full_refresh`), `source_file`, `rows_extracted`, `fact_inserted`, `fact_updated`, `fact_unchanged`, `not_in_source`, `details` (JSONB with the counts of every table), `status` (`RUNNING`, `SUCCESS`, `FAILED`) and `error_message`. The row is inserted in its own transaction before extraction, so a source that fails validation still leaves a `FAILED` row. `audit.data_quality_audit` stores the data quality results under the same `run_id`. Neither table is dropped or truncated by `--full-refresh`.
9. **Missing Source Rows Are Kept:**
   * Claims that exist in the warehouse but not in the current file are not deleted. They are counted as `not_in_source`, logged as a warning and stored in the run log. The staging-to-fact reconciliation compares only the fact rows whose `policy_number` is in staging.
10. **Known Limits of the Design:**
   * No delete handling, no history of attribute changes, no protection against two runs at the same time, and a static CSV as the only source. The model assumes one claim per policy. These limits are discussed in [interview_notes.md](interview_notes.md).
