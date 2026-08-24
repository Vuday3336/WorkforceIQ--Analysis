-- =====================================================================
-- vw_department_attrition_controlled
--
-- BUSINESS QUESTION
--   "Which departments have a RETENTION problem, as opposed to simply
--    having a young workforce?"
--
-- THE CONFOUNDER
--   Tenure is the strongest single predictor of leaving, and departments do
--   not have the same tenure mix. A department that has doubled in size in
--   two years is mostly made up of 0-2 year employees -- the cohort that
--   leaves most -- so it will post a high crude attrition rate even if it
--   retains people better than average AT EVERY TENURE LEVEL. Ranking
--   departments on the crude rate therefore rewards the ones that stopped
--   hiring and punishes the ones that grew. That ranking is worse than
--   useless: it is actively misleading, and it is what the quarterly PDF
--   this project replaces was doing.
--
-- THE METHOD -- indirect standardisation
--   Borrowed from epidemiology, where the same problem appears as comparing
--   mortality between regions with different age structures.
--
--     1. Compute the company-wide attrition rate for each tenure cohort.
--        This is the "standard schedule" -- what leaving looks like at a
--        given tenure, company-wide.
--     2. For each department, apply that standard schedule to ITS OWN
--        tenure mix. The result is EXPECTED leavers: how many people this
--        department would have lost if it were exactly average at every
--        tenure level, given who it actually employs.
--     3. Compare observed leavers to expected.
--
--        SAR = observed / expected      (Standardised Attrition Ratio)
--
--        SAR > 1  -> loses more people than its tenure mix explains
--        SAR ~ 1  -> performing to expectation; the crude rate is mix
--        SAR < 1  -> retains BETTER than average, whatever the crude
--                    rate says
--
--     4. tenure_adjusted_rate = SAR x company crude rate, which puts the
--        result back on the familiar percentage scale so it can sit next
--        to the crude rate in the same visual.
--
--   What this does NOT claim: SAR adjusts for tenure mix only. Role mix,
--   pay position and overtime are still uncontrolled. Two departments with
--   the same SAR are comparable on tenure, not on everything.
--
-- GRAIN: one row per department.
-- =====================================================================

CREATE OR REPLACE VIEW vw_department_attrition_controlled AS
WITH tenure_banded AS (
    SELECT t.employee_id,
           t.department_id,
           t.attrition_flag,
           t.tenure_years,
           CASE
               WHEN t.tenure_years <  1 THEN '0-1 yr'
               WHEN t.tenure_years <  3 THEN '1-3 yr'
               WHEN t.tenure_years <  5 THEN '3-5 yr'
               WHEN t.tenure_years < 10 THEN '5-10 yr'
               ELSE '10 yr+'
           END AS tenure_cohort
    FROM vw_employee_tenure t
),

-- STEP 1: the standard schedule -- company-wide attrition rate by cohort
company_cohort_rates AS (
    SELECT tenure_cohort,
           COUNT(*)                    AS company_employees,
           AVG(attrition_flag * 1.0)   AS company_cohort_rate
    FROM tenure_banded
    GROUP BY tenure_cohort
),

company_overall AS (
    SELECT AVG(attrition_flag * 1.0) AS company_crude_rate,
           COUNT(*)                  AS company_headcount
    FROM tenure_banded
),

-- STEP 2 + 3: apply the standard schedule to each department's own mix
dept_expected AS (
    SELECT tb.department_id,
           COUNT(*)                          AS employees,
           SUM(tb.attrition_flag)            AS observed_leavers,
           -- expected leavers = sum over this department's people of the
           -- company-wide rate for the cohort each person sits in
           SUM(ccr.company_cohort_rate)      AS expected_leavers,
           AVG(tb.tenure_years)              AS avg_tenure_years
    FROM tenure_banded tb
    JOIN company_cohort_rates ccr
      ON ccr.tenure_cohort = tb.tenure_cohort
    GROUP BY tb.department_id
)

SELECT d.department_id,
       d.department_name,
       d.division,

       de.employees                                        AS headcount,
       de.observed_leavers,
       ROUND(de.expected_leavers, 1)                        AS expected_leavers,
       ROUND(de.avg_tenure_years, 2)                        AS avg_tenure_years,

       -- the number the old quarterly PDF reported
       ROUND(de.observed_leavers * 1.0
             / NULLIF(de.employees, 0), 4)                  AS crude_attrition_rate,

       -- STEP 3
       ROUND(de.observed_leavers
             / NULLIF(de.expected_leavers, 0), 3)           AS standardised_attrition_ratio,

       -- STEP 4: SAR expressed back on the percentage scale
       ROUND(de.observed_leavers
             / NULLIF(de.expected_leavers, 0)
             * co.company_crude_rate, 4)                    AS tenure_adjusted_rate,

       -- how much of the crude rate was tenure mix rather than retention
       ROUND(de.observed_leavers * 1.0 / NULLIF(de.employees, 0)
             - (de.observed_leavers / NULLIF(de.expected_leavers, 0)
                * co.company_crude_rate), 4)                AS mix_effect,

       ROUND(co.company_crude_rate, 4)                      AS company_crude_rate,

       CASE
           WHEN de.observed_leavers / NULLIF(de.expected_leavers, 0) >= 1.15
               THEN 'Worse than tenure predicts'
           WHEN de.observed_leavers / NULLIF(de.expected_leavers, 0) <= 0.85
               THEN 'Better than tenure predicts'
           ELSE 'In line with tenure mix'
       END AS verdict,

       -- with a department this small, do not over-read the ratio
       CASE WHEN de.expected_leavers >= 10 THEN 1 ELSE 0 END AS has_reliable_sample

FROM dept_expected de
JOIN departments   d ON d.department_id = de.department_id
CROSS JOIN company_overall co
ORDER BY standardised_attrition_ratio DESC;


-- The cohort schedule itself, exposed so the adjustment is auditable
-- rather than a black box sitting inside one view.
CREATE OR REPLACE VIEW vw_company_cohort_rates AS
SELECT CASE
           WHEN tenure_years <  1 THEN '0-1 yr'
           WHEN tenure_years <  3 THEN '1-3 yr'
           WHEN tenure_years <  5 THEN '3-5 yr'
           WHEN tenure_years < 10 THEN '5-10 yr'
           ELSE '10 yr+'
       END                                 AS tenure_cohort,
       COUNT(*)                            AS employees,
       SUM(attrition_flag)                 AS leavers,
       ROUND(AVG(attrition_flag * 1.0), 4) AS company_cohort_rate
FROM vw_employee_tenure
GROUP BY 1
ORDER BY 1;
