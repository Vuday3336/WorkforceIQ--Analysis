-- =====================================================================
-- vw_manager_span_attrition
--
-- BUSINESS QUESTION
--   "Do managers with larger teams lose more people?"
--   If span-of-control drives attrition, the fix is an org-design change
--   (split the team, add a lead) rather than a people-management one.
--
-- WHY IT IS BUILT THIS WAY
--   Two traps this view is written to avoid:
--
--   1. SMALL-TEAM NOISE. A manager with 3 reports who loses 1 shows a 33%
--      attrition rate and will top any naive ranking. The view therefore
--      emits direct_reports alongside the rate and a has_reliable_sample
--      flag, and the banded roll-up at the bottom -- which is the thing you
--      should actually draw conclusions from -- aggregates employees, not
--      averages of per-manager rates. Averaging rates would give the
--      3-person team the same weight as the 20-person team.
--
--   2. MANAGERS ARE EMPLOYEES TOO. The self-join has to exclude the
--      manager's own attrition from their team's rate, otherwise a manager
--      who resigned pollutes their own scorecard.
--
-- GRAIN: one row per manager (plus a span-band roll-up view).
-- =====================================================================

CREATE OR REPLACE VIEW vw_manager_span_attrition AS
WITH reports AS (
    SELECT e.manager_id,
           e.employee_id,
           t.attrition_flag,
           t.tenure_years
    FROM employees e
    JOIN vw_employee_tenure t ON t.employee_id = e.employee_id
    WHERE e.manager_id IS NOT NULL
      AND e.manager_id <> e.employee_id   -- belt and braces alongside the CHECK constraint
),

by_manager AS (
    SELECT r.manager_id,
           COUNT(*)                            AS direct_reports,
           SUM(r.attrition_flag)               AS reports_lost,
           AVG(r.attrition_flag * 1.0)         AS team_attrition_rate,
           AVG(r.tenure_years)                 AS team_avg_tenure
    FROM reports r
    GROUP BY r.manager_id
)

SELECT m.employee_id                            AS manager_id,
       m.first_name || ' ' || m.last_name       AS manager_name,
       d.department_name,
       m.job_role                               AS manager_role,
       m.job_level                              AS manager_level,
       m.current_status                         AS manager_status,

       bm.direct_reports,
       bm.reports_lost,
       ROUND(bm.team_attrition_rate, 4)         AS team_attrition_rate,
       ROUND(bm.team_avg_tenure, 2)             AS team_avg_tenure,

       CASE
           WHEN bm.direct_reports <  6 THEN '1-5 reports'
           WHEN bm.direct_reports < 11 THEN '6-10 reports'
           WHEN bm.direct_reports < 16 THEN '11-15 reports'
           ELSE '16+ reports'
       END AS span_band,
       CASE
           WHEN bm.direct_reports <  6 THEN 1
           WHEN bm.direct_reports < 11 THEN 2
           WHEN bm.direct_reports < 16 THEN 3
           ELSE 4
       END AS span_band_sort,

       -- below ~8 reports a single departure moves the rate by >12 points,
       -- so anything smaller is flagged rather than silently ranked
       CASE WHEN bm.direct_reports >= 8 THEN 1 ELSE 0 END AS has_reliable_sample,

       -- rank within department, worst first, restricted to usable samples
       RANK() OVER (
           PARTITION BY d.department_name
           ORDER BY CASE WHEN bm.direct_reports >= 8 THEN bm.team_attrition_rate END DESC NULLS LAST
       ) AS attrition_rank_in_dept

FROM by_manager bm
JOIN employees   m ON m.employee_id   = bm.manager_id
JOIN departments d ON d.department_id = m.department_id;


-- The roll-up to read. Aggregates PEOPLE across the band rather than
-- averaging per-manager rates, so team size is weighted correctly.
CREATE OR REPLACE VIEW vw_attrition_by_span_band AS
SELECT span_band,
       span_band_sort,
       COUNT(*)                        AS managers,
       SUM(direct_reports)             AS employees_covered,
       SUM(reports_lost)               AS leavers,
       ROUND(SUM(reports_lost) * 1.0
             / NULLIF(SUM(direct_reports), 0), 4) AS attrition_rate,
       ROUND(AVG(direct_reports), 1)   AS avg_span
FROM vw_manager_span_attrition
GROUP BY span_band, span_band_sort
ORDER BY span_band_sort;
