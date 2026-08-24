-- =====================================================================
-- vw_compensation_percentile
--
-- BUSINESS QUESTION
--   "Are people who are underpaid RELATIVE TO THEIR PEERS leaving more?"
--
-- WHY IT IS BUILT THIS WAY
--   Raw salary is useless for this question -- a Support Representative on
--   4,000/month is well paid and an Engineering Director on 4,000/month is
--   about to resign. The comparison only means anything WITHIN a job role,
--   so PERCENT_RANK() is partitioned by job_role. NTILE(4) is carried
--   alongside because quartile labels are what an HR business partner will
--   actually act on in a conversation.
--
--   Salary is taken from the effective-dated compensation_history at the
--   correct point in time: the last row on or before termination for a
--   leaver, the latest row for an active employee. Joining to "current"
--   salary for someone who left 18 months ago would silently compare them
--   against a market that moved on without them.
--
-- GRAIN: one row per employee.
-- =====================================================================

CREATE OR REPLACE VIEW vw_employee_current_comp AS
WITH ranked AS (
    SELECT c.employee_id,
           c.effective_date,
           c.monthly_income,
           c.salary_hike_pct,
           c.stock_option_level,
           ROW_NUMBER() OVER (
               PARTITION BY c.employee_id
               ORDER BY c.effective_date DESC
           ) AS rn
    FROM compensation_history c
    JOIN employees e ON e.employee_id = c.employee_id
    LEFT JOIN attrition_events a ON a.employee_id = c.employee_id
    -- point-in-time correctness: never look past the person's exit
    WHERE c.effective_date <= COALESCE(a.termination_date, DATE '2025-12-31')
)
SELECT employee_id,
       effective_date AS comp_effective_date,
       monthly_income,
       salary_hike_pct,
       stock_option_level
FROM ranked
WHERE rn = 1;


CREATE OR REPLACE VIEW vw_compensation_percentile AS
WITH emp_comp AS (
    SELECT e.employee_id,
           e.first_name,
           e.last_name,
           e.department_id,
           d.department_name,
           e.job_role,
           e.job_level,
           e.current_status,
           t.attrition_flag,
           t.tenure_years,
           cc.monthly_income,
           cc.salary_hike_pct,
           cc.stock_option_level
    FROM employees e
    JOIN departments d              ON d.department_id = e.department_id
    JOIN vw_employee_current_comp cc ON cc.employee_id = e.employee_id
    JOIN vw_employee_tenure t        ON t.employee_id  = e.employee_id
),

ranked AS (
    SELECT ec.*,

           -- where this person sits inside their own job role
           ROUND(PERCENT_RANK() OVER (
                     PARTITION BY ec.job_role ORDER BY ec.monthly_income
                 )::NUMERIC, 4) AS income_pct_rank_in_role,

           NTILE(4) OVER (
               PARTITION BY ec.job_role ORDER BY ec.monthly_income
           ) AS income_quartile_in_role,

           -- how far off the role's midpoint they are, in percent
           ROUND(
               (ec.monthly_income
                - AVG(ec.monthly_income) OVER (PARTITION BY ec.job_role))
               / NULLIF(AVG(ec.monthly_income) OVER (PARTITION BY ec.job_role), 0) * 100
           , 1) AS pct_vs_role_average,

           COUNT(*) OVER (PARTITION BY ec.job_role) AS role_peer_count

    FROM emp_comp ec
)

SELECT employee_id,
       first_name,
       last_name,
       department_id,
       department_name,
       job_role,
       job_level,
       current_status,
       attrition_flag,
       tenure_years,
       monthly_income,
       salary_hike_pct,
       stock_option_level,
       income_pct_rank_in_role,
       income_quartile_in_role,
       CASE income_quartile_in_role
           WHEN 1 THEN 'Q1 (lowest paid in role)'
           WHEN 2 THEN 'Q2'
           WHEN 3 THEN 'Q3'
           WHEN 4 THEN 'Q4 (highest paid in role)'
       END AS income_quartile_label,
       pct_vs_role_average,
       role_peer_count
FROM ranked;


-- The roll-up the Compensation page actually charts.
CREATE OR REPLACE VIEW vw_attrition_by_comp_quartile AS
SELECT income_quartile_in_role,
       MIN(income_quartile_label)            AS income_quartile_label,
       COUNT(*)                              AS employees,
       SUM(attrition_flag)                   AS leavers,
       ROUND(AVG(attrition_flag * 1.0), 4)   AS attrition_rate,
       ROUND(AVG(monthly_income), 0)         AS avg_monthly_income
FROM vw_compensation_percentile
GROUP BY income_quartile_in_role
ORDER BY income_quartile_in_role;
