# Power BI Dashboard Specification

Connection: PostgreSQL, server `localhost:5433`, database `insurance_dw`, user `postgres`
(or import the CSVs from `data/processed/`).

Import: `core.fact_claims`, `core.dim_policy`, `core.dim_incident`, `core.dim_customer`,
`core.dim_vehicle`, `core.dim_date`, and the view `marts.fraud_risk_score`.

**Rename the tables in Model view** (Power BI names them like `core fact_claims`) to
`fact_claims`, `dim_policy`, `dim_incident`, `dim_customer`, `dim_vehicle`, `dim_date` and
`fraud_risk_score`, so the DAX below works unchanged. In `fraud_risk_score`, hide the columns
that duplicate dimensions (`incident_state`, `incident_severity`, `total_claim_amount`,
`fraud_reported_flag`) and slice on the dimension or fact columns instead.

The old `analytics.anomaly_flags` / `anomaly_fraud_metrics` views no longer exist. Remove the
old anomaly visuals and any table imported from them, then refresh.
The renamed fact column also means any visual or measure built on `fact_claims[loss_ratio]`
must be repointed to `claims_to_premium_multiple`.

## Model relationships

| From (many) | To (one) | Notes |
| :--- | :--- | :--- |
| fact_claims[policy_key] | dim_policy[policy_key] | single direction |
| fact_claims[customer_key] | dim_customer[customer_key] | single direction |
| fact_claims[incident_key] | dim_incident[incident_key] | single direction |
| fact_claims[vehicle_key] | dim_vehicle[vehicle_key] | single direction |
| fact_claims[incident_date_key] | dim_date[date_key] | active |
| fact_claims[policy_bind_date_key] | dim_date[date_key] | inactive (use USERELATIONSHIP if needed) |
| fraud_risk_score[claim_key] | fact_claims[claim_key] | one-to-one, cross-filter both directions so the risk band slicer filters every page |

## DAX measures

Create a `_Measures` table and add:

```
Claim Count = COUNTROWS(fact_claims)

Total Claims Amount = SUM(fact_claims[total_claim_amount])

Total Annual Premium = SUM(fact_claims[policy_annual_premium])

Claims-to-Premium Multiple =
    DIVIDE([Total Claims Amount], [Total Annual Premium])
-- Format: 0.0"x"  (relative risk indicator, NOT an actuarial loss ratio)

Avg Claim Severity = DIVIDE([Total Claims Amount], [Claim Count])

Reported Fraud Claims = CALCULATE([Claim Count], fact_claims[fraud_reported_flag] = 1)

Fraud Rate = DIVIDE([Reported Fraud Claims], [Claim Count])

Fraud Dollar Exposure =
    CALCULATE([Total Claims Amount], fact_claims[fraud_reported_flag] = 1)

Fraud Lift =
    DIVIDE([Fraud Rate], CALCULATE([Fraud Rate], ALL(fraud_risk_score[risk_band])))

High Risk Claims =
    CALCULATE([Claim Count], fraud_risk_score[risk_band] = "High")

High Risk Precision =
    DIVIDE(
        CALCULATE([Reported Fraud Claims], fraud_risk_score[risk_band] = "High"),
        [High Risk Claims]
    )

High Risk Recall =
    DIVIDE(
        CALCULATE([Reported Fraud Claims], fraud_risk_score[risk_band] = "High"),
        CALCULATE([Reported Fraud Claims], ALL(fraud_risk_score[risk_band]))
    )
```

Calculated column on `dim_policy` so tenure groups sort logically (then select `tenure_group`,
Column tools, Sort by column, `Tenure Sort`):

```
Tenure Sort =
    SWITCH(
        dim_policy[tenure_group],
        "<2 Years", 1,
        "2-5 Years", 2,
        "5-10 Years", 3,
        "10-20 Years", 4,
        "20+ Years", 5,
        99
    )
```

Number formats: counts `#,0`; currency `$#,0`; rates `0.0%`; multiple `0.0"x"`.

Expected values to check your cards against (from the warehouse): 1,000 claims, $52,761,940 total
claims, $1,256,406.15 annual premium, 41.99x multiple, 247 reported fraud claims (24.70%),
$14,894,620 fraud dollar exposure, High band 276 claims (60.51% fraud rate, precision 60.51%,
recall 67.61%).

## Slicers (same position on every page; sync them with View, Sync slicers)

`incident_state`, `incident_severity`, `risk_band`, `tenure_group` (sorted by `Tenure Sort`).

Do **not** use a Year slicer or a monthly trend: incident dates span only 2015-01-01 to
2015-03-01 (60 days, a single year), so a Year slicer has one value and a monthly line has
two or three points. Use a daily trend instead.

## Page 1: Executive Overview

- Card row (same size, one row): Claim Count (format `#,0`, shows 1,000 not 1K), Total Claims Amount,
  Avg Claim Severity, Claims-to-Premium Multiple, Reported Fraud Claims, Fraud Rate.
- Fraud Dollar Exposure card.
- Clustered column: Fraud Rate by `incident_severity`. Title: "Fraud Rate by Incident Severity".
- Clustered column: Total Claims Amount by `incident_severity`.
- Bar chart: Fraud Rate by `auto_make` (sorted descending).
- Small text note under the multiple card: "Total claims / annual premium. Not an actuarial loss ratio."

## Page 2: Fraud Risk Scoring

- Column chart: Claim Count by `risk_band` (order High, Medium, Low).
- Column chart: Fraud Rate by `risk_band`.
- Table: `risk_band`, Claim Count, Reported Fraud Claims, Fraud Rate, Fraud Lift.
- Cards: High Risk Claims, High Risk Precision, High Risk Recall.
- Table of High band claims: `claim_id`, `policy_number`, `risk_score`, `total_claim_amount`,
  `incident_severity`, `fraud_reported_flag`.
- Column chart: Fraud Rate by `tenure_group`, axis sorted by `Tenure Sort`
  (reads <2, 2-5, 5-10, 10-20, 20+ Years).
- Text note: "The High band is the Major Damage claims; the other rules only order the Medium and Low bands."

## Page 3: Trends and Geography

- Line chart: Total Claims Amount by `dim_date[full_date]` (daily, 60 days).
- Column chart: Claim Count by `dim_date[day_name]`.
- Bar chart: Claim Count and Fraud Rate by `incident_state`.
- Matrix: `incident_state` by `incident_severity`, value Claim Count.
- Optional map: Claim Count by `incident_state`.

## Layout and polish checklist

- Same card size and alignment on every page; consistent colors (one for fraud, one neutral).
- Every visual has a clear title with no duplicated words.
- No empty areas; resize visuals to fill the canvas.
- Sync the slicers across pages.

## Optional: row-level security by incident_state

1. Modeling, Manage roles, Create. Name it `OH_Only`.
2. Select table `dim_incident` and set the DAX filter:
   ```
   [incident_state] = "OH"
   ```
3. Save. Modeling, View as, tick `OH_Only`, OK. Every visual should now show only Ohio claims
   (23 claims).
4. Repeat per state, or build a user-to-state mapping table for dynamic RLS later.

RLS is enforced for viewers in the Power BI Service. In Desktop, "View as" is for testing only.
