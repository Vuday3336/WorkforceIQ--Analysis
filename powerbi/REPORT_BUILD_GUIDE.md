# Power BI — Build Guide

## What is in this folder, and what is not

| File | What it is |
|---|---|
| `WorkforceIQ.pbip` | Power BI project file. **Open this with Power BI Desktop.** |
| `WorkforceIQ.SemanticModel/` | The full semantic model in TMDL — 14 tables, 6 relationships, 22 DAX measures. Plain text, diffable, reviewable. |
| `measures.dax` | Every measure in one readable file, with the reasoning for each. |
| *(no `.pbix`)* | See below. |

**Why there is no `.pbix` in this repo.** A `.pbix` is a proprietary binary that
only Power BI Desktop can write, and Desktop is Windows-only. It was not
available in the environment this repo was generated in, so shipping one would
have meant shipping something untested.

That is not really a loss. **PBIP/TMDL is the format you want in source
control** — a `.pbix` is an opaque blob that cannot be diffed, code-reviewed or
merged, which is why Microsoft built PBIP in the first place. Desktop opens the
`.pbip` natively and `File → Save As → .pbix` produces a binary in one step if
one is needed for sharing.

The report *pages* are specified below rather than serialised, for the same
reason: the PBIR visual layer is version-pinned JSON that cannot be validated
without Desktop, and an unopenable file is worse than a precise spec. The
semantic model — the part that carries the actual analytical work — is complete.

---

## Step 1 — connect

1. Open `WorkforceIQ.pbip` in Power BI Desktop.
2. **Transform data → Manage parameters**, set:
   - `ServerName` → `db.<your-project-ref>.supabase.co`
   - `DatabaseName` → `postgres`
3. Refresh. Credentials: **Database** auth, user `postgres`, your Postgres
   password. Encryption: **enable**.

Connection is a **live PostgreSQL connector connection to the analytical views**
— not a CSV import. That distinction is worth stating on a résumé, because most
portfolio dashboards import a flat file and lose the entire SQL layer.

## Step 2 — mark the date table (do not skip)

**Table tools → Mark as date table → `DimDate[date_key]`.**

Every time-intelligence measure (`SAMEPERIODLASTYEAR`, `DATESINPERIOD`,
`DATESYTD`) returns wrong numbers *silently* without this. No error, just
incorrect values — which is the worst failure mode there is.

## Step 3 — verify the model

Relationships that should exist (all single-direction, one-to-many):

```
DimDepartment[department_id] 1 --> * DimEmployee[department_id]
DimEmployee[employee_id]     1 --> * FactAttrition[employee_id]
DimEmployee[employee_id]     1 --> * FactCompensation[employee_id]
DimEmployee[employee_id]     1 --> * FactReview[employee_id]
DimEmployee[employee_id]     1 --> * RiskScores[employee_id]
DimDate[date_key]            1 --> * FactAttrition[termination_date]
```

Sanity check before building any visual — drop these on a blank page:

| Measure | Expected |
|---|--:|
| `Total Headcount` | 1,470 |
| `Active Headcount` | 1,233 |
| `Terminated Count` | 237 |
| `Attrition Rate` | 16.1% |
| `High Risk Employees` | 124 |

If `Terminated Count` shows 237 but `Attrition Rate` does not show 16.1%, the
DimEmployee relationship is filtering in the wrong direction.

---

## Page 1 — Executive Overview

**Question it answers:** how bad is it, where, and is it moving?

**KPI cards** (row across the top)
| Card | Measure |
|---|---|
| Active Headcount | `[Active Headcount]` |
| YTD Attrition Rate | `[YTD Attrition Rate]` |
| YoY Change | `[YoY Attrition Change]` — conditional colour: red > 0, green < 0 |
| Rolling 12-Month Rate | `[Rolling 12-Month Attrition Rate]` |
| High Risk Employees | `[High Risk Employees]` |

**Visuals**
1. **Clustered bar — crude vs tenure-adjusted attrition by department.**
   Source `DepartmentControlled`. Axis `department_name`, values
   `crude_attrition_rate` and `tenure_adjusted_rate`.
   *This is the most important visual in the report.* Put a text box beside it:
   > Engineering's 17.6% crude rate is the highest of the large departments,
   > but its tenure-adjusted ratio is 1.01 — exactly average. Its rate is
   > workforce age, not retention. Sales (1.36) and HR (1.40) are the real
   > problems.

2. **Line chart — attrition trend.** Axis `DimDate[year_quarter]`, values
   `[Attrition Rate (Period)]` and `[Rolling 12-Month Attrition Rate]`.
   Caption it as demonstrating the rolling-window measure; the underlying
   quarterly slope is a property of the synthesised termination dates
   (see `docs/sql_findings.md`), not a discovered trend.

3. **Table — department scorecard.** From `DepartmentControlled`:
   `department_name`, `headcount`, `crude_attrition_rate`,
   `standardised_attrition_ratio`, `verdict`. Data bars on the SAR column,
   centred at 1.0.

**Slicers:** `DimDepartment[division]`, `DimDate[year]`.

---

## Page 2 — Tenure & Cohort Analysis

**Question:** do we lose people early, or after they are established?

1. **Column + line combo.** From `TenureCohort`: axis `tenure_cohort` (sort by
   `cohort_sort`), columns `employees`, line `attrition_rate`.
2. **Bar — share of all leavers** by cohort, from `TenureCohort[share_of_all_leavers]`.
   Annotate:
   > The 0–1 yr cohort has the highest *rate* (36.4%) but only 6.8% of actual
   > outflow. The 1–3 yr cohort is 36.3% of everyone who leaves. Fund that one.
3. **Matrix — cohort × department.** Rows `department_name`, columns
   `tenure_cohort`, values `[Attrition Rate]`, background colour scale.
4. **Scatter — avg tenure vs SAR.** From `DepartmentControlled`: X
   `avg_tenure_years`, Y `standardised_attrition_ratio`, size `headcount`.
   Reference line at Y = 1.0.

---

## Page 3 — Compensation & Satisfaction

**Question:** does pay position predict leaving, and what compounds it?

1. **Column — attrition by in-role pay quartile.** From `CompQuartile`.
   Note the non-monotonicity in a caption: Q4 ticks back up to 14.6%, so
   "underpaid people leave" is too simple a story.
2. **Matrix — the headline cross-segment.** From `OvertimeSatisfaction`: rows
   `overtime_flag`, columns `satisfaction_bucket`, values `attrition_rate` with
   a red-white colour scale, plus `lift_vs_base` as a second value.

   This is the money visual:
   > Overtime **and** low satisfaction → 36.6%, 2.27× the company rate and
   > 5.3× the 6.9% of employees with neither factor. Overtime alone (21.1%)
   > is worse than low satisfaction alone (13.5%) — high satisfaction does not
   > protect someone who is being worked too hard.

3. **Scatter — pay percentile vs attrition.** From `DimEmployee`: X
   `income_pct_rank_in_role`, Y `[Attrition Rate]`, legend `job_role`.
4. **Bar — span-of-control.** From `SpanBand`. Caption it as the **negative
   result** it is: no relationship between team size and attrition; the 20.3%
   in the 1–5 band is small-sample noise across 79 people.

---

## Page 4 — Attrition Risk Watchlist

**Question:** who is at risk right now, and what do I say to their manager?

The page that makes this an operational tool rather than a retrospective.

**Main table** — from `vw_attrition_risk_watchlist` (via `RiskScores` +
`DimEmployee`), sorted by `risk_score` descending, filtered to
`model_name = "logistic_regression"` and the latest `scored_date`:

| Column | Note |
|---|---|
| `employee_name` | |
| `department_name`, `job_role`, `manager_name` | |
| `risk_score` | data bars |
| `risk_tier` | conditional colour: High red, Medium amber, Low grey |
| `[Flight Risk Score (Rule-Based)]` | the explainable heuristic, side by side |
| `tenure_years`, `income_quartile_label`, `overtime_flag`, `job_satisfaction` | the context that makes it actionable |
| `risk_flag_count` | |

**Slicers:** `department_name`, `manager_name`, `risk_tier`, `overtime_flag`.

**Supporting visuals**
- Donut: employee count by `risk_tier`.
- Bar: High-risk count by department.
- Card: `[Risk Score Delta (Model - Rule)]` — where the model and the human
  heuristic disagree most. Those rows are the interesting ones: the heuristic
  weights every overtime employee identically, the model knows that overtime on
  a 2-year Sales Rep in the bottom pay quartile is not the same as overtime on a
  12-year Finance Manager. They agree only r = 0.47.

**Put a disclaimer text box on this page.** It flags named individuals:

> Model output, not a verdict. Scores are associations, not causes, and carry
> roughly ±0.06 PR-AUC of uncertainty. Use as a prompt for a conversation,
> never as an input to a performance or employment decision.

---

## Step 4 — row-level security (recommended)

The Watchlist names individuals, so a manager should see only their own team.

**Modeling → Manage roles → new role `Manager`:**

```dax
[manager_name] = USERPRINCIPALNAME()
```

on `DimEmployee` (mapping `manager_name` to the Entra UPN in production). Leave
an `HRBP` role unfiltered. Test with **View as → Manager**.

Without RLS every manager in the tenant can read every flight-risk score in the
company, which is a genuine HR-confidentiality problem rather than a nice-to-have.

---

## Step 5 — screenshots

Export one PNG per page into `docs/dashboard_screenshots/` and they will render
in the README, which already references them.
