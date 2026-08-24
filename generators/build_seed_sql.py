"""
Emits sql/seed_data.sql -- a plain, dependency-free INSERT script.

Anyone with psql and no Python can build the whole database with:

    psql "$DATABASE_URL" -f sql/schema.sql
    psql "$DATABASE_URL" -f sql/seed_data.sql
    psql "$DATABASE_URL" -f sql/views/*.sql

Insert order respects the foreign keys. employees is loaded in two passes --
rows first with manager_id NULL, then a single UPDATE to wire the hierarchy --
because the self-referencing FK cannot be satisfied by any single ordering of
a self-referencing tree.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "sql" / "seed_data.sql"

BATCH = 500


def lit(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return "NULL"
    if isinstance(v, (int,)) or (hasattr(v, "dtype") and "int" in str(getattr(v, "dtype", ""))):
        return str(int(v))
    if isinstance(v, float):
        return str(round(v, 6))
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    s = str(v)
    return "'" + s.replace("'", "''") + "'"


def emit(f, table: str, df: pd.DataFrame) -> None:
    cols = list(df.columns)
    f.write("\n-- " + table + ": " + format(len(df), ",") + " rows\n")
    records = df.to_dict("records")
    for start in range(0, len(records), BATCH):
        chunk = records[start : start + BATCH]
        f.write("INSERT INTO " + table + " (" + ", ".join(cols) + ") VALUES\n")
        lines = []
        for rec in chunk:
            lines.append("  (" + ", ".join(lit(rec[c]) for c in cols) + ")")
        f.write(",\n".join(lines))
        f.write(";\n")


def main() -> None:
    with OUT.open("w", encoding="utf-8") as f:
        f.write("-- =====================================================\n")
        f.write("-- WorkforceIQ seed data\n")
        f.write("-- GENERATED FILE -- do not edit by hand.\n")
        f.write("-- Regenerate with: python generators/build_seed_sql.py\n")
        f.write("-- Source: data/processed/*.csv (see generators/build_dataset.py)\n")
        f.write("-- =====================================================\n\n")
        f.write("BEGIN;\n\n")
        f.write("TRUNCATE attrition_risk_scores, attrition_events, performance_reviews,\n")
        f.write("         compensation_history, employees, departments, dim_date\n")
        f.write("    RESTART IDENTITY CASCADE;\n")

        emit(f, "departments", pd.read_csv(PROCESSED / "departments.csv"))

        # pass 1: employees without the self-FK
        emp = pd.read_csv(PROCESSED / "employees.csv")
        managers = emp[["employee_id", "manager_id"]].dropna().astype(int)
        emp_no_mgr = emp.drop(columns=["manager_id"])
        emit(f, "employees", emp_no_mgr)

        # pass 2: wire the hierarchy now that every row exists
        f.write("\n-- resolve the self-referencing manager hierarchy\n")
        f.write("UPDATE employees e SET manager_id = v.manager_id FROM (VALUES\n")
        f.write(",\n".join(
            "  (" + str(int(r.employee_id)) + ", " + str(int(r.manager_id)) + ")"
            for r in managers.itertuples()))
        f.write("\n) AS v(employee_id, manager_id)\n")
        f.write("WHERE e.employee_id = v.employee_id;\n")

        emit(f, "compensation_history", pd.read_csv(PROCESSED / "compensation_history.csv"))
        emit(f, "performance_reviews", pd.read_csv(PROCESSED / "performance_reviews.csv"))
        emit(f, "attrition_events", pd.read_csv(PROCESSED / "attrition_events.csv"))

        # dim_date is pure calendar arithmetic -- generate it in SQL rather
        # than shipping 4,383 literal rows
        f.write("\n-- dim_date: generated in-database, no literals needed\n")
        f.write("""INSERT INTO dim_date (date_key, year, quarter, month, month_name,
                      year_quarter, year_month, is_month_end)
SELECT d::DATE,
       EXTRACT(YEAR    FROM d)::SMALLINT,
       EXTRACT(QUARTER FROM d)::SMALLINT,
       EXTRACT(MONTH   FROM d)::SMALLINT,
       TRIM(TO_CHAR(d, 'Month')),
       EXTRACT(YEAR FROM d)::TEXT || ' Q' || EXTRACT(QUARTER FROM d)::TEXT,
       TO_CHAR(d, 'YYYY-MM'),
       (d::DATE = (DATE_TRUNC('month', d) + INTERVAL '1 month - 1 day')::DATE)
FROM GENERATE_SERIES(DATE '2015-01-01', DATE '2026-12-31', INTERVAL '1 day') AS d;
""")

        scores = PROCESSED / "attrition_risk_scores.csv"
        if scores.exists():
            emit(f, "attrition_risk_scores", pd.read_csv(scores))

        f.write("\nCOMMIT;\n")

    size_kb = OUT.stat().st_size / 1024
    print("wrote " + str(OUT) + "  (" + format(size_kb, ".0f") + " KB)")


if __name__ == "__main__":
    main()
