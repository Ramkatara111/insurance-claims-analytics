# Interview Notes

Honest answers to the questions this project is most likely to raise. Numbers come from the
populated warehouse.

## Why separate customer and policy dimensions when the data is 1:1?
The source data has exactly one claim per policy, so `dim_customer` and `dim_policy` both map 1:1 to the fact table and could be merged. I kept them separate because they describe different business entities (who is insured vs. what is covered), and the model then extends naturally when a customer holds several policies or a policy has several claims. With this dataset the separation adds no analytical power, and I would say so rather than present it as a necessity.

## Why a rule-based score instead of machine learning?
The dataset is small (1,000 rows) and looks like a benchmark sample, so a model would mostly learn its artificial patterns. A transparent score is easy to explain to claims and audit teams, because every point has a stated reason. The project targets analytics skills (SQL, modelling, KPI design), not model building. The score is evaluated in-sample, so the results are descriptive, not a performance claim. I also excluded hobbies, which correlate strongly with the fraud flag here but are non-causal lifestyle variables that should not drive a claims decision.

## What does the risk score actually tell you?
The High band (276 claims, 60.51% reported fraud, precision 60.51%, recall 67.61%, lift 2.45) is exactly the Major Damage claims. The score is mostly a Major Damage detector; the other three rules only order the lower bands (Medium 14.84% vs Low 9.78%). I tested two more rules and dropped them because the data did not support them: no police report (weak gap) and no authorities contacted (6.6% fraud rate, lower than average, the opposite of what I expected).

## Why is the claims-to-premium multiple not a loss ratio?
A loss ratio is incurred losses divided by earned premium over the same period, usually around 60-70% for auto. This dataset has one year of annual premium per policy against total claim amounts, so total claims divided by premium is about 42x. That number is only a relative indicator for comparing segments, which is why I named it a multiple and documented the caveat.

## Why did the statistical outlier checks flag almost nothing?
They flag 1 of 1,000 claims, and it is not reported as fraud. Claim amounts are not extreme relative to the portfolio, and fraud here is tied to categorical patterns (above all incident severity), not unusual amounts. I kept the checks as an outlier check only. A repeat-claimant rule cannot work with one claim per policy, so I removed it.

## What would I change with multi-claim data?
- Add a claim-level grain with a `claim_id` distinct from the policy, so repeat-claimant and claim-frequency analysis become possible.
- Keep customer, policy and incident as separate dimensions, and move policy attributes from SCD Type 1 (overwrite) to Type 2 (history) if changes over time matter for reporting.
- Compute a real loss ratio from earned premium and incurred losses over matching periods.
- Add scheduling/orchestration, protection against concurrent runs and a cloud warehouse once volume justifies it (the load itself is already incremental, see below).
- Validate the score on a hold-out period instead of in-sample.

## What are the limits of the findings?
1,000 rows over only 60 days (2015-01-01 to 2015-03-01), a 24.7% fraud rate that is unrealistically high, a fraud flag that is a label and not a verified investigation outcome, and segment percentages (such as a single vehicle make or the 41 policies under 2 years' tenure) that rest on small counts.

## How does the incremental load work?
The natural key is `policy_number`. A run first reads and cleans the CSV in memory and refuses to continue if `policy_number` is duplicated, so a bad file fails before any warehouse data changes. Then one transaction does everything: it truncates and reloads the staging table, adds any missing dates to `dim_date`, and for each dimension and the fact table runs an `UPDATE` (only rows whose values differ, using `IS DISTINCT FROM`) followed by an `INSERT` of rows whose `policy_number` is not there yet, in source-file order. Fact rows get their dimension keys by joining the dimensions on `policy_number`, and new claims get `claim_id` values continuing after the current maximum. The same transaction marks the run `SUCCESS` in `audit.pipeline_run_log`, with the inserted, updated and unchanged counts, so a `SUCCESS` row exists only if the load itself committed. Views, the data quality suite and the reconciliation run after the commit, outside that transaction, so the pipeline as a whole is not atomic: if one of them fails, the load stays committed and the run is marked `FAILED` with `load_committed = true` in its details.

The result is idempotent: running the same file twice reports 0 inserted, 0 updated, 1000 unchanged and leaves every `claim_key` and `claim_id` as it was. I check this with seven scenarios in `tests/test_incremental_load.py` (fresh load, re-run, appended rows, one changed row, a row removed from the source, duplicate key, `--full-refresh`), each against a throwaway PostgreSQL database. `--full-refresh` drops and rebuilds the staging and core tables in the same transaction as the load, and never touches the audit schema.

## Why UPDATE then INSERT instead of INSERT ... ON CONFLICT?
`ON CONFLICT DO UPDATE` would be shorter, but my surrogate keys are `SERIAL` columns. PostgreSQL evaluates the column default (`nextval`) before it discovers the conflict, so every existing row that goes through the upsert consumes a sequence value. Re-running the same 1,000-row file would burn 1,000 values per table per run and leave growing gaps in `customer_key`, `policy_key`, `incident_key`, `vehicle_key` and `claim_key`. Gaps are harmless to joins, but I wanted the keys to stay dense and to be able to assert in a test that new claims get exactly the next keys. Doing the `UPDATE` first and then inserting only the rows that do not exist yet never calls `nextval` for an existing row. The price is two statements per table and a comparison of every column to detect changes. `dim_date` has no sequence, so it uses `ON CONFLICT DO NOTHING`.

## What are the limitations of the incremental load?
* **SCD Type 1 only.** A changed attribute overwrites the old value and sets `updated_at`. There is no history, so I cannot report what a customer or policy looked like before the change. Type 2 would need effective dates, a current flag and a new surrogate key per version.
* **No delete handling.** A claim that disappears from the source stays in the warehouse. The run counts it as `not_in_source`, logs a warning and records the number, but never removes it. I chose that because a missing row in a file could be a mistake and a silent delete is harder to undo.
* **The natural key assumes one claim per policy.** `policy_number` identifies the claim, the customer row, the policy row, the incident and the vehicle. A source with several claims per policy is rejected by the duplicate check; supporting it would need a real claim identifier and a redesigned grain.
* **`claim_id` depends on load order only for the first load.** On an empty table the claims get `CLM-10001` onwards in file order. Later, new claims get the next numbers in the order they appear in the file that introduces them. If the first load had used a differently ordered file, the ids would differ, and that is why a `--full-refresh` can reassign ids when the file order has changed.
* **The source is a static CSV.** There is no live feed, so I demonstrate incremental behaviour with appended or edited copies of the file (that is what the tests do). It shows the mechanics, not behaviour under real arrival patterns, late data or volume.
* **No handling of concurrent runs.** Nothing locks the load, so two runs started at the same time could interfere with each other. A scheduler or an advisory lock would be needed before running this unattended.
* **Existing databases need one `--full-refresh`** after the schema change, because the new natural-key columns cannot be back-filled on rows loaded by the old schema.
