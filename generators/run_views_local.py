"""
Local validation harness.

Loads the generated CSVs into an in-process DuckDB database, creates every
view in sql/views/ from the exact same .sql files that ship to Postgres, and
prints the results. DuckDB speaks the same window-function / CTE / FILTER
dialect these views are written in, so this catches SQL errors and produces
the real numbers quoted in docs/sql_findings.md without needing a Postgres
server running.

    python generators/run_views_local.py           # run all views
    python generators/run_views_local.py --json    # emit results as JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
VIEWS_DIR = ROOT / "sql" / "views"

TABLES = [
    "departments",
    "employees",
    "compensation_history",
    "performance_reviews",
    "attrition_events",
    "dim_date",
]

DATE_COLS = {
    "employees": ["hire_date"],
    "compensation_history": ["effective_date"],
    "performance_reviews": ["review_date"],
    "attrition_events": ["termination_date"],
    "dim_date": ["date_key"],
}


def build(con: duckdb.DuckDBPyConnection) -> None:
    for t in TABLES:
        df = pd.read_csv(PROCESSED / (t + ".csv"), parse_dates=DATE_COLS.get(t, []))
        for c in DATE_COLS.get(t, []):
            df[c] = df[c].dt.date
        con.register("df_" + t, df)
        con.execute("CREATE OR REPLACE TABLE " + t + " AS SELECT * FROM df_" + t)

    # risk scores are written by the model; create the table empty so
    # vw_attrition_risk_watchlist compiles even before a scoring run
    scores = PROCESSED / "attrition_risk_scores.csv"
    if scores.exists():
        df = pd.read_csv(scores, parse_dates=["scored_date"])
        df["scored_date"] = df["scored_date"].dt.date
        con.register("df_scores", df)
        con.execute("CREATE OR REPLACE TABLE attrition_risk_scores AS SELECT * FROM df_scores")
    else:
        con.execute(
            "CREATE OR REPLACE TABLE attrition_risk_scores ("
            "employee_id INTEGER, scored_date DATE, risk_score DOUBLE,"
            "risk_tier VARCHAR, model_name VARCHAR)"
        )

    for path in sorted(VIEWS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        try:
            con.execute(sql)
        except Exception as exc:  # noqa: BLE001 - we want the file name in the message
            print("FAILED creating views from " + path.name, file=sys.stderr)
            print(str(exc), file=sys.stderr)
            raise
        print("  created views from " + path.name)


def show(con: duckdb.DuckDBPyConnection, title: str, sql: str) -> pd.DataFrame:
    df = con.execute(sql).fetchdf()
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)
    with pd.option_context("display.width", 200, "display.max_columns", 40):
        print(df.to_string(index=False))
    return df


def main() -> None:
    as_json = "--json" in sys.argv
    con = duckdb.connect()
    print("Loading tables and creating views...")
    build(con)

    results: dict[str, list] = {}

    results["company_headline"] = show(
        con,
        "COMPANY HEADLINE",
        """
        SELECT COUNT(*)                                              AS total_employees,
               SUM(CASE WHEN current_status='Active' THEN 1 ELSE 0 END)     AS active_headcount,
               SUM(CASE WHEN current_status='Terminated' THEN 1 ELSE 0 END) AS leavers,
               ROUND(AVG(CASE WHEN current_status='Terminated' THEN 1.0 ELSE 0.0 END),4)
                                                                     AS crude_attrition_rate
        FROM employees
        """,
    ).to_dict("records")

    results["v1_department_rolling"] = show(
        con,
        "V1  vw_attrition_by_department -- rolling 4q rate, latest quarter",
        """
        SELECT department_name, year_quarter, headcount_end, terminations,
               attrition_rate_qtr, rolling_4q_terminations, rolling_4q_attrition_rate
        FROM vw_attrition_by_department
        WHERE year_quarter = '2025 Q4'
        ORDER BY rolling_4q_attrition_rate DESC
        """,
    ).to_dict("records")

    results["v1_company_trend"] = show(
        con,
        "V1  company-wide quarterly trend",
        """
        SELECT year_quarter,
               SUM(terminations)                                   AS terminations,
               SUM(headcount_end)                                  AS headcount_end,
               ROUND(SUM(terminations)/NULLIF(SUM(avg_headcount),0),4) AS attrition_rate_qtr
        FROM vw_attrition_by_department
        GROUP BY year_quarter ORDER BY year_quarter
        """,
    ).to_dict("records")

    results["v2_tenure_cohort"] = show(
        con,
        "V2  vw_tenure_cohort_attrition",
        """
        SELECT tenure_cohort, employees, leavers, attrition_rate,
               share_of_all_leavers, lift_vs_company, avg_tenure_years
        FROM vw_tenure_cohort_attrition ORDER BY cohort_sort
        """,
    ).to_dict("records")

    results["v3_comp_quartile"] = show(
        con,
        "V3  vw_compensation_percentile -- attrition by in-role pay quartile",
        "SELECT * FROM vw_attrition_by_comp_quartile",
    ).to_dict("records")

    results["v3_bottom_decile"] = show(
        con,
        "V3  bottom vs top decile of in-role pay",
        """
        SELECT CASE WHEN income_pct_rank_in_role <= 0.10 THEN 'Bottom 10% in role'
                    WHEN income_pct_rank_in_role >= 0.90 THEN 'Top 10% in role'
                    ELSE 'Middle 80%' END AS band,
               COUNT(*) AS employees, SUM(attrition_flag) AS leavers,
               ROUND(AVG(attrition_flag*1.0),4) AS attrition_rate
        FROM vw_compensation_percentile
        GROUP BY 1 ORDER BY attrition_rate DESC
        """,
    ).to_dict("records")

    results["v4_span_band"] = show(
        con,
        "V4  vw_attrition_by_span_band",
        "SELECT * FROM vw_attrition_by_span_band",
    ).to_dict("records")

    results["v4_worst_managers"] = show(
        con,
        "V4  worst-performing managers with a usable sample",
        """
        SELECT manager_name, department_name, direct_reports, reports_lost,
               team_attrition_rate, span_band
        FROM vw_manager_span_attrition
        WHERE has_reliable_sample = 1
        ORDER BY team_attrition_rate DESC LIMIT 8
        """,
    ).to_dict("records")

    results["v5_overtime_satisfaction"] = show(
        con,
        "V5  vw_overtime_satisfaction_attrition -- THE HEADLINE",
        """
        SELECT overtime_flag, satisfaction_bucket, employees, leavers,
               attrition_rate, company_base_rate, lift_vs_base, has_reliable_sample
        FROM vw_overtime_satisfaction_attrition
        ORDER BY lift_vs_base DESC
        """,
    ).to_dict("records")

    results["v5_three_way"] = show(
        con,
        "V5  three-way: overtime x satisfaction x work-life balance",
        "SELECT * FROM vw_overtime_satisfaction_wlb_attrition",
    ).to_dict("records")

    results["v6_cohort_schedule"] = show(
        con,
        "V6  standard schedule -- company attrition rate by tenure cohort",
        "SELECT * FROM vw_company_cohort_rates",
    ).to_dict("records")

    results["v6_controlled"] = show(
        con,
        "V6  vw_department_attrition_controlled -- crude vs tenure-adjusted",
        """
        SELECT department_name, headcount, avg_tenure_years, observed_leavers,
               expected_leavers, crude_attrition_rate, standardised_attrition_ratio,
               tenure_adjusted_rate, mix_effect, verdict, has_reliable_sample
        FROM vw_department_attrition_controlled
        """,
    ).to_dict("records")

    n = con.execute("SELECT COUNT(*) FROM attrition_risk_scores").fetchone()[0]
    if n:
        results["v7_watchlist"] = show(
            con,
            "V7  vw_attrition_risk_watchlist -- top 15 active employees at risk",
            """
            SELECT employee_name, department_name, job_role, manager_name,
                   ROUND(risk_score,3) AS risk_score, risk_tier, risk_flag_count,
                   tenure_years, income_quartile_label, overtime_flag, job_satisfaction
            FROM vw_attrition_risk_watchlist
            WHERE model_name = 'logistic_regression'
            ORDER BY risk_score DESC LIMIT 15
            """,
        ).to_dict("records")
        results["v7_tiers"] = show(
            con,
            "V7  watchlist tier distribution",
            """
            SELECT model_name, risk_tier, COUNT(*) AS employees,
                   ROUND(AVG(risk_score),4) AS avg_score
            FROM vw_attrition_risk_watchlist
            GROUP BY model_name, risk_tier ORDER BY model_name, avg_score DESC
            """,
        ).to_dict("records")
    else:
        print("")
        print("(attrition_risk_scores is empty -- run notebooks/attrition_risk_model "
              "to populate the watchlist)")

    if as_json:
        out = ROOT / "docs" / "view_results.json"
        out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print("")
        print("wrote " + str(out))


if __name__ == "__main__":
    main()
