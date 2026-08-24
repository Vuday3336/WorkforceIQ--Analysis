-- =====================================================================
-- vw_attrition_by_department
--
-- BUSINESS QUESTION
--   "Which departments are losing people fastest, and is it getting worse
--    or is one bad quarter distorting the picture?"
--
-- WHY IT IS BUILT THIS WAY
--   A single quarter of terminations in a 52-person department is noise --
--   two extra leavers swings the rate by 4 points. So the view returns both
--   the point-in-time quarterly rate AND a rolling 4-quarter rate computed
--   with a window frame, which is the number leadership should actually
--   read. The rolling figure is annualised by construction (four quarters
--   of leavers over the average headcount across those quarters).
--
--   Headcount is measured at both ends of the quarter and averaged, rather
--   than using a single snapshot. Using end-of-period headcount alone
--   understates the denominator in a shrinking team and therefore
--   overstates its attrition rate -- exactly the teams under scrutiny.
--
-- GRAIN: one row per department per quarter.
-- =====================================================================

CREATE OR REPLACE VIEW vw_attrition_by_department AS
WITH quarters AS (
    SELECT DISTINCT
           year,
           quarter,
           year_quarter,
           MAKE_DATE(year, (quarter - 1) * 3 + 1, 1) AS quarter_start,
           CAST(MAKE_DATE(year, (quarter - 1) * 3 + 1, 1)
                + INTERVAL '3 months' - INTERVAL '1 day' AS DATE) AS quarter_end
    FROM dim_date
    WHERE year BETWEEN 2024 AND 2025
),

-- every department x every quarter, so quarters with zero leavers still
-- produce a row instead of silently dropping out of the trend line
dept_quarter AS (
    SELECT d.department_id,
           d.department_name,
           d.division,
           q.year,
           q.quarter,
           q.year_quarter,
           q.quarter_start,
           q.quarter_end
    FROM departments d
    CROSS JOIN quarters q
),

metrics AS (
    SELECT dq.department_id,
           dq.department_name,
           dq.division,
           dq.year,
           dq.quarter,
           dq.year_quarter,

           COUNT(*) FILTER (
               WHERE e.hire_date < dq.quarter_start
                 AND (a.termination_date IS NULL OR a.termination_date >= dq.quarter_start)
           ) AS headcount_start,

           COUNT(*) FILTER (
               WHERE e.hire_date <= dq.quarter_end
                 AND (a.termination_date IS NULL OR a.termination_date > dq.quarter_end)
           ) AS headcount_end,

           COUNT(*) FILTER (
               WHERE a.termination_date BETWEEN dq.quarter_start AND dq.quarter_end
           ) AS terminations

    FROM dept_quarter dq
    JOIN employees e
      ON e.department_id = dq.department_id
    LEFT JOIN attrition_events a
      ON a.employee_id = e.employee_id
    GROUP BY dq.department_id, dq.department_name, dq.division,
             dq.year, dq.quarter, dq.year_quarter
),

with_denominator AS (
    SELECT m.*,
           (m.headcount_start + m.headcount_end) / 2.0 AS avg_headcount
    FROM metrics m
)

SELECT department_id,
       department_name,
       division,
       year,
       quarter,
       year_quarter,
       headcount_start,
       headcount_end,
       ROUND(avg_headcount, 1) AS avg_headcount,
       terminations,

       ROUND(terminations / NULLIF(avg_headcount, 0), 4) AS attrition_rate_qtr,

       -- rolling 4-quarter window: current quarter plus the three before it
       SUM(terminations) OVER (
           PARTITION BY department_id
           ORDER BY year, quarter
           ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
       ) AS rolling_4q_terminations,

       ROUND(
           SUM(terminations) OVER (
               PARTITION BY department_id
               ORDER BY year, quarter
               ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
           )
           / NULLIF(AVG(avg_headcount) OVER (
               PARTITION BY department_id
               ORDER BY year, quarter
               ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
             ), 0),
       4) AS rolling_4q_attrition_rate,

       -- how many quarters of history back this rolling figure; the first
       -- three quarters of any series are partial and should not be read
       -- as annualised rates
       COUNT(*) OVER (
           PARTITION BY department_id
           ORDER BY year, quarter
           ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
       ) AS rolling_window_quarters

FROM with_denominator;
