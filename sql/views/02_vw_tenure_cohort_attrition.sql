-- =====================================================================
-- vw_tenure_cohort_attrition
--
-- BUSINESS QUESTION
--   "Do we lose people early, or after they are established?"
--   The answer changes the intervention completely: an early-tenure spike
--   is an onboarding / hiring-fit problem, a late-tenure spike is a career-
--   ceiling and compensation problem. Spending onboarding budget on a
--   late-tenure problem is the expensive mistake this view prevents.
--
-- WHY IT IS BUILT THIS WAY
--   Tenure has to be measured at the RIGHT MOMENT for each person. For a
--   leaver it is tenure at termination; for an active employee it is tenure
--   as of the snapshot date. Measuring everyone as of today would inflate
--   leavers' tenure by however long ago they left and smear them into the
--   wrong cohort -- the classic version of this analysis done wrong.
--
--   Rates are also broken out by department so the reader can see whether a
--   cohort problem is company-wide or concentrated.
--
-- GRAIN: one row per tenure cohort (plus a per-department variant view).
-- =====================================================================

CREATE OR REPLACE VIEW vw_employee_tenure AS
SELECT e.employee_id,
       e.department_id,
       e.job_role,
       e.current_status,
       e.hire_date,
       a.termination_date,
       CASE WHEN a.employee_id IS NULL THEN 0 ELSE 1 END AS attrition_flag,

       -- tenure at the moment that matters for this person
       CAST(
           (COALESCE(a.termination_date, DATE '2025-12-31') - e.hire_date) / 365.25
           AS NUMERIC(6,2)
       ) AS tenure_years

FROM employees e
LEFT JOIN attrition_events a
       ON a.employee_id = e.employee_id;


CREATE OR REPLACE VIEW vw_tenure_cohort_attrition AS
WITH cohorts AS (
    SELECT t.*,
           CASE
               WHEN t.tenure_years <  1 THEN '0-1 yr'
               WHEN t.tenure_years <  3 THEN '1-3 yr'
               WHEN t.tenure_years <  5 THEN '3-5 yr'
               WHEN t.tenure_years < 10 THEN '5-10 yr'
               ELSE '10 yr+'
           END AS tenure_cohort,
           CASE
               WHEN t.tenure_years <  1 THEN 1
               WHEN t.tenure_years <  3 THEN 2
               WHEN t.tenure_years <  5 THEN 3
               WHEN t.tenure_years < 10 THEN 4
               ELSE 5
           END AS cohort_sort
    FROM vw_employee_tenure t
),

base AS (
    SELECT AVG(attrition_flag * 1.0) AS company_attrition_rate
    FROM cohorts
)

SELECT c.tenure_cohort,
       c.cohort_sort,
       COUNT(*)                                  AS employees,
       SUM(c.attrition_flag)                     AS leavers,
       ROUND(AVG(c.attrition_flag * 1.0), 4)     AS attrition_rate,
       ROUND(AVG(c.tenure_years), 2)             AS avg_tenure_years,

       -- share of ALL company leavers this cohort accounts for: a cohort can
       -- have a high rate but still be a small slice of the actual outflow
       ROUND(SUM(c.attrition_flag) * 1.0
             / NULLIF(SUM(SUM(c.attrition_flag)) OVER (), 0), 4) AS share_of_all_leavers,

       -- lift against the company base rate
       ROUND(AVG(c.attrition_flag * 1.0) / NULLIF(MAX(b.company_attrition_rate), 0), 2)
           AS lift_vs_company

FROM cohorts c
CROSS JOIN base b
GROUP BY c.tenure_cohort, c.cohort_sort
ORDER BY c.cohort_sort;


-- Same cohort logic, cut by department, for the drill-through on the
-- Tenure & Cohort page of the report.
CREATE OR REPLACE VIEW vw_tenure_cohort_by_department AS
WITH cohorts AS (
    SELECT t.*,
           CASE
               WHEN t.tenure_years <  1 THEN '0-1 yr'
               WHEN t.tenure_years <  3 THEN '1-3 yr'
               WHEN t.tenure_years <  5 THEN '3-5 yr'
               WHEN t.tenure_years < 10 THEN '5-10 yr'
               ELSE '10 yr+'
           END AS tenure_cohort,
           CASE
               WHEN t.tenure_years <  1 THEN 1
               WHEN t.tenure_years <  3 THEN 2
               WHEN t.tenure_years <  5 THEN 3
               WHEN t.tenure_years < 10 THEN 4
               ELSE 5
           END AS cohort_sort
    FROM vw_employee_tenure t
)
SELECT d.department_name,
       c.tenure_cohort,
       c.cohort_sort,
       COUNT(*)                              AS employees,
       SUM(c.attrition_flag)                 AS leavers,
       ROUND(AVG(c.attrition_flag * 1.0), 4) AS attrition_rate
FROM cohorts c
JOIN departments d ON d.department_id = c.department_id
GROUP BY d.department_name, c.tenure_cohort, c.cohort_sort
ORDER BY d.department_name, c.cohort_sort;
