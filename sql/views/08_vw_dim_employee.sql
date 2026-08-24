-- =====================================================================
-- vw_dim_employee
--
-- The wide employee dimension the Power BI model imports as DimEmployee.
--
-- WHY A WIDE DIMENSION RATHER THAN LETTING POWER BI DO THE JOINS
--   Every attribute an employee-level slicer or DAX measure needs is
--   resolved here, in SQL, once: department, tenure, termination date, pay
--   position within role, and the most recent review signal. The
--   alternative -- importing six normalised tables and wiring them up with
--   model relationships -- pushes the same joins into the VertiPaq engine on
--   every visual refresh and makes measures like [Headcount (EOP)] depend on
--   relationship direction rather than on plain columns.
--
--   The normalised tables are still imported for the fact-level pages; this
--   view is the conformed dimension they all hang off.
--
--   Point-in-time correctness is inherited from views 03 and 05: pay and
--   satisfaction are as at the person's exit for leavers, as at the snapshot
--   for active employees.
--
-- GRAIN: one row per employee.
-- =====================================================================

CREATE OR REPLACE VIEW vw_dim_employee AS
SELECT e.employee_id,
       e.first_name,
       e.last_name,
       e.first_name || ' ' || e.last_name AS employee_name,

       e.department_id,
       d.department_name,
       d.division,
       e.job_role,
       e.job_level,
       e.business_travel,

       e.hire_date,
       t.termination_date,
       e.current_status,
       t.attrition_flag,
       t.tenure_years,

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
       END AS tenure_cohort_sort,

       e.gender,
       e.age,
       e.marital_status,
       e.education_level,
       e.distance_from_home,

       e.manager_id,
       mgr.first_name || ' ' || mgr.last_name AS manager_name,

       cp.monthly_income,
       cp.income_pct_rank_in_role,
       cp.income_quartile_in_role,
       cp.income_quartile_label,
       cp.pct_vs_role_average,
       cp.stock_option_level,
       cp.salary_hike_pct,

       lr.latest_review_date,
       lr.performance_rating,
       lr.job_satisfaction,
       lr.environment_satisfaction,
       lr.work_life_balance,
       lr.overtime_flag,

       ae.voluntary_flag

FROM employees e
JOIN departments d                       ON d.department_id = e.department_id
JOIN vw_employee_tenure t                ON t.employee_id   = e.employee_id
LEFT JOIN employees mgr                  ON mgr.employee_id = e.manager_id
LEFT JOIN vw_compensation_percentile cp  ON cp.employee_id  = e.employee_id
LEFT JOIN vw_employee_latest_review lr   ON lr.employee_id  = e.employee_id
LEFT JOIN attrition_events ae            ON ae.employee_id  = e.employee_id;
