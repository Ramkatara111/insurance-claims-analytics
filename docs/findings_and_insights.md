# Insurance Claims Portfolio: Findings & Insights

> [!NOTE]
> All metrics in this report were computed by running SQL against the populated PostgreSQL warehouse (`core.fact_claims` and the dimensional views). The dataset is a 1,000-row public benchmark sample; read the caveats in sections 8 and 9 before drawing conclusions.

---

## 1. Executive Summary & Key Performance Indicators

| KPI Metric | Value | Business Interpretation |
| :--- | :--- | :--- |
| **Total Claims** | **1,000** | Full population of claims filed Jan 1 – Mar 1, 2015 |
| **Unique Policyholders** | **1,000** | 1:1 claimant-to-policy ratio in analyzed snapshot |
| **Total Claim Amount** | **$52,761,940.00** | Total reported claim amount across the 1,000 claims |
| **Average Claim Severity** | **$52,761.94** | Mean financial loss per incident |
| **Total Annual Premium** | **$1,256,406.15** | One year of premium summed across the 1,000 claimant policies |
| **Reported Fraudulent Claims** | **247** | `fraud_reported = 'Y'`, a flag in the source data and not a verified outcome |
| **Portfolio Fraud Rate** | **24.70%** | About 1 in 4 claims carries the fraud flag (unusually high versus real-world rates) |
| **Fraud Financial Exposure** | **$14,894,620.00** | **28.23%** of all claim dollars sit on claims carrying the fraud flag |
| **Claims-to-Premium Multiple** | **41.99x** | Total claims / one year of annual premium. A relative indicator only, **not** an actuarial loss ratio (real loss ratios need earned premium and incurred losses over the same period) |

---

## 2. Incurred Loss by Claim Sub-Component

The $52.76M total claim amount splits into three distinct claim cost components:

```
[========================================================================] Total Claims: $52,761,940 (100.0%)
[===================================================] Vehicle Claim: $37,928,950 (71.89%)
[==========] Bodily Injury Claim: $7,433,420 (14.09%)
[==========] Property Damage Claim: $7,399,570 (14.02%)
```

* **Vehicle physical damage is the overwhelming loss driver (71.89%)**, averaging $37,928.95 per vehicle.
* Bodily injury and property damage are almost equally balanced (~14.0% each).

---

## 3. Incident Severity vs. Fraud Propensity

Grouping claims by recorded physical severity reveals a dramatic concentration of fraud in **Major Damage** events:

| Incident Severity | Total Claims | % of Total Volume | Fraud Claims | Fraud Rate (%) | Total Incurred Loss | Average Claim Size |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Major Damage** | **276** | **27.6%** | **167** | **60.51%** | **$17,682,540** | **$64,067** |
| Total Loss | 280 | 28.0% | 36 | 12.86% | $17,382,740 | $62,081 |
| Minor Damage | 354 | 35.4% | 38 | 10.73% | $17,219,510 | $48,643 |
| Trivial Damage | 90 | 9.0% | 6 | 6.67% | $477,150 | $5,302 |

### Key Insight:
* **Over 67.6% of all fraudulent claims (167 out of 247)** and **$17.68M in losses** are concentrated in the `Major Damage` category.
* While `Total Loss` claims have comparable severity ($62,081 vs $64,067), their fraud rate is only **12.86%**, compared to **60.51% for Major Damage**.
* **Recommendation:** Claims flagged as `Major Damage` should automatically trigger Special Investigation Unit (SIU) triage prior to payout disbursement.

---

## 4. Collision Dynamics & Law Enforcement Reporting

| Incident Type | Collision Type | Claims Count | Fraud Count | Fraud Rate (%) | Avg Total Claim | Avg Vehicle Claim |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| Multi-vehicle Collision | Rear Collision | 152 | 49 | **32.24%** | $60,417 | $43,164 |
| Multi-vehicle Collision | Side Collision | 152 | 37 | 24.34% | $61,328 | $44,350 |
| Single Vehicle Collision | Rear Collision | 140 | 42 | **30.00%** | $63,103 | $45,385 |
| Single Vehicle Collision | Front Collision | 139 | 42 | **30.22%** | $65,487 | $47,518 |
| Single Vehicle Collision | Side Collision | 124 | 33 | 26.61% | $64,794 | $46,553 |
| Multi-vehicle Collision | Front Collision | 115 | 28 | 24.35% | $63,657 | $45,215 |
| Vehicle Theft | Not Applicable | 94 | 8 | 8.51% | $5,517 | $3,999 |
| Parked Car | Not Applicable | 84 | 8 | 9.52% | $5,308 | $3,807 |

* Collisions involving moving vehicles average **$60,000+** in claims with fraud rates between **24% and 32%**.
* Non-collision claims (`Vehicle Theft` and `Parked Car`) exhibit significantly lower payouts (~$5,400) and low fraud rates (~9%).

---

## 5. Customer Tenure Cohort Risk

Analyzing policyholder relationship duration reveals a classic underwriting risk curve:

| Customer Tenure Group | Customer Count | Avg Age | Avg Claim Amount | Fraud Claims | Fraud Rate (%) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **< 2 Years** | **41** | **29.1** | **$55,559** | **14** | **34.15%** |
| 2–5 Years | 60 | 32.7 | $49,652 | 11 | 18.33% |
| 5–10 Years | 158 | 28.6 | $50,968 | 41 | 25.95% |
| 10–20 Years | 362 | 36.0 | $52,060 | 86 | 23.76% |
| 20+ Years | 379 | 48.1 | $54,370 | 95 | 25.07% |

* **First-year and new policies (<2 years) carry the highest fraud rate (34.15%)**, nearly double that of policyholders in the 2–5 year tenure bracket (18.33%).
* **Underwriting Takeaway:** Carriers should implement stricter pre-bind vehicle inspections or verification periods for accounts in their first 24 months.

---

## 6. Vehicle Make & Manufacturer Loss Profiles

| Auto Make | Total Claims | Total Incurred Loss | Avg Claim Amount | Avg Vehicle Damage | Fraud Claims | Fraud Rate (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Mercedes** | 65 | $3,404,190 | $52,372 | $37,596 | **22** | **33.85%** |
| **Ford** | 72 | $4,073,050 | $56,570 | $41,011 | **22** | **30.56%** |
| **Audi** | 69 | $3,752,480 | $54,384 | $39,081 | **21** | **30.43%** |
| Volkswagen | 68 | $3,458,130 | $50,855 | $35,970 | 19 | 27.94% |
| BMW | 72 | $4,025,180 | $55,905 | $39,496 | 20 | 27.78% |
| Chevrolet | 76 | $4,008,740 | $52,747 | $38,516 | 21 | 27.63% |
| Honda | 55 | $2,844,320 | $51,715 | $37,484 | 14 | 25.45% |
| Dodge | 80 | $4,475,550 | $55,944 | $40,016 | 20 | 25.00% |
| Subaru | 80 | $4,298,410 | $53,730 | $38,905 | 19 | 23.75% |
| Saab | 80 | $4,115,630 | $51,445 | $37,127 | 18 | 22.50% |
| Acura | 68 | $3,571,280 | $52,519 | $38,041 | 13 | 19.12% |
| Toyota | 70 | $3,256,660 | $46,524 | $33,450 | 13 | 18.57% |
| Nissan | 78 | $4,020,530 | $51,545 | $37,527 | 14 | 17.95% |
| **Jeep** | 67 | $3,457,790 | $51,609 | $36,092 | **11** | **16.42%** |

* Luxury European brands (Mercedes at 33.85%, Audi at 30.43%) and domestic trucks (Ford at 30.56%) show significantly elevated fraud rates compared to Japanese mass-market brands (Jeep at 16.42%, Nissan at 17.95%, Toyota at 18.57%).

---

## 7. The Kaggle Benchmark Hobby Anomaly

An empirical audit of the `insured_hobbies` field reveals extreme statistical anomalies:

| Insured Hobby | Policy Count | Fraud Claims | Fraud Rate (%) | Avg Claim Size |
| :--- | :---: | :---: | :---: | :---: |
| **chess** | **46** | **38** | **82.61%** | **$54,423** |
| **cross-fit** | **35** | **26** | **74.29%** | **$60,713** |
| yachting | 53 | 16 | 30.19% | $53,565 |
| board-games | 48 | 14 | 29.17% | $55,490 |
| polo | 47 | 13 | 27.66% | $51,329 |
| reading | 64 | 17 | 26.56% | $48,716 |
| ... | ... | ... | ... | ... |
| golfing | 55 | 6 | 10.91% | $47,559 |
| kayaking | 54 | 5 | 9.26% | $51,147 |
| **camping** | **55** | **5** | **9.09%** | **$54,878** |

### Professional Engineering Commentary:
Why this matters for analysis:
* While synthetic benchmark datasets often contain strong artificial correlations (e.g., `chess` at 82.6% fraud), in a real carrier environment, relying on leisure hobbies for claims decisions would be unsound and would raise fairness and regulatory concerns. For this reason hobbies are excluded from the fraud risk score in section 8.
* An analyst's role is to identify such artifacts, document them, and keep rules driven by sound indicators (such as prior loss history and damage mismatch, which this dataset does not contain).

---

## 8. Rule-Based Fraud Risk Score

Built in `sql/05_fraud_risk_score.sql` as the view `marts.fraud_risk_score` (pure SQL, no machine learning). Points: Major Damage +3, collision incident (single or multi-vehicle) +1, policy tenure under 2 years +1, claims-to-premium multiple above the 75th percentile +1 (maximum 6). Bands: High 4+, Medium 2-3, Low 0-1.

### Rule diagnostics (reported fraud rate when the rule fires)

| Rule | Claims flagged | Fraud rate when flagged | Fraud rate when not flagged |
| :--- | ---: | ---: | ---: |
| Major Damage | 276 | 60.51% | 11.05% |
| Collision incident | 822 | 28.10% | 8.99% |
| Tenure under 2 years | 41 | 34.15% | 24.30% |
| Claims-to-premium above 75th percentile | 250 | 28.40% | 23.47% |

Rules I tested and **dropped** because the data did not support them: `police_report_available` (fraud rate 25.5% when `NO`/`UNKNOWN` vs 22.9% when `YES`, a weak gap) and `authorities_contacted = 'None'` (6.6% fraud rate, which is *lower* than the 24.7% average, the opposite of the intuitive assumption). Hobbies were excluded on principle.

### Results by risk band

| Risk band | Claims | Reported fraud | Fraud rate | Lift vs overall (24.7%) |
| :--- | ---: | ---: | ---: | ---: |
| High | 276 | 167 | 60.51% | 2.45 |
| Medium | 182 | 27 | 14.84% | 0.60 |
| Low | 542 | 53 | 9.78% | 0.40 |

High band: **precision 60.51%, recall 67.61%** (167 of 247 reported fraud claims).

### Honest reading of these results
* The High band is exactly the 276 Major Damage claims, because Major Damage (3 points) combined with the collision rule always reaches 4. The score is therefore mostly a Major Damage detector; the other three rules only order the Medium and Low bands (14.84% vs 9.78%).
* The rules and thresholds were chosen from patterns in these same 1,000 rows, so the results are in-sample and descriptive, not a measure of performance on new claims.
* The dataset is a benchmark sample with an unrealistically high fraud rate, so absolute rates should not be generalised.

---

## 9. Statistical Outlier Checks (Not a Fraud Indicator)

`sql/04_outlier_checks.sql` flags unusually large claim amounts with two rules: above mean + 3 standard deviations, and above Q3 + 1.5 x IQR.

* Together they flag **1 of 1,000 claims**, and that claim is not reported as fraud: true positives 0, precision 0.00%, recall 0.00% (`analytics.outlier_fraud_metrics`).
* Conclusion: in this dataset fraud is associated with categorical patterns (above all incident severity), not with unusually large amounts, so amount-outlier rules do not detect it.
* A repeat-claimant rule was removed: every policy has exactly one claim, so it could never fire.
