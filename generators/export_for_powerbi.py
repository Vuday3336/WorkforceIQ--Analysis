"""
Exports every table the Power BI model needs into data/powerbi/*.csv.

WHY THIS EXISTS
    The semantic model originally imported straight from PostgreSQL. That is
    the better story on a CV, but it makes the report unopenable by anyone who
    does not hold the database password -- and on Supabase specifically it also
    fails on TLS, because the pooler's certificate is not chained to a root in
    the Windows trust store.

    A portfolio report that a reviewer cannot open is worth nothing, so the
    shipped default is a file import: open the .pbip and the data is simply
    there. The PostgreSQL connection is kept as a documented one-parameter
    switch (see powerbi/REPORT_BUILD_GUIDE.md).

    Nothing analytical is lost. These CSVs are not hand-made extracts -- each
    one is the result of running the actual shipped .sql view, so the SQL layer
    still computes every number in the report.

    python generators/export_for_powerbi.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_store import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "powerbi"

# (Power BI table name, source table or view)
EXPORTS = [
    ("DimDate", "dim_date"),
    ("DimDepartment", "departments"),
    ("DimEmployee", "vw_dim_employee"),
    ("FactAttrition", "attrition_events"),
    ("FactCompensation", "compensation_history"),
    ("FactReview", "performance_reviews"),
    ("RiskScores", "attrition_risk_scores"),
    ("AttritionByDepartment", "vw_attrition_by_department"),
    ("TenureCohort", "vw_tenure_cohort_attrition"),
    ("CompQuartile", "vw_attrition_by_comp_quartile"),
    ("OvertimeSatisfaction", "vw_overtime_satisfaction_attrition"),
    ("SpanBand", "vw_attrition_by_span_band"),
    ("DepartmentControlled", "vw_department_attrition_controlled"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()
    total = 0
    for name, source in EXPORTS:
        df = con.execute("SELECT * FROM " + source).fetchdf()
        for c in df.columns:
            dtype = str(df[c].dtype)
            # Power BI's Csv.Document is happiest with plain ISO dates
            if "date" in dtype or "datetime" in dtype:
                df[c] = df[c].astype(str).str.slice(0, 10)
            # M's `type logical` conversion wants lowercase true/false.
            # pandas writes True/False, which is a coin flip depending on
            # locale settings, and DAX measures use is_month_end as a real
            # boolean inside FILTER() -- so a failed conversion would break
            # every rolling and YTD measure rather than just look wrong.
            elif dtype == "bool":
                df[c] = df[c].map({True: "true", False: "false"})
        df.to_csv(OUT / (name + ".csv"), index=False, encoding="utf-8")
        total += len(df)
        print("  %-24s %7d rows  <- %s" % (name, len(df), source))
    con.close()
    print("\n  %d tables, %s rows -> %s" % (len(EXPORTS), format(total, ","),
                                            OUT.relative_to(ROOT).as_posix()))


if __name__ == "__main__":
    main()
