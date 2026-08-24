# SQL Findings — Everline Corp Attrition Analysis

Every number on this page is produced by the view named in its heading, run
against the database built by `sql/schema.sql` + `sql/seed_data.sql`. Nothing
here is hand-typed: regenerate the whole set with

```bash
python generators/run_views_local.py --json
```

which writes `docs/view_results.json`.

**Snapshot date:** 2025-12-31 (close of FY2025)
**Population:** 1,470 employees — 1,233 active, 237 departed
**Company crude attrition rate:** **16.12%**

---

## A note on what is real and what is constructed

This matters for reading every finding below, so it goes first rather than in a
footnote.

The source is the public **IBM HR Analytics Employee Attrition & Performance**
dataset — 1,470 real rows. Every *driver* variable and the attrition label
itself are used **unmodified**: monthly income, job satisfaction, environment
satisfaction, work-life balance, overtime, performance rating, stock options,
salary hike, distance from home, age, gender, marital status, education, job
level, years at company, and the Attrition flag.

The source is a **single dateless snapshot**, so anything time-shaped had to be
constructed to make SCD modelling and time-intelligence DAX possible:

| Constructed | How |
|---|---|
| Employee names | Deterministic pick from a name pool |
| `hire_date` / `termination_date` | Back-dated from real `YearsAtCompany`; terminations spread across FY2024–FY2025 |
| `compensation_history` rows | Real current salary walked backwards through the real `PercentSalaryHike` |
| `performance_reviews` rows | Most recent review = the real source values; prior years jittered ±1 |
| Manager hierarchy | 3-level tree per department, seniority-ordered |
| Voluntary/involuntary split | Deterministic 80/20 |
| The 6-department overlay | Documented role → department map (below) |

**What this means for the findings.** Findings 2–6 below are about *drivers*
(tenure, pay position, overtime, satisfaction, manager span) and are real signal
from the source data. Finding 1's *quarter-over-quarter trend shape* is partly
by construction — terminations were distributed with a mild upward ramp — and is
therefore presented as a demonstration of the rolling-window SQL, **not** as a
discovered insight. It is labelled as such in that section too.

### The 6-department overlay

The source has three departments (R&D / Sales / HR); the Everline Corp scenario
calls for six. The mapping is deterministic and fully reversible:

| Source `JobRole` | Everline department | Everline `job_role` |
|---|---|---|
| Sales Executive | Sales | Sales Executive |
| Sales Representative | Sales | Sales Representative |
| Research Scientist | Engineering | Software Engineer |
| Laboratory Technician | Engineering | QA Engineer |
| Research Director | Engineering | Engineering Director |
| Manufacturing Director | Operations | Operations Director |
| Healthcare Representative | Customer Support | Support Representative |
| Human Resources | HR | HR Generalist |
| Manager | Finance | Finance Manager |

Headcount is 1,470 rather than the round "~2,000" of the original brief. Padding
the population with ~530 invented employees would have diluted a real attrition
label with synthetic ones and made every rate below partly fictional. Keeping
the real 1,470 was the better trade.

---

## 1. `vw_attrition_by_department`

**Business question:** Which departments are losing people fastest, and is it
getting worse — or is one bad quarter distorting the picture?

**SQL technique:** `CROSS JOIN` of departments × quarters (so zero-termination
quarters still produce rows instead of vanishing from the trend line), aggregate
`FILTER` clauses to compute start/end headcount and terminations in one pass,
and a `ROWS BETWEEN 3 PRECEDING AND CURRENT ROW` window frame for the rolling
4-quarter rate.

**Why the denominator is an average.** Headcount is measured at both ends of the
quarter and averaged. Using end-of-period headcount alone understates the
denominator in a shrinking team and therefore *overstates* its attrition rate —
which is precisely wrong for the teams already under scrutiny.

### Rolling 4-quarter attrition rate, as at 2025 Q4

| Department | Headcount | 4q terminations | Rolling 4q rate |
|---|--:|--:|--:|
| HR | 40 | 8 | **18.2%** |
| Sales | 319 | 46 | **13.5%** |
| Engineering | 520 | 59 | 10.8% |
| Customer Support | 122 | 4 | 3.3% |
| Operations | 135 | 4 | 3.0% |
| Finance | 97 | 1 | 1.0% |

**Finding:** HR and Sales run at roughly 4–18× the rate of Finance, Operations
and Customer Support. HR's 18.2% comes off a base of 40 people — eight
departures — so the rolling figure is doing real work there; the single-quarter
rate for HR in 2025 Q4 was 2.5%, and reading that number alone would have
concluded HR was fine.

**On the trend:** the company-wide quarterly rate moves 1.37% → 2.81% across the
eight quarters. As flagged above, that ramp is a property of how termination
dates were synthesised, not a discovered trend. The rolling-window SQL is the
deliverable here; the slope is not evidence.

---

## 2. `vw_tenure_cohort_attrition`

**Business question:** Do we lose people early, or after they are established?
The answer changes the intervention completely — an early-tenure spike is an
onboarding and hiring-fit problem, a late-tenure spike is a career-ceiling and
compensation problem. Spending onboarding budget on a late-tenure problem is the
expensive mistake this view exists to prevent.

**SQL technique:** `CASE`-based cohort bucketing over a tenure expression that is
**measured at the right moment for each person** — at termination for leavers, at
the snapshot date for active employees. Plus a windowed
`SUM(SUM(...)) OVER ()` to compute each cohort's share of total outflow.

**The trap this avoids:** measuring everyone's tenure as of today would inflate
each leaver's tenure by however long ago they left, smearing them into the wrong
cohort. That is the standard way this analysis gets done wrong.

| Tenure cohort | Employees | Leavers | Attrition rate | Lift vs company | Share of all leavers |
|---|--:|--:|--:|--:|--:|
| 0–1 yr | 44 | 16 | **36.4%** | 2.26× | 6.8% |
| 1–3 yr | 298 | 86 | **28.9%** | 1.79× | **36.3%** |
| 3–5 yr | 238 | 39 | 16.4% | 1.02× | 16.5% |
| 5–10 yr | 524 | 58 | 11.1% | 0.69× | 24.5% |
| 10 yr+ | 366 | 38 | 10.4% | 0.64× | 16.0% |

**Finding:** Attrition is an early-tenure phenomenon and it falls off a cliff.
The first three years carry **43% of all departures from 23% of the headcount**.
The 0–1 year cohort has the highest *rate* (36.4%), but the **1–3 year cohort is
the one to fund** — it is the single largest block of actual outflow at 36.3% of
all leavers, because a high rate applied to 298 people beats a higher rate
applied to 44.

Rate alone would have pointed the budget at the wrong cohort. That is why
`share_of_all_leavers` is in the view.

---

## 3. `vw_compensation_percentile`

**Business question:** Are people who are underpaid *relative to their peers*
leaving more?

**SQL technique:** `PERCENT_RANK()` and `NTILE(4)` partitioned by `job_role`,
plus a windowed role average for a "% vs role midpoint" column. Salary comes
from `compensation_history` at the correct point in time via a
`ROW_NUMBER()` "latest row on or before exit" pattern.

**Why partition by role:** raw salary is meaningless for this question. A
Support Representative on 4,000/month is well paid; an Engineering Director on
4,000/month is about to resign. The comparison only means anything *within* a
role.

| In-role pay quartile | Employees | Leavers | Attrition rate | Avg monthly income |
|---|--:|--:|--:|--:|
| Q1 (lowest paid in role) | 370 | 72 | **19.5%** | 4,481 |
| Q2 | 369 | 62 | 16.8% | 5,568 |
| Q3 | 367 | 50 | **13.6%** | 6,868 |
| Q4 (highest paid in role) | 364 | 53 | 14.6% | 9,139 |

At the decile extremes the gap is cleaner:

| Band | Employees | Leavers | Attrition rate |
|---|--:|--:|--:|
| Bottom 10% in role | 152 | 31 | **20.4%** |
| Middle 80% | 1,166 | 185 | 15.9% |
| Top 10% in role | 152 | 21 | **13.8%** |

**Finding:** Pay position matters, but **less than the intuition suggests, and
not monotonically**. Bottom-quartile employees leave at 19.5% against 13.6% for
Q3 — a 1.43× effect. But Q4, the *highest* paid in each role, ticks back up to
14.6%, breaking the monotone story that "underpaid people leave."

The most defensible reading: being in the bottom quartile of your role is a real
risk factor worth roughly 6 percentage points, but pay is a weaker lever here
than overtime (finding 5) and cannot be treated as the primary explanation. The
Q4 uptick is consistent with senior, highly-marketable people being recruited
away — a different problem with a different fix.

---

## 4. `vw_manager_span_attrition`

**Business question:** Do managers with larger teams lose more people? If span
of control drives attrition, the fix is org design — split the team, add a lead
— rather than manager coaching.

**SQL technique:** self-join through `employees.manager_id`, aggregation to
per-manager grain, `RANK()` within department, and a banded roll-up that
aggregates **people** rather than averaging per-manager rates.

**Two traps this view is written around:**

1. **Small-team noise.** A manager with 3 reports who loses 1 posts a 33% rate
   and tops any naive ranking. Hence `direct_reports` travels with every rate and
   a `has_reliable_sample` flag marks anything under 8 reports.
2. **Averaging rates.** Taking `AVG(team_attrition_rate)` across managers gives
   a 3-person team the same weight as a 22-person team. The roll-up sums
   headcount and leavers instead.

| Span band | Managers | Employees covered | Leavers | Attrition rate | Avg span |
|---|--:|--:|--:|--:|--:|
| 1–5 reports | 19 | 79 | 16 | 20.3% | 4.2 |
| 6–10 reports | 39 | 286 | 40 | **14.0%** | 7.3 |
| 11–15 reports | 26 | 343 | 57 | 16.6% | 13.2 |
| 16+ reports | 37 | 756 | 123 | 16.3% | 20.4 |

**Finding — this one is a negative result, and it is reported as such.**
There is **no meaningful relationship between span of control and attrition.**
The rate is flat across 6–10, 11–15 and 16+ reports (14.0% / 16.6% / 16.3%), and
the apparent 20.3% spike in the smallest band is exactly the small-sample
artefact the view was built to expose: 79 employees across 19 managers, where two
extra departures move the number by 2.5 points.

**So what:** do not restructure the org on this basis. Variation *between
individual managers* is large — several managers with 13–20 reports sit above
50% team attrition — but it does not track team size, which points at manager
quality or team composition rather than span. That is a coaching question, not an
org-design one.

Reporting this honestly is the point. The hypothesis was reasonable and the data
does not support it.

---

## 5. `vw_overtime_satisfaction_attrition` — the headline

**Business question:** Overtime and low satisfaction each look bad on their own.
Do they compound?

**SQL technique:** simultaneous multi-condition segmentation on two dimensions,
with each cell carrying its lift against the company base rate, computed via a
`CROSS JOIN` to a single-row base-rate CTE. A three-way extension adds work-life
balance. Satisfaction and overtime are read from the **most recent review on or
before exit** — the last signal the company actually had.

**Why not two separate charts:** someone working overtime who is otherwise happy
is a completely different retention risk from someone working overtime who is
already disengaged. Averaging them produces a moderate-looking number that
justifies no action.

| Overtime | Job satisfaction | Employees | Leavers | Attrition rate | Lift vs base |
|---|---|--:|--:|--:|--:|
| **Yes** | **Low (1–2)** | 153 | 56 | **36.6%** | **2.27×** |
| Yes | Medium (3) | 121 | 41 | 33.9% | 2.10× |
| Yes | High (4) | 142 | 30 | 21.1% | 1.31× |
| No | Low (1–2) | 416 | 56 | 13.5% | 0.83× |
| No | Medium (3) | 321 | 32 | 10.0% | 0.62× |
| No | High (4) | 317 | 22 | **6.9%** | 0.43× |

Adding work-life balance sharpens it further:

| Overtime | Satisfaction | Work-life balance | Employees | Attrition rate |
|---|---|---|--:|--:|
| Yes | Low | Poor | 39 | **41.0%** |
| Yes | Low | OK | 114 | 35.1% |
| Yes | OK | Poor | 87 | 29.9% |
| No | OK | OK | 455 | **6.6%** |

**Finding:** **Overtime is the dominant driver, and satisfaction modulates it.**

- Employees working overtime with low satisfaction attrit at **36.6% — 2.27× the
  company base rate** and **5.3× the rate of employees with neither risk factor**
  (6.9%).
- Stack on poor work-life balance and it reaches **41.0%, 6.2× the 6.6%** of the
  clean segment.
- The asymmetry is the real insight: **overtime alone (21.1% even among the
  highly satisfied) is worse than low satisfaction alone (13.5%).** High
  satisfaction does not protect someone who is being worked too hard — it only
  softens the blow. Overtime is the lever.

**So what:** 416 active employees carry the overtime flag. This is the single
most actionable segment in the dataset, and it is a workload-allocation problem
before it is an engagement problem.

---

## 6. `vw_department_attrition_controlled` — the one worth explaining in an interview

**Business question:** Which departments have a *retention* problem, as opposed
to simply having a young workforce?

### The confounder

Tenure is the strongest single predictor of leaving (finding 2: 36.4% at 0–1
years, 10.4% at 10+). Departments do not have the same tenure mix. A department
that doubled in size in two years is mostly 0–2 year employees — the cohort that
leaves most — so it posts a high crude attrition rate **even if it retains people
better than average at every tenure level.**

Ranking departments on the crude rate therefore rewards the departments that
stopped hiring and punishes the ones that grew. That ranking is not merely
imprecise; it is actively misleading, and it is what the quarterly PDF this
project replaces was doing.

### The method — indirect standardisation

Borrowed from epidemiology, where the identical problem appears as comparing
mortality between regions with different age structures.

1. Compute the company-wide attrition rate **for each tenure cohort**. This is
   the *standard schedule* — what leaving looks like at a given tenure.

   | Tenure cohort | Employees | Company rate |
   |---|--:|--:|
   | 0–1 yr | 44 | 36.4% |
   | 1–3 yr | 298 | 28.9% |
   | 3–5 yr | 238 | 16.4% |
   | 5–10 yr | 524 | 11.1% |
   | 10 yr+ | 366 | 10.4% |

2. For each department, apply that schedule to **its own tenure mix** to get
   *expected leavers*: how many it would have lost if it were exactly average at
   every tenure level, given who it actually employs.

3. Compare observed to expected:

   ```
   SAR = observed leavers / expected leavers      (Standardised Attrition Ratio)
   ```

4. `tenure_adjusted_rate = SAR × company crude rate`, to put it back on the
   familiar percentage scale so it can sit beside the crude rate in one visual.

### Result

| Department | Headcount | Avg tenure | Observed | Expected | Crude rate | **SAR** | Adjusted rate | Verdict |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| HR | 52 | 5.8 | 12 | 8.6 | 23.1% | **1.40** | 22.5% | Worse than tenure predicts |
| Sales | 409 | 7.1 | 90 | 66.1 | 22.0% | **1.36** | 21.9% | Worse than tenure predicts |
| Engineering | 631 | 6.3 | 111 | 109.7 | 17.6% | **1.01** | 16.3% | In line with tenure mix |
| Operations | 145 | 8.1 | 10 | 20.4 | 6.9% | **0.49** | 7.9% | Better than tenure predicts |
| Customer Support | 131 | 8.9 | 9 | 18.5 | 6.9% | **0.49** | 7.8% | Better than tenure predicts |
| Finance | 102 | 15.0 | 5 | 13.7 | 4.9% | **0.37** | 5.9% | Better than tenure predicts |

**Finding — and this is the one that changes a decision:**

**Engineering does not have a retention problem.** Its crude rate is 17.6%, above
the 16.1% company average, and it is by far the largest single source of
departures — **111 of 237 leavers, 47% of all outflow.** On the old report it
looked like the problem to solve. But its SAR is **1.01**: it loses almost
exactly the number of people its tenure mix predicts. Engineering has a *hiring
volume* consequence, not a retention failure. Sending a retention task force
there would find nothing to fix.

**Sales and HR are the real retention problems.** SAR 1.36 and 1.40 — they lose
36–40% more people than their tenure structure accounts for. Sales is the
priority of the two: same effect size, but 409 people against HR's 52, and HR's
expected-leaver count (8.6) is below the reliability threshold the view flags.

**Finance's 4.9% is mostly mix, not merit.** With an average tenure of 15 years
it sits almost entirely in the lowest-risk cohorts. Its SAR of 0.37 says it *does*
genuinely outperform — but the crude rate overstates by how much.

### What this does *not* claim

SAR adjusts for **tenure mix only**. Role mix, pay position and overtime exposure
remain uncontrolled — and finding 5 shows overtime is a strong driver, so a
department with unusually high overtime would still look bad here for reasons
that are not "management quality". Two departments with the same SAR are
comparable on tenure, not on everything. Saying so is part of the finding.

---

## Summary — what leadership should do

| Priority | Finding | Action |
|---|---|---|
| 1 | Overtime + low satisfaction = 36.6% attrition, 2.27× base; overtime alone beats low satisfaction alone | Treat overtime as a workload-allocation problem. 416 active employees carry the flag. |
| 2 | Sales SAR 1.36 across 409 people — a real retention gap, not tenure mix | Retention task force to Sales, not Engineering. |
| 3 | 1–3 year cohort = 36% of all outflow | Fund the 1–3 year experience, not just onboarding. |
| 4 | Engineering's 17.6% crude rate is entirely explained by tenure mix (SAR 1.01) | Stop treating Engineering as the problem. |
| 5 | Bottom in-role pay quartile leaves at 19.5% vs 13.6% (Q3) | Real but secondary; ~6pp effect, non-monotone. |
| 6 | Span of control shows no relationship to attrition | Do not restructure. Coaching question, not org design. |
