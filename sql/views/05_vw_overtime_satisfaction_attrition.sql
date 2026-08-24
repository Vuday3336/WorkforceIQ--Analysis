-- =====================================================================
-- vw_overtime_satisfaction_attrition
--
-- BUSINESS QUESTION
--   "Overtime and low satisfaction each look bad on their own. Do they
--    compound?"
--
-- WHY IT IS BUILT THIS WAY
--   Reporting overtime attrition and satisfaction attrition as two separate
--   bar charts hides the thing that matters. Someone working overtime who
--   is otherwise happy is a very different retention risk from someone
--   working overtime who is already disengaged, and averaging them together
--   produces a moderate-looking number that justifies no action.
--
--   So the segmentation is done on BOTH dimensions simultaneously, and each
--   cell carries its lift against the company base rate. Lift is the column
--   that turns this from a table into a decision.
--
--   Satisfaction and overtime come from the MOST RECENT review on or before
--   the person's exit -- the last signal the company actually had about
--   them before they left. Using an average of all their reviews would blur
--   a sharp recent drop into years of contentment.
--
-- GRAIN: one row per (overtime x satisfaction bucket) cell.
-- =====================================================================

CREATE OR REPLACE VIEW vw_employee_latest_review AS
WITH ranked AS (
    SELECT r.employee_id,
           r.review_date,
           r.performance_rating,
           r.job_satisfaction,
           r.environment_satisfaction,
           r.work_life_balance,
           r.overtime_flag,
           r.manager_id,
           ROW_NUMBER() OVER (
               PARTITION BY r.employee_id
               ORDER BY r.review_date DESC
           ) AS rn
    FROM performance_reviews r
    LEFT JOIN attrition_events a ON a.employee_id = r.employee_id
    WHERE r.review_date <= COALESCE(a.termination_date, DATE '2025-12-31')
)
SELECT employee_id,
       review_date AS latest_review_date,
       performance_rating,
       job_satisfaction,
       environment_satisfaction,
       work_life_balance,
       overtime_flag,
       manager_id
FROM ranked
WHERE rn = 1;


CREATE OR REPLACE VIEW vw_overtime_satisfaction_attrition AS
WITH segmented AS (
    SELECT e.employee_id,
           t.attrition_flag,
           lr.overtime_flag,
           lr.job_satisfaction,
           CASE
               WHEN lr.job_satisfaction <= 2 THEN 'Low (1-2)'
               WHEN lr.job_satisfaction  = 3 THEN 'Medium (3)'
               ELSE 'High (4)'
           END AS satisfaction_bucket,
           CASE
               WHEN lr.job_satisfaction <= 2 THEN 1
               WHEN lr.job_satisfaction  = 3 THEN 2
               ELSE 3
           END AS satisfaction_sort
    FROM employees e
    JOIN vw_employee_tenure        t  ON t.employee_id  = e.employee_id
    JOIN vw_employee_latest_review lr ON lr.employee_id = e.employee_id
),

base AS (
    SELECT AVG(attrition_flag * 1.0) AS company_attrition_rate
    FROM segmented
)

SELECT s.overtime_flag,
       s.satisfaction_bucket,
       s.satisfaction_sort,
       COUNT(*)                                AS employees,
       SUM(s.attrition_flag)                   AS leavers,
       ROUND(AVG(s.attrition_flag * 1.0), 4)   AS attrition_rate,
       ROUND(MAX(b.company_attrition_rate), 4) AS company_base_rate,

       -- the headline column: how many times the company base rate this
       -- particular combination of conditions runs at
       ROUND(AVG(s.attrition_flag * 1.0)
             / NULLIF(MAX(b.company_attrition_rate), 0), 2) AS lift_vs_base,

       -- guard against reading a dramatic multiple off a handful of people
       CASE WHEN COUNT(*) >= 30 THEN 1 ELSE 0 END AS has_reliable_sample

FROM segmented s
CROSS JOIN base b
GROUP BY s.overtime_flag, s.satisfaction_bucket, s.satisfaction_sort
ORDER BY s.overtime_flag DESC, s.satisfaction_sort;


-- Three-way extension: overtime x satisfaction x work-life balance.
-- Used for the drill-through tooltip on the Compensation & Satisfaction page.
CREATE OR REPLACE VIEW vw_overtime_satisfaction_wlb_attrition AS
WITH segmented AS (
    SELECT t.attrition_flag,
           lr.overtime_flag,
           CASE WHEN lr.job_satisfaction  <= 2 THEN 'Low' ELSE 'OK' END AS satisfaction_flag,
           CASE WHEN lr.work_life_balance <= 2 THEN 'Poor' ELSE 'OK' END AS wlb_flag
    FROM employees e
    JOIN vw_employee_tenure        t  ON t.employee_id  = e.employee_id
    JOIN vw_employee_latest_review lr ON lr.employee_id = e.employee_id
)
SELECT overtime_flag,
       satisfaction_flag,
       wlb_flag,
       COUNT(*)                              AS employees,
       SUM(attrition_flag)                   AS leavers,
       ROUND(AVG(attrition_flag * 1.0), 4)   AS attrition_rate
FROM segmented
GROUP BY overtime_flag, satisfaction_flag, wlb_flag
ORDER BY attrition_rate DESC;
