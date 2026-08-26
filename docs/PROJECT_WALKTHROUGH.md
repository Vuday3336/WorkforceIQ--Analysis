# WorkforceIQ — How It Was Built

A pin-to-pin account of the project: every layer, every tool, why each choice was
made, what went wrong, and how to reproduce it. Written to be the document you
revise from before an interview.

**Live dashboard:** https://vuday3336.github.io/WorkforceIQ--Analysis/
**Repository:** https://github.com/Vuday3336/WorkforceIQ--Analysis

---

## 0. What exists, in one screen

```
data/raw/ibm_hr_attrition.csv          1,470 real employees, one dateless snapshot
        │
        │  generators/build_dataset.py          normalise + synthesise the time dimension
        ▼
data/processed/*.csv                   6 tables, 23,645 rows, deterministic
        │
        ├─► sql/schema.sql  +  seed_data.sql  ─────► PostgreSQL (Supabase, live)
        │        7 tables, FKs, checks, indexes            + rls_policies.sql
        │
        ├─► sql/views/*.sql  ──────────────────────► 8 analytical views
        │        window functions, CTEs,                   (run on BOTH Postgres
        │        indirect standardisation                   and DuckDB, verified
        │                                                   identical)
        ├─► generators/train_attrition_model.py ───► attrition_risk_scores
        │        LogReg vs RandomForest                    1,233 employees scored
        │        features read FROM the views
        │
        ├─► generators/build_powerbi.py ───────────► powerbi/ (PBIP + TMDL)
        │        14 tables · 22 DAX measures               4 pages · 23 visuals
        │
        └─► generators/build_dashboard.py ─────────► docs/index.html
                 data inlined                              GitHub Pages
```

**Final counts:** 27 commits · 100 files · 3,260 lines of Python · 1,302 lines of
hand-written SQL · 1,566 lines of TMDL · 913 lines of dashboard HTML/CSS/JS.

---

## 1. Tools used, and why each one

| Tool | Used for | Why this and not something else |
|---|---|---|
| **PostgreSQL 17** (Supabase) | The production database | The brief called for a real relational DB. Supabase gives a hosted Postgres on a free tier with a REST layer on top. |
| **DuckDB** | Running the same `.sql` files locally | Speaks the same window-function / CTE / `FILTER` dialect as Postgres but needs no server. This is what made the SQL testable in a loop. |
| **Python 3.13 + pandas** | Data generation, feature assembly, exports | Deterministic transforms; the whole dataset rebuilds byte-identically from a seed. |
| **scikit-learn** | The attrition model | Pipelines keep preprocessing inside cross-validation, which is what stops the classic scaling leak. |
| **matplotlib** | Model charts | Static PNGs that render in GitHub without a JS runtime. |
| **nbformat + nbclient** | Building and *executing* the notebook | The committed `.ipynb` carries real outputs and charts rather than empty cells — it is generated and executed by a script, so it can never drift from the training code. |
| **Power BI Desktop** (PBIP/TMDL) | The BI layer | PBIP is Microsoft's text-based project format: diffable, reviewable, mergeable. A `.pbix` is an opaque binary. |
| **psycopg2** | Loading and verifying Postgres | Runs the shipped `.sql` files against the live database, so the files themselves are what gets tested. |
| **Vanilla HTML/CSS/JS** | The hosted dashboard | No framework, no build step, no CDN. One self-contained file that GitHub Pages serves directly. |
| **GitHub Pages** | Hosting the dashboard | Free, tied to the repo, no separate deploy pipeline. |
| **PowerShell** | Capturing Power BI screenshots | Power BI Desktop has no CLI or automation surface, so a Win32 window capture was the only route. |
| **Git** | 27 scoped commits | Each commit is one logical change with the reasoning in the body. |

---

## 2. Layer 1 — The data

### 2.1 The source

The public **IBM HR Analytics Employee Attrition & Performance** dataset: 1,470
real rows, 35 columns, a 16.12% attrition rate. Fetched directly rather than
hand-downloaded, so the pipeline starts from a reproducible input.

### 2.2 The central problem: the source has no dates

The dataset is **a single snapshot with no dates anywhere**. `YearsAtCompany` is
a whole number; there are no hire dates, no termination dates, no salary history,
no review history. That makes three of the brief's requirements impossible as-is:
SCD Type 2 modelling, time-intelligence DAX, and any trend analysis.

So the time dimension had to be constructed. The rule followed throughout:

> **Every driver variable and the attrition label are used unmodified. Only
> time-shaped fields are synthesised, and every synthesis is documented.**

| Real (untouched) | Synthesised (documented) |
|---|---|
| MonthlyIncome, JobSatisfaction, EnvironmentSatisfaction, WorkLifeBalance, OverTime, PerformanceRating, StockOptionLevel, PercentSalaryHike, DistanceFromHome, Age, Gender, MaritalStatus, Education, JobLevel, YearsAtCompany, **Attrition** | Employee names, hire/termination dates, per-year compensation rows, per-year review rows, the manager hierarchy, the voluntary/involuntary split, the 6-department overlay |

**Consequence, stated in the README and on the dashboard:** findings about
*drivers* are real signal. Finding 1's quarter-over-quarter *slope* is partly by
construction and is labelled a technique demonstration, not an insight.

### 2.3 How each synthesised field was derived

- **Compensation history** — the real current salary is walked *backwards*
  through the real `PercentSalaryHike`: `income(t-1) = income(t) / (1 + hike)`.
  One row per review cycle, up to 7. Result: 7,863 effective-dated rows.
- **Performance reviews** — the most recent review carries the **real** source
  values; earlier years are jittered ±1 deterministically. 7,147 rows.
- **Manager hierarchy** — a three-level tree per department (head → middle
  managers where the department is large enough → line managers → ICs), with
  deliberately uneven spans of 4–20 so view 4 has real variation to test.
- **Dates** — leavers get a termination date inside the FY2024–FY2025 window and
  a hire date back-dated by their real tenure; stayers are back-dated from the
  snapshot date.
- **The six-department overlay** — the source has 3 departments; the scenario
  calls for 6. A documented, reversible `JobRole → department` map.

### 2.4 A deliberate decision: 1,470, not "~2,000"

The brief said ~2,000 employees. Padding with ~530 invented people would have
diluted a real attrition label with synthetic ones and made every rate partly
fictional. The real 1,470 was the better trade, and the deviation is stated
plainly rather than hidden.

### 2.5 Determinism

Fixed seed plus a per-employee SHA-256 hash for every derived field. Re-running
`build_dataset.py` reproduces every table byte-for-byte. Verified by wiping
`data/processed/` and rebuilding — headline 0.1612 and Sales SAR 1.361 both
reproduced exactly.

### 2.6 A leakage bug found and fixed here

The first version jittered hire dates for **active employees only**. That put
every leaver on an exact integer tenure and every stayer just above one — a
**class-dependent gap the model could exploit as free signal**. It showed up as
an implausible **71.4% attrition rate in the under-1-year band**.

Fixed by drawing the within-year offset identically for both classes. The band
now reads 37.8%, and the tenure curve declines smoothly. This is left documented
because it is the whole argument for point-in-time feature stores: synthetic-data
leakage is quiet, and it flatters your metrics.

---

## 3. Layer 2 — The relational schema

`sql/schema.sql` — 7 tables, foreign keys, check constraints, covering indexes.

```
departments ──< employees ──< compensation_history
                   │    └──< performance_reviews
                   │    └──< attrition_events
                   │    └──< attrition_risk_scores
                   └──(self-FK: manager_id)
dim_date ──< attrition_events
```

### Three decisions worth defending

**`employee_id` is a natural key**, carried from the source HRIS extract rather
than a surrogate serial. A refreshed extract then needs no identity remapping
across the model and the scoring job.

**`compensation_history` and `performance_reviews` are effective-dated** (SCD
Type 2 pattern) rather than one mutable row per employee. This is what makes
point-in-time questions — *"what was this person paid when they resigned?"* —
answerable at all. Without it, view 3 would compare a leaver against a market
that moved on without them.

**`attrition_risk_scores` has a composite PK** on
`(employee_id, scored_date, model_name)`. Every scoring run is retained rather
than overwritten, so model drift stays observable instead of being lost.

**`dim_date`** is not in the brief but is required: Power BI time-intelligence
DAX needs a contiguous *marked* date table, and deriving one from fact dates
leaves gaps in any period with no terminations.

The self-referencing `manager_id` FK is **`DEFERRABLE INITIALLY DEFERRED`** — a
self-referencing tree cannot be satisfied by any single row ordering, so the
constraint has to be checked at COMMIT.

---

## 4. Layer 3 — The analytical SQL

Eight views in `sql/views/`. Company baseline: **1,470 employees, 1,233 active,
237 departures, 16.12% crude attrition**.

| # | View | Question | Finding | SQL technique |
|---|---|---|---|---|
| 1 | `vw_attrition_by_department` | Which departments are worst, and is one quarter distorting it? | HR 18.2%, Sales 13.5% rolling-4q vs Finance 1.0% | `CROSS JOIN` spine, aggregate `FILTER`, `ROWS BETWEEN 3 PRECEDING` |
| 2 | `vw_tenure_cohort_attrition` | Do we lose people early or late? | 0–1yr highest *rate* (36.4%) but 1–3yr is **36.3% of all leavers** | `CASE` cohorts, `SUM(SUM(...)) OVER ()` |
| 3 | `vw_compensation_percentile` | Do underpaid-vs-peers people leave more? | Bottom quartile 19.5% vs Q3 13.6% — **non-monotonic**, Q4 rises to 14.6% | `PERCENT_RANK()` / `NTILE(4)` partitioned by role |
| 4 | `vw_manager_span_attrition` | Do bigger teams lose more people? | **No relationship** — flat 14.0 / 16.6 / 16.3% | self-join, `RANK()`, weighted roll-up |
| 5 | `vw_overtime_satisfaction_attrition` | Do overtime and low satisfaction compound? | **36.6%, 2.27× base, 5.3× the clean segment** | two-dimension segmentation + lift via `CROSS JOIN` base CTE |
| 6 | `vw_department_attrition_controlled` | Retention problem or young workforce? | **Engineering SAR 1.01** — its 17.6% is tenure mix | indirect standardisation |
| 7 | `vw_attrition_risk_watchlist` | Who is at risk now? | Joins model output to actionable human context | `ROW_NUMBER()` latest-run pick |
| 8 | `vw_dim_employee` | — | The conformed wide dimension Power BI imports | wide dimensional join |

### 4.1 View 6 in detail — the one to explain in an interview

**The confounder.** Tenure is the strongest single predictor of leaving (36.4% at
0–1 years, 10.4% at 10+). Departments do not have the same tenure mix. A
department that doubled in size is mostly 0–2 year employees — the cohort that
leaves most — so it posts a high crude rate **even if it retains people better
than average at every tenure level**.

Ranking on the crude rate therefore **rewards departments that stopped hiring and
punishes the ones that grew.** That is not imprecise, it is actively misleading —
and it is what the quarterly PDF this project replaces was doing.

**The method — indirect standardisation**, borrowed from epidemiology, where the
identical problem appears as comparing mortality across regions with different
age structures:

1. Compute the company-wide attrition rate **for each tenure cohort** (the
   *standard schedule*).
2. Apply that schedule to each department's **own tenure mix** → *expected leavers*.
3. `SAR = observed / expected`.
4. `tenure_adjusted_rate = SAR × company crude rate` to put it back on a
   familiar scale.

| Department | Head | Observed | Expected | Crude | **SAR** | Verdict |
|---|--:|--:|--:|--:|--:|---|
| HR | 52 | 12 | 8.6 | 23.1% | **1.40** | Worse than tenure predicts |
| Sales | 409 | 90 | 66.1 | 22.0% | **1.36** | Worse than tenure predicts |
| Engineering | 631 | 111 | 109.7 | 17.6% | **1.01** | In line with tenure mix |
| Operations | 145 | 10 | 20.4 | 6.9% | **0.49** | Better |
| Customer Support | 131 | 9 | 18.5 | 6.9% | **0.49** | Better |
| Finance | 102 | 5 | 13.7 | 4.9% | **0.37** | Better |

**The result that changes a decision:** Engineering is 47% of all outflow (111 of
237) and looked like *the* problem. Its SAR is 1.01 — it loses almost exactly
what its tenure mix predicts. That is a hiring-volume consequence, not a
retention failure. **Sales (1.36 across 409 people) is where a retention task
force belongs.**

**What it does not claim:** SAR adjusts for **tenure mix only**. Role mix, pay
position and overtime remain uncontrolled. Saying so is part of the finding.

### 4.2 View 4 is a negative result, reported as one

Span of control shows **no relationship** to attrition. The apparent 20.3% spike
in the smallest band is the small-sample artefact the view was built to expose:
79 employees across 19 managers, where two extra departures move the number by
2.5 points. The view therefore emits `direct_reports` and a
`has_reliable_sample` flag alongside every rate, and the roll-up aggregates
**people** rather than averaging per-manager rates — averaging would weight a
4-person team like a 20-person one.

Reporting this honestly was the point. The hypothesis was reasonable; the data
does not support it.

---

## 5. Layer 4 — The model

`notebooks/attrition_risk_model.ipynb`, generated and executed by
`generators/build_notebook.py` (26 cells, 0 errors, 4 charts).

### 5.1 Features come from the SQL views, not from pandas

`generators/feature_store.py` assembles the modelling frame by **querying the
same views the dashboard reads**. If the definition of "pay percentile within
role" changes, it changes in one place and the model and the report move
together. A model whose feature definitions drift from the BI layer is how you
end up explaining why two numbers on the same screen disagree.

### 5.2 Method

- Stratified 75/25 split → 1,102 train / 368 test (59 leavers in test)
- All preprocessing **inside** the sklearn `Pipeline` (impute → scale →
  one-hot), so it is fit on training folds only — this is what avoids the
  classic scaling leak
- `class_weight="balanced"` on both models
- 5-fold stratified CV on PR-AUC for model selection

### 5.3 Results

| | Logistic Regression | Random Forest |
|---|--:|--:|
| Recall (leavers) | **0.66** | 0.22 |
| Precision (leavers) | 0.35 | 0.57 |
| ROC-AUC | 0.739 | 0.749 |
| **5-fold CV PR-AUC** | **0.507 ± 0.065** | **0.508 ± 0.035** |

Confusion matrix (LogReg): TN 238 · FP 71 · FN 20 · TP 39.

**The two models are statistically tied** — the gap (0.001) is far inside the
fold-to-fold standard deviation. The forest buys no real accuracy, so the
watchlist ships on the **logistic regression**, where a coefficient answers the
only question a manager ever asks: *why this person?*

**Accuracy is excluded on purpose.** At a 16.1% base rate, predicting "nobody
leaves" scores 83.9%. Everything is judged on recall and PR-AUC — and for a
retention watchlist recall is the right bias: a false positive costs one coffee
conversation, a false negative costs a replacement hire.

### 5.4 What drives attrition (permutation importance)

| Feature | Drop in PR-AUC when shuffled |
|---|--:|
| `tenure_years_log` | 0.220 |
| `overtime` | 0.178 |
| `monthly_income` | 0.102 |
| `tenure_years` | 0.092 |
| `job_role` | 0.060 |
| `pct_vs_role_average` | 0.044 |

**Tenure, overtime, compensation — in that order.** This agrees with the SQL
findings, which is the reassuring part: two independent methods on the same data
reaching the same conclusion.

A `tenure_years_log` term was added because attrition falls off a cliff in the
first three years and then flattens — a raw linear term badly underfits that
curve, and it was the main reason the tree model initially looked better.

### 5.5 Scoring, and a capacity decision made in the open

All 1,233 active employees are scored. Tiers are **quantiles of the active
population** (top decile → High = 124 people; next 20% → Medium = 246), not a
0.5 probability cutoff. Under balanced class weights, 0.5 flags several hundred
people, which no HR team can action. That is a capacity decision, not a
statistical one, and it is documented rather than buried.

### 5.6 Model vs a human checklist

`Flight Risk Score (Rule-Based)` is a transparent weighted checklist implemented
both in Python and as a DAX measure, shown beside the model on the Watchlist
page. They correlate only **r = 0.46**, with 48% top-decile overlap.

That gap is the model earning its place: a checklist weights every overtime
employee identically; the model knows overtime on a 2-year Sales Rep in the
bottom pay quartile is a different proposition from overtime on a 12-year
Finance Manager.

---

## 6. Layer 5 — Power BI

`powerbi/` is a **PBIP project with a TMDL semantic model** — 14 tables, 6
relationships, **22 documented DAX measures**, 4 pages, **23 visuals** — all
generated by `generators/build_powerbi.py` and `build_report_pages.py`.

The generator **introspects column types from the live SQL views**, so the
semantic model cannot drift from the database schema: change a view, re-run,
and the model follows.

### 6.1 Selected DAX

```dax
Attrition Rate = DIVIDE ( [Terminated Count], [Total Headcount] )

-- point-in-time headcount. ALL() is required, or the date relationship
-- restricts this to people who already left.
Headcount (EOP) =
VAR AsOf = MAX ( DimDate[date_key] )
RETURN
    CALCULATE (
        COUNTROWS ( DimEmployee ),
        FILTER (
            ALL ( DimEmployee ),
            DimEmployee[hire_date] <= AsOf
                && ( ISBLANK ( DimEmployee[termination_date] )
                     || DimEmployee[termination_date] > AsOf )
        )
    )

Rolling 12-Month Attrition Rate =
VAR AsOf   = MAX ( DimDate[date_key] )
VAR Window = DATESINPERIOD ( DimDate[date_key], AsOf, -12, MONTH )
RETURN
    DIVIDE (
        CALCULATE ( [Terminated Count], Window ),
        AVERAGEX ( FILTER ( Window, DimDate[is_month_end] ), [Headcount (EOP)] )
    )

YoY Attrition Change =
    [Attrition Rate (Period)]
        - CALCULATE ( [Attrition Rate (Period)], SAMEPERIODLASTYEAR ( DimDate[date_key] ) )
```

`Avg Headcount (Period)` averages **month-end** headcounts rather than the
period endpoints: using end-of-period headcount alone understates the
denominator in a shrinking team and therefore *overstates* its attrition rate —
exactly wrong for the teams already under scrutiny.

### 6.2 The date table is marked in the TMDL

`dataCategory: Time` plus `isKey` on `date_key` are serialised into the model —
exactly what Desktop writes when you use *Mark as date table*. Skipping that step
makes every time-intelligence measure return wrong numbers **silently**, with no
error. It should not depend on anyone remembering a ribbon click.

### 6.3 Why the report imports files rather than connecting live

The model originally connected straight to PostgreSQL. That is the better line on
a CV, and it was the wrong call for a portfolio artefact:

- **A reviewer without the database password sees a report full of `(Blank)`.**
- **Supabase's pooler certificate chains to no root Windows trusts**, so Npgsql
  rejected it and every table failed with *"The remote certificate is invalid."*
- **The free tier pauses the database.** Two active projects per org; create a
  third and one gets paused automatically — which happened mid-build.

So the model imports `data/powerbi/*.csv`. **No analytical fidelity is lost:**
`export_for_powerbi.py` produces those files by running the actual shipped `.sql`
views. The live-connection M is retained and documented in the build guide.

### 6.4 Why there is no `.pbix`

A `.pbix` is an opaque binary that cannot be diffed, reviewed or merged — which
is precisely why Microsoft built PBIP. The text project is the better git
artefact; `File → Save As` produces a binary in one step when one is needed.

---

## 7. Layer 6 — The hosted dashboard

`web/template.html` + `web/data.json` → `docs/index.html`, served by GitHub Pages.

- **Self-contained single file.** The query results are inlined at build time,
  so there is no fetch, no API dependency, and it works even when the database
  is paused.
- **No framework, no build step, no CDN.** Charts are hand-built SVG and CSS.
- **Theme-aware** across light, dark and the unstamped system default.
- **Interactive watchlist** with department, tier and name filters.

Design: deep petrol-blue accent with cool-biased neutrals; separate semantic
scale (brick / ochre / forest) because attrition data is fundamentally a severity
read; Archivo for headings, IBM Plex Sans for body, IBM Plex Mono for every
figure so columns of numbers align.

---

## 8. Layer 7 — Hosting and security

| Piece | Where | State |
|---|---|---|
| Dashboard | GitHub Pages `/docs` | Live, HTTP 200 |
| Database | Supabase Postgres, `ap-south-1` | 1,470 employees, 2,466 scores, 16 views |
| REST API | Supabase PostgREST | Anonymous **read-only** |
| Repo | GitHub | 27 commits, 100 files |

### Security posture — `sql/rls_policies.sql`

Anonymous **read** is intentional: it is what lets the dashboard query the
database with no server-side secret, over a public demo dataset with synthesised
names. Anonymous **write** is closed twice over:

1. RLS enabled on all 7 tables with SELECT-only policies
2. `INSERT/UPDATE/DELETE/TRUNCATE` revoked from `anon` and `authenticated`,
   including from default privileges
3. All 16 views set `security_invoker = on`, so a view cannot bypass the policies
   on the table beneath it — without this, the planned per-manager RLS on the
   watchlist would be decorative

**Verified empirically:** anon `SELECT` returns data; anon `INSERT` and `DELETE`
both return `42501 permission denied`; Supabase's security advisor reports
**zero lints** (was 23).

**Critically, this runs as step 4 of every load.** `schema.sql` opens with
`DROP TABLE ... CASCADE`, and dropping a table takes its RLS policies with it.
Security applied as a one-off migration is destroyed by the next rebuild — which
is exactly what happened once during this build, leaving all seven tables exposed
until it was caught.

---

## 9. Testing and verification

### 9.1 Cross-engine parity — `generators/verify_parity.py`

Developing against DuckDB and shipping to Postgres only works if the two agree.
This runs **13 checks, including row-level comparison across all 1,470
employees**, against both engines and exits non-zero on any difference.

**Result: 13/13 identical.**

It earned its place immediately by catching two bugs that raised no error on
either engine:

- **`NTILE(4)` was non-deterministic.** NTILE forces equal-sized buckets, so
  employees sharing a salary across a quartile boundary *must* be split — and
  which tied row landed where was arbitrary. Postgres and DuckDB disagreed by one
  employee on the Q3/Q4 boundary. Fixed with an `employee_id` tiebreaker.
- **`::NUMERIC` meant two different things.** Postgres reads it as arbitrary
  precision; DuckDB defaults it to `DECIMAL(18,3)`. So `ROUND(x::NUMERIC, 4)`
  silently truncated pay percentiles to three decimals on DuckDB only. Fixed with
  an explicit `NUMERIC(12,8)`.

Both were quiet wrong-answer bugs — the kind a test has to catch because review
will not.

### 9.2 Other verification

- `run_views_local.py` executes the **exact `.sql` files that ship to Postgres**
  against DuckDB and regenerates `docs/view_results.json`, so every documented
  figure is reproducible
- `load_to_postgres.py --verify` runs sanity queries against the live database
- Deterministic rebuild verified by wiping `data/processed/` and re-running
- The dashboard was checked in a real browser for rendered pixel widths, both
  themes, filter behaviour and horizontal overflow
- Every generated JSON is parsed and every Power BI projection `queryRef` is
  checked against its query before commit

---

## 10. Bugs found and fixed — the honest list

This is worth reading as its own section, because it is what the commit history
actually documents.

| # | Bug | Why it mattered |
|---|---|---|
| 1 | **Tenure jitter applied to actives only** | Class-dependent leakage; produced a fake 71% attrition rate in the under-1-year band and flattered the model |
| 2 | **Non-deterministic `NTILE`** | Same query, different answer on different engines |
| 3 | **Bare `::NUMERIC`** | Silent 3-decimal truncation on DuckDB only |
| 4 | **RLS destroyed by rebuild** | All 7 tables briefly exposed with default write grants |
| 5 | **Dashboard bars rendered empty** | Width set from a `requestAnimationFrame` callback, which never fires in a backgrounded/zero-size viewport — correctness depended on an animation frame |
| 6 | **Mermaid ER diagram would not parse** | `FK UK` / `PK_FK` — mermaid allows one key token per attribute |
| 7 | **Wrong `.pbip` `$schema` + missing report folder** | Project would not open |
| 8 | **Table named `Measures`** | Reserved name in the Tabular model |
| 9 | **CompatibilityLevel downgrade 1606→1567** | Tabular rejects downgrades outright |
| 10 | **`DECIMAL` columns imported as text** | `AVERAGE()`/`MAX()` over text broke two visuals; surfaced as a misleading "capacity or license issue" |
| 11 | **Shared `DataFolder` parameter** | The model's only cross-query dependency; Power Query failed all 13 loads with "cyclic reference" |
| 12 | **Aggregation enum shifted** | Count is 2, not 4 — the risk-tier donut plotted *max employee_id* per tier instead of counting employees, and rendered without error |
| 13 | **Misleading grand totals** | Averaged six departmental rates into 0.14 against a true 0.1612 |

Several of these — 1, 10, 12 — were **wrong-number bugs that rendered without
any error**. Two structural changes were made so they cannot recur silently:
unmapped column types now **raise** instead of defaulting to string, and every
Power BI projection `queryRef` is validated against its query at build time.

---

## 11. Reproducing it

```bash
pip install -r requirements.txt
```

Order matters — the model must be scored before the seed file is generated:

```bash
python generators/build_dataset.py         # normalise + synthesise the time dimension
python generators/train_attrition_model.py # train, evaluate, score, write charts
python generators/build_seed_sql.py        # emit sql/seed_data.sql (includes scores)
python generators/run_views_local.py       # run all 8 views on DuckDB, no server
python generators/build_notebook.py        # regenerate the executed notebook
python generators/export_for_powerbi.py    # run the views -> data/powerbi/*.csv
python generators/build_powerbi.py         # regenerate the TMDL model + report
python generators/build_dashboard.py       # rebuild the web dashboard
```

Load into PostgreSQL and verify:

```bash
cp .env.example .env      # add DATABASE_URL (session pooler, port 5432)
python generators/load_to_postgres.py
python generators/verify_parity.py
```

---

## 12. What I'd do differently in production

- **Ingestion** — scheduled HRIS pull (Workday / BambooHR) into a staging schema,
  dbt for the transform, `dbt test` enforcing what are currently only DDL checks.
- **Point-in-time features** — the biggest correctness gap. A real feature store
  snapshotting every employee at a fixed lookback date removes bug #1's whole
  class.
- **Model monitoring** — scores are retained so drift is *observable*, but nothing
  watches it. Production needs PSI on feature distributions, scheduled
  retraining, and crucially **tracking whether flagged employees actually left**
  — the only real measure of whether this works. There is no feedback loop today.
- **Row-level security in Power BI** — managers must see only their own team, via
  `DimEmployee[manager_name] = USERPRINCIPALNAME()`. Without it every manager can
  read every flight-risk score in the company.
- **Incremental refresh** — full refresh is fine at 1,470 employees and wrong at
  50,000.
- **Governance** — a flight-risk score attached to a named employee is sensitive
  personal data. It needs a retention policy, an audit log, and a written policy
  that scores never inform performance or termination decisions.
- **Causal inference** — every finding is associational. The overtime finding is
  strong enough to justify a staggered rollout of workload caps with
  difference-in-differences on the result.

---

## 13. Interview cheat-sheet

**One-line pitch.** An HR attrition platform: normalised Postgres schema with
SCD Type 2 history, eight analytical SQL views, a Power BI semantic model with 22
DAX time-intelligence measures, and a scikit-learn model scored back into the
database — with cross-engine parity testing.

**Best technical story — finding 6.** Engineering's 17.6% crude attrition looked
like the problem and is 47% of all outflow. Its standardised attrition ratio is
1.01: it loses exactly what its tenure mix predicts. The crude ranking was
rewarding departments that stopped hiring. Sales at 1.36 is the real problem.

**Best engineering story — cross-engine parity.** Developing on DuckDB and
shipping to Postgres, a parity test caught a non-deterministic `NTILE` and a
`::NUMERIC` precision difference — both silent wrong answers.

**Best judgement story — the negative result.** Span of control shows no
relationship to attrition, and the project says so rather than dressing up
small-sample noise.

**Best honesty story — the leakage bug.** A synthetic-data artefact created a
71% attrition rate in the under-1-year band that the model happily exploited.
Found, fixed, and documented as the argument for point-in-time feature stores.

**Expect: "why logistic regression over the forest?"** They were tied on
cross-validated PR-AUC (0.507 vs 0.508, inside a 0.065 fold-to-fold SD). The
forest bought no accuracy, so interpretability was free — and when HR names an
employee, the next question is always *why*.
