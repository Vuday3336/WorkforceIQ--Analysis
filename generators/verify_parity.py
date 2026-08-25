"""
Cross-engine parity test for the analytical view layer.

Runs the SAME queries against the live PostgreSQL database and against a local
DuckDB instance built from the CSVs, and asserts they return identical results.

WHY THIS EXISTS
    The views are developed and validated locally against DuckDB (fast, no
    server) but ship to Postgres, where Power BI reads them. That only works if
    the two engines actually agree, and twice they did not:

    1. NTILE(4) ORDER BY monthly_income alone. NTILE forces equal-sized
       buckets, so tied salaries straddling a quartile boundary MUST be split,
       and which tied row lands in which bucket was arbitrary. The engines
       disagreed by one employee on the Q3/Q4 boundary. Fixed with an
       employee_id tiebreaker.

    2. `::NUMERIC` with no precision. Postgres reads that as arbitrary
       precision; DuckDB defaults it to DECIMAL(18,3). ROUND(x::NUMERIC, 4)
       therefore truncated to three decimals on DuckDB only, and percentiles
       differed by up to 0.0005. Fixed with an explicit NUMERIC(12,8).

    Neither bug raised an error anywhere. Both would have shipped silently and
    made the dashboard disagree with the notebook. Hence a test.

    python generators/verify_parity.py

Exits non-zero on any mismatch, so it works as a CI gate.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg2  # noqa: E402

from feature_store import connect  # noqa: E402
from load_to_postgres import dsn  # noqa: E402

TOLERANCE = 1e-9

# Every view, at the grain that would expose a discrepancy. Row-level queries
# (v3_percentile, v7, v8) matter most: an aggregate can hide two offsetting
# per-row differences, which is exactly how the NTILE bug survived the first
# check.
CHECKS = {
    "v1_dept_quarter": """
        SELECT department_name, year_quarter, headcount_end, terminations,
               attrition_rate_qtr, rolling_4q_attrition_rate
        FROM vw_attrition_by_department
        ORDER BY department_name, year_quarter""",
    "v2_cohort": """
        SELECT tenure_cohort, employees, leavers, attrition_rate,
               share_of_all_leavers, lift_vs_company
        FROM vw_tenure_cohort_attrition ORDER BY tenure_cohort""",
    "v2_cohort_dept": """
        SELECT department_name, tenure_cohort, employees, leavers, attrition_rate
        FROM vw_tenure_cohort_by_department ORDER BY department_name, tenure_cohort""",
    "v3_percentile": """
        SELECT employee_id, monthly_income, income_pct_rank_in_role,
               income_quartile_in_role, pct_vs_role_average
        FROM vw_compensation_percentile ORDER BY employee_id""",
    "v3_quartile": """
        SELECT income_quartile_in_role, employees, leavers, attrition_rate,
               avg_monthly_income
        FROM vw_attrition_by_comp_quartile ORDER BY income_quartile_in_role""",
    "v4_manager": """
        SELECT manager_id, direct_reports, reports_lost, team_attrition_rate,
               span_band, has_reliable_sample
        FROM vw_manager_span_attrition ORDER BY manager_id""",
    "v4_span_band": """
        SELECT span_band, managers, employees_covered, leavers, attrition_rate
        FROM vw_attrition_by_span_band ORDER BY span_band""",
    "v5_ot_sat": """
        SELECT overtime_flag, satisfaction_bucket, employees, leavers,
               attrition_rate, lift_vs_base
        FROM vw_overtime_satisfaction_attrition
        ORDER BY overtime_flag, satisfaction_bucket""",
    "v5_threeway": """
        SELECT overtime_flag, satisfaction_flag, wlb_flag, employees, leavers,
               attrition_rate
        FROM vw_overtime_satisfaction_wlb_attrition ORDER BY 1, 2, 3""",
    "v6_controlled": """
        SELECT department_name, headcount, observed_leavers, expected_leavers,
               crude_attrition_rate, standardised_attrition_ratio,
               tenure_adjusted_rate, mix_effect, verdict
        FROM vw_department_attrition_controlled ORDER BY department_name""",
    "v6_schedule": """
        SELECT tenure_cohort, employees, leavers, company_cohort_rate
        FROM vw_company_cohort_rates ORDER BY tenure_cohort""",
    "v7_watchlist": """
        SELECT employee_id, risk_score, risk_tier, risk_flag_count,
               tenure_years, income_quartile_label
        FROM vw_attrition_risk_watchlist
        WHERE model_name = 'logistic_regression' ORDER BY employee_id""",
    "v8_dim_employee": """
        SELECT employee_id, tenure_years, tenure_cohort, income_quartile_in_role,
               income_pct_rank_in_role, overtime_flag, job_satisfaction
        FROM vw_dim_employee ORDER BY employee_id""",
}


def compare(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """Column-wise compare, tolerant of int64-vs-float64 driver differences."""
    a.columns = [c.lower() for c in a.columns]
    b.columns = [c.lower() for c in b.columns]

    if list(a.columns) != list(b.columns):
        return ["column sets differ: %s vs %s" % (list(a.columns), list(b.columns))]
    if len(a) != len(b):
        return ["row counts differ: %d vs %d" % (len(a), len(b))]

    problems = []
    for col in a.columns:
        x, y = a[col].reset_index(drop=True), b[col].reset_index(drop=True)
        xn, yn = pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce")
        if xn.notna().all() and yn.notna().all():
            # numeric: the two drivers legitimately differ on int64 vs float64,
            # so compare by value, not by dtype or repr
            bad = ~np.isclose(xn.astype(float), yn.astype(float),
                              rtol=0, atol=TOLERANCE, equal_nan=True)
            if bad.any():
                i = int(np.argmax(bad))
                problems.append(
                    "%s: %d/%d rows differ (first at row %d: pg=%s duckdb=%s)"
                    % (col, int(bad.sum()), len(x), i, xn.iloc[i], yn.iloc[i]))
        else:
            bad = x.astype(str) != y.astype(str)
            if bad.any():
                i = int(np.argmax(bad.values))
                problems.append(
                    "%s: %d/%d rows differ (first at row %d: pg=%r duckdb=%r)"
                    % (col, int(bad.sum()), len(x), i, x.iloc[i], y.iloc[i]))
    return problems


def main() -> None:
    print("connecting ...")
    pg = psycopg2.connect(dsn())
    duck = connect()
    print("postgres + duckdb ready\n")

    failures = 0
    for name, query in CHECKS.items():
        a = pd.read_sql(query, pg)
        b = duck.execute(query).fetchdf()
        problems = compare(a, b)
        status = "ok" if not problems else "MISMATCH"
        print("  %-18s %6d rows   %s" % (name, len(a), status))
        for p in problems:
            print("       - " + p)
        failures += bool(problems)

    pg.close()
    print()
    print("=" * 66)
    if failures:
        print("PARITY FAILED: %d of %d checks differ" % (failures, len(CHECKS)))
        print("=" * 66)
        sys.exit(1)
    print("PARITY OK: all %d checks identical on PostgreSQL and DuckDB" % len(CHECKS))
    print("=" * 66)


if __name__ == "__main__":
    main()
