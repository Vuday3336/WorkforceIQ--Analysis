"""
Feature assembly for the attrition-risk model.

Deliberately builds the modelling frame by querying the SAME SQL views the
Power BI report reads, rather than re-deriving features in pandas. If the
definition of "pay percentile within role" changes, it changes in one place
(sql/views/03) and both the dashboard and the model move together. A model
whose features drift away from the BI layer's definitions is how you end up
explaining to a stakeholder why two numbers on the same screen disagree.

Reads the CSVs in data/processed/ through DuckDB so this runs with no
database server; the identical query text runs against Postgres unchanged.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
VIEWS_DIR = ROOT / "sql" / "views"

SNAPSHOT_DATE = "2025-12-31"

_TABLES = {
    "departments": [],
    "employees": ["hire_date"],
    "compensation_history": ["effective_date"],
    "performance_reviews": ["review_date"],
    "attrition_events": ["termination_date"],
    "dim_date": ["date_key"],
}

FEATURE_SQL = """
SELECT
    e.employee_id,
    t.attrition_flag,
    e.current_status,

    -- tenure and demographics
    t.tenure_years,
    -- attrition falls off steeply in the first three years and then
    -- flattens, so a raw linear tenure term badly underfits it. The log
    -- form lets the linear model represent that curve; the forest finds
    -- it on its own.
    LN(1 + t.tenure_years) AS tenure_years_log,
    e.age,
    e.job_level,
    e.distance_from_home,

    -- compensation position (from view 03)
    cp.monthly_income,
    cp.income_pct_rank_in_role,
    cp.pct_vs_role_average,
    cp.salary_hike_pct,
    cp.stock_option_level,

    -- most recent review signal (from view 05)
    lr.job_satisfaction,
    lr.environment_satisfaction,
    lr.work_life_balance,
    lr.performance_rating,
    CASE WHEN lr.overtime_flag = 'Yes' THEN 1 ELSE 0 END AS overtime,

    -- manager context (from view 04)
    COALESCE(ms.direct_reports, 0) AS manager_span,

    -- categoricals
    d.department_name,
    e.job_role,
    e.marital_status,
    e.education_level,
    e.business_travel,
    e.gender

FROM employees e
JOIN departments d                       ON d.department_id = e.department_id
JOIN vw_employee_tenure t                ON t.employee_id   = e.employee_id
LEFT JOIN vw_compensation_percentile cp  ON cp.employee_id  = e.employee_id
LEFT JOIN vw_employee_latest_review lr   ON lr.employee_id  = e.employee_id
LEFT JOIN vw_manager_span_attrition ms   ON ms.manager_id   = e.manager_id
"""


def connect() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with every table loaded and every view created."""
    con = duckdb.connect()
    for table, date_cols in _TABLES.items():
        df = pd.read_csv(PROCESSED / (table + ".csv"), parse_dates=date_cols)
        for c in date_cols:
            df[c] = df[c].dt.date
        con.register("df_" + table, df)
        con.execute("CREATE OR REPLACE TABLE " + table + " AS SELECT * FROM df_" + table)

    # Model output, if a scoring run has already happened. Created empty
    # otherwise so the views still compile -- the training job needs the views
    # in order to build features, so on a cold start this table cannot yet
    # exist. Nothing in FEATURE_SQL reads it, so there is no circularity.
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
        con.execute(path.read_text(encoding="utf-8"))
    return con


def load_features() -> pd.DataFrame:
    con = connect()
    df = con.execute(FEATURE_SQL).fetchdf()
    con.close()
    return df


NUMERIC_FEATURES = [
    "tenure_years", "tenure_years_log", "age", "job_level", "distance_from_home",
    "monthly_income", "income_pct_rank_in_role", "pct_vs_role_average",
    "salary_hike_pct", "stock_option_level", "job_satisfaction",
    "environment_satisfaction", "work_life_balance", "performance_rating",
    "overtime", "manager_span",
]

CATEGORICAL_FEATURES = [
    "department_name", "job_role", "marital_status",
    "education_level", "business_travel", "gender",
]
