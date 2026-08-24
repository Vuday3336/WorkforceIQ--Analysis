-- =====================================================================
-- vw_attrition_risk_watchlist
--
-- BUSINESS QUESTION
--   "Who is at elevated flight risk RIGHT NOW, and what is driving it?"
--
-- This is the view that turns the project from a retrospective report into
-- an operational tool. It joins the model output in attrition_risk_scores
-- back onto the human context an HR business partner needs in order to do
-- something about it -- manager, tenure, pay position, overtime, last
-- satisfaction score -- because a bare probability is not actionable.
--
-- WHY IT IS BUILT THIS WAY
--   * ACTIVE EMPLOYEES ONLY. Scoring someone who already resigned is not a
--     prediction, and leaving them in the table would flatter the numbers.
--   * LATEST SCORING RUN ONLY. attrition_risk_scores keeps every run so
--     drift stays observable, so the view has to pick the most recent
--     scored_date per employee per model rather than fanning out history.
--   * The rule-based flags travel WITH the score. A manager who is told
--     "0.71" will ignore it; one who is told "0.71 -- overtime, bottom
--     salary quartile for their role, satisfaction 2/4" can act today.
--     The flags are also the fallback explanation when the model is
--     retrained and coefficients move.
--
-- GRAIN: one row per active employee per model.
-- =====================================================================

CREATE OR REPLACE VIEW vw_attrition_risk_watchlist AS
WITH latest_scores AS (
    SELECT s.employee_id,
           s.model_name,
           s.scored_date,
           s.risk_score,
           s.risk_tier,
           ROW_NUMBER() OVER (
               PARTITION BY s.employee_id, s.model_name
               ORDER BY s.scored_date DESC
           ) AS rn
    FROM attrition_risk_scores s
)

SELECT e.employee_id,
       e.first_name || ' ' || e.last_name        AS employee_name,
       d.department_name,
       d.division,
       e.job_role,
       e.job_level,
       e.hire_date,
       e.distance_from_home,
       e.marital_status,

       mgr.employee_id                            AS manager_id,
       mgr.first_name || ' ' || mgr.last_name     AS manager_name,

       ls.model_name,
       ls.scored_date,
       ls.risk_score,
       ls.risk_tier,

       cp.tenure_years,
       cp.monthly_income,
       cp.income_pct_rank_in_role,
       cp.income_quartile_label,
       cp.pct_vs_role_average,

       lr.latest_review_date,
       lr.job_satisfaction,
       lr.environment_satisfaction,
       lr.work_life_balance,
       lr.overtime_flag,
       lr.performance_rating,

       -- the plain-English drivers that make the score actionable
       CASE WHEN lr.overtime_flag = 'Yes'            THEN 1 ELSE 0 END AS flag_overtime,
       CASE WHEN lr.job_satisfaction <= 2            THEN 1 ELSE 0 END AS flag_low_satisfaction,
       CASE WHEN lr.work_life_balance <= 2           THEN 1 ELSE 0 END AS flag_poor_wlb,
       CASE WHEN cp.income_quartile_in_role = 1      THEN 1 ELSE 0 END AS flag_bottom_pay_quartile,
       CASE WHEN cp.tenure_years < 3                 THEN 1 ELSE 0 END AS flag_early_tenure,
       CASE WHEN e.distance_from_home >= 20          THEN 1 ELSE 0 END AS flag_long_commute,

       (CASE WHEN lr.overtime_flag = 'Yes'       THEN 1 ELSE 0 END
      + CASE WHEN lr.job_satisfaction <= 2       THEN 1 ELSE 0 END
      + CASE WHEN lr.work_life_balance <= 2      THEN 1 ELSE 0 END
      + CASE WHEN cp.income_quartile_in_role = 1 THEN 1 ELSE 0 END
      + CASE WHEN cp.tenure_years < 3            THEN 1 ELSE 0 END
      + CASE WHEN e.distance_from_home >= 20     THEN 1 ELSE 0 END) AS risk_flag_count

FROM employees e
JOIN departments d                ON d.department_id = e.department_id
JOIN latest_scores ls             ON ls.employee_id  = e.employee_id AND ls.rn = 1
LEFT JOIN employees mgr           ON mgr.employee_id = e.manager_id
LEFT JOIN vw_compensation_percentile cp ON cp.employee_id = e.employee_id
LEFT JOIN vw_employee_latest_review  lr ON lr.employee_id = e.employee_id
WHERE e.current_status = 'Active';
