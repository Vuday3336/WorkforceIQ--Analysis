"""
Generates the Power BI project under powerbi/.

Emits a PBIP + TMDL semantic model: the tables, the relationships and every
DAX measure, as plain text. Column names and data types are introspected from
the actual SQL views through DuckDB, so the semantic model cannot drift away
from the database schema -- change a view, re-run this, and the model follows.

    python generators/build_powerbi.py

NOTE ON .pbix
    A .pbix is a proprietary binary that only Power BI Desktop can write, and
    Desktop is Windows-only and was not available in the environment this repo
    was built in. PBIP/TMDL is the text-based project format Desktop opens
    natively (File > Open > WorkforceIQ.pbip) and is the format you actually
    want in git -- a .pbix is an opaque blob that cannot be diffed or reviewed.
    Open the .pbip, verify the connection, then File > Save As > .pbix if a
    binary is needed for sharing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_report_pages import build_sections  # noqa: E402
from feature_store import connect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PBI = ROOT / "powerbi"
MODEL = PBI / "WorkforceIQ.SemanticModel"
REPORT = PBI / "WorkforceIQ.Report"
DEF = MODEL / "definition"
TABLES = DEF / "tables"

T = "\t"

# "Measures" is a RESERVED table name in the Tabular object model -- Power BI
# Desktop refuses to open a model containing it with:
#     Unable to open document: Unsupported Table name "Measures" ...
# The leading underscore is the conventional workaround, and has the useful
# side effect of sorting the table to the top of the field list. TMDL needs the
# name single-quoted everywhere because of that leading underscore.
MEASURES_TABLE = "_Measures"
MEASURES_TABLE_Q = "'_Measures'"

# DuckDB type -> TMDL dataType.
#
# DECIMAL/NUMERIC must be here. Anything missing used to fall through to
# "string", and SQL ROUND()/NUMERIC casts produce DECIMAL constantly -- so
# DimEmployee[tenure_years] and [income_pct_rank_in_role] were imported as
# TEXT. Nothing errored at load; the failures appeared later and looked
# unrelated: AVERAGE() over text broke the Avg Tenure card, and MAX() over
# text plus a text-vs-number comparison in the rule-based risk measure broke
# the whole watchlist table with a misleading "capacity or license issue".
#
# The unknown-type fallback below now raises instead of guessing, so a new
# column type fails the build rather than shipping a silently wrong model.
TYPE_MAP = {
    "BIGINT": "int64", "INTEGER": "int64", "SMALLINT": "int64", "HUGEINT": "int64",
    "TINYINT": "int64", "UBIGINT": "int64", "UINTEGER": "int64",
    "DOUBLE": "double", "FLOAT": "double", "REAL": "double",
    "DECIMAL": "double", "NUMERIC": "double",
    "VARCHAR": "string", "TEXT": "string", "CHAR": "string",
    "DATE": "dateTime", "TIMESTAMP": "dateTime", "TIMESTAMP_NS": "dateTime",
    "BOOLEAN": "boolean", "BOOL": "boolean",
}

# Columns whose display order must come from a numeric companion rather than
# alphabetical text. Without these, "0-1 yr" sorts next to "10 yr+" and the
# cohort charts read in a nonsense order.
#   (table, column) -> sort-by column
SORT_BY = {
    ("TenureCohort", "tenure_cohort"): "cohort_sort",
    ("DimEmployee", "tenure_cohort"): "tenure_cohort_sort",
    ("SpanBand", "span_band"): "span_band_sort",
    ("OvertimeSatisfaction", "satisfaction_bucket"): "satisfaction_sort",
    ("CompQuartile", "income_quartile_label"): "income_quartile_in_role",
    ("DimEmployee", "income_quartile_label"): "income_quartile_in_role",
}

# (tmdl table name, source view/table, description)
MODEL_TABLES = [
    ("DimDate", "dim_date",
     "Contiguous 2015-2026 calendar. Already marked as the date table "
     "(dataCategory: Time + isKey on date_key), which is what SAMEPERIODLASTYEAR "
     "and DATESINPERIOD require. Do not clear that marking."),
    ("DimDepartment", "departments",
     "Six Everline Corp departments rolled up to division."),
    ("DimEmployee", "vw_dim_employee",
     "Conformed wide employee dimension. Pay position and satisfaction are "
     "point-in-time correct: as at exit for leavers, as at snapshot for actives."),
    ("FactAttrition", "attrition_events",
     "One row per departure. Joined to DimDate on termination_date - this is "
     "the relationship every time-intelligence measure travels along."),
    ("FactCompensation", "compensation_history",
     "Effective-dated salary history (SCD Type 2 pattern)."),
    ("FactReview", "performance_reviews",
     "One row per employee per review cycle."),
    ("RiskScores", "attrition_risk_scores",
     "Model output. Every scoring run is retained so drift stays observable, "
     "so filter to the latest scored_date when building visuals."),
    ("AttritionByDepartment", "vw_attrition_by_department",
     "Pre-aggregated quarterly + rolling 4-quarter departmental rates."),
    ("TenureCohort", "vw_tenure_cohort_attrition",
     "Attrition by tenure cohort with lift against the company base rate."),
    ("CompQuartile", "vw_attrition_by_comp_quartile",
     "Attrition by in-role pay quartile."),
    ("OvertimeSatisfaction", "vw_overtime_satisfaction_attrition",
     "The headline cross-segment: overtime x satisfaction, with lift."),
    ("SpanBand", "vw_attrition_by_span_band",
     "Attrition by manager span-of-control band."),
    ("DepartmentControlled", "vw_department_attrition_controlled",
     "Crude vs tenure-adjusted departmental attrition (indirect standardisation)."),
]

# Measures live on a dedicated empty table so they are not buried inside a
# data table in the field list -- standard practice on any model with more
# than a handful of measures.
MEASURES = [
    # ---- headline counts -------------------------------------------------
    ("Total Headcount", "COUNTROWS ( DimEmployee )", "0", "Headline",
     "Every employee in scope, active or departed. The denominator of the crude rate."),
    ("Active Headcount",
     'CALCULATE ( COUNTROWS ( DimEmployee ), DimEmployee[current_status] = "Active" )',
     "0", "Headline", "Employees still with Everline at the snapshot date."),
    ("Terminated Count", "COUNTROWS ( FactAttrition )", "0", "Headline",
     "Departures. Filtered by DimDate through termination_date, so this "
     "responds to any date slicer."),

    # ---- rates -----------------------------------------------------------
    ("Attrition Rate", "DIVIDE ( [Terminated Count], [Total Headcount] )", "0.0%", "Rates",
     "The crude rate. Correct for an all-time view; use Attrition Rate (Period) "
     "inside a date filter, because Total Headcount is not point-in-time."),

    ("Headcount (EOP)", """
VAR AsOf = MAX ( DimDate[date_key] )
RETURN
    CALCULATE (
        COUNTROWS ( DimEmployee ),
        FILTER (
            ALL ( DimEmployee ),
            DimEmployee[hire_date] <= AsOf
                && ( ISBLANK ( DimEmployee[termination_date] )
                     || DimEmployee[termination_date] > AsOf )
        )
    )
""", "0", "Rates",
     "Point-in-time headcount at the end of the filtered period: hired on or "
     "before, and either still here or terminated after. ALL() is required - "
     "without it the employee filter context from the date relationship would "
     "restrict this to people who already left."),

    ("Avg Headcount (Period)", """
AVERAGEX (
    FILTER ( VALUES ( DimDate[date_key] ), DimDate[is_month_end] ),
    [Headcount (EOP)]
)
""", "0", "Rates",
     "Mean of month-end headcounts across the period. Averaging the endpoints "
     "of a shrinking team understates the denominator and overstates its rate."),

    ("Attrition Rate (Period)",
     "DIVIDE ( [Terminated Count], [Avg Headcount (Period)] )", "0.0%", "Rates",
     "The rate to use in any date-sliced visual."),

    # ---- time intelligence ----------------------------------------------
    ("Attrition Rate LY",
     "CALCULATE ( [Attrition Rate (Period)], SAMEPERIODLASTYEAR ( DimDate[date_key] ) )",
     "0.0%", "Time Intelligence",
     "Same period, previous year. Requires DimDate to be marked as the date table."),

    ("YoY Attrition Change",
     "[Attrition Rate (Period)] - [Attrition Rate LY]", "+0.0%;-0.0%;0.0%",
     "Time Intelligence",
     "Percentage-POINT change year over year, not a percentage change. "
     "16% -> 18% reads as +2.0pp."),

    ("YoY Attrition Change %", """
DIVIDE (
    [Attrition Rate (Period)] - [Attrition Rate LY],
    [Attrition Rate LY]
)
""", "+0.0%;-0.0%;0.0%", "Time Intelligence",
     "Relative change, for the KPI card subtitle."),

    ("YTD Terminations",
     "TOTALYTD ( [Terminated Count], DimDate[date_key] )", "0", "Time Intelligence",
     "Year-to-date departures."),

    ("YTD Attrition Rate", """
VAR AsOf = MAX ( DimDate[date_key] )
VAR YtdDates = DATESYTD ( DimDate[date_key] )
VAR Terms = CALCULATE ( [Terminated Count], YtdDates )
VAR AvgHead =
    AVERAGEX ( FILTER ( YtdDates, DimDate[is_month_end] ), [Headcount (EOP)] )
RETURN
    DIVIDE ( Terms, AvgHead )
""", "0.0%", "Time Intelligence",
     "Year-to-date rate against the year-to-date average headcount."),

    ("Rolling 12-Month Attrition Rate", """
VAR AsOf = MAX ( DimDate[date_key] )
VAR Window = DATESINPERIOD ( DimDate[date_key], AsOf, -12, MONTH )
VAR Terms = CALCULATE ( [Terminated Count], Window )
VAR AvgHead =
    AVERAGEX ( FILTER ( Window, DimDate[is_month_end] ), [Headcount (EOP)] )
RETURN
    DIVIDE ( Terms, AvgHead )
""", "0.0%", "Time Intelligence",
     "The trend number leadership should actually read. A single month in a "
     "50-person department swings on one resignation; the 12-month window "
     "smooths that without hiding a genuine shift."),

    # ---- risk ------------------------------------------------------------
    ("High Risk Employees", """
CALCULATE (
    DISTINCTCOUNT ( RiskScores[employee_id] ),
    RiskScores[risk_tier] = "High",
    RiskScores[model_name] = "logistic_regression"
)
""", "0", "Risk", "Size of the current High tier watchlist."),

    ("Avg Risk Score", """
CALCULATE (
    AVERAGE ( RiskScores[risk_score] ),
    RiskScores[model_name] = "logistic_regression"
)
""", "0.000", "Risk", "Mean modelled probability across the filtered population."),

    ("Flight Risk Score (Rule-Based)", """
VAR Overtime  = IF ( SELECTEDVALUE ( DimEmployee[overtime_flag] ) = "Yes", 2.0, 0 )
VAR LowSat    = IF ( SELECTEDVALUE ( DimEmployee[job_satisfaction] ) <= 2, 1.5, 0 )
VAR PoorWLB   = IF ( SELECTEDVALUE ( DimEmployee[work_life_balance] ) <= 2, 1.0, 0 )
VAR LowPay    = IF ( SELECTEDVALUE ( DimEmployee[income_pct_rank_in_role] ) <= 0.25, 1.5, 0 )
VAR RiskyTenure =
    VAR Tenure = SELECTEDVALUE ( DimEmployee[tenure_years] )
    RETURN IF ( Tenure >= 1 && Tenure <= 3, 1.0, 0 )
VAR LongCommute = IF ( SELECTEDVALUE ( DimEmployee[distance_from_home] ) >= 20, 0.5, 0 )
RETURN
    DIVIDE (
        Overtime + LowSat + PoorWLB + LowPay + RiskyTenure + LongCommute,
        7.5
    )
""", "0.00", "Risk",
     "The explainable counterpart to the model score: a transparent weighted "
     "checklist, shown beside the trained model so the two can be compared. "
     "Weights come from the SQL findings - overtime is heaviest because it is "
     "the strongest driver. Correlation with the model is only r=0.47, and "
     "that gap is the argument for the model."),

    ("Risk Score Delta (Model - Rule)",
     "[Avg Risk Score] - [Flight Risk Score (Rule-Based)]", "+0.00;-0.00;0.00",
     "Risk",
     "Where the model and the human heuristic disagree - the most interesting "
     "rows on the Watchlist page."),

    # ---- segment -----------------------------------------------------------
    ("Overtime %", """
DIVIDE (
    CALCULATE ( COUNTROWS ( DimEmployee ), DimEmployee[overtime_flag] = "Yes" ),
    COUNTROWS ( DimEmployee )
)
""", "0.0%", "Segments", "Share of the filtered population flagged for overtime."),

    ("Low Satisfaction %", """
DIVIDE (
    CALCULATE ( COUNTROWS ( DimEmployee ), DimEmployee[job_satisfaction] <= 2 ),
    COUNTROWS ( DimEmployee )
)
""", "0.0%", "Segments", "Share scoring 1-2 out of 4 on job satisfaction."),

    ("Avg Tenure (Years)", "AVERAGE ( DimEmployee[tenure_years] )", "0.0", "Segments",
     "At termination for leavers, at snapshot for active employees."),

    ("Avg Monthly Income", "AVERAGE ( DimEmployee[monthly_income] )", "#,0", "Segments",
     "Point-in-time salary from compensation_history."),

    ("Attrition Lift vs Company", """
VAR Segment = [Attrition Rate]
VAR Company = CALCULATE ( [Attrition Rate], ALL ( DimEmployee ) )
RETURN
    DIVIDE ( Segment, Company )
""", "0.00x", "Segments",
     "How many times the company base rate the current selection runs at. "
     "This is the column that turns a table into a decision."),
]

RELATIONSHIPS = [
    ("DimDepartment", "department_id", "DimEmployee", "department_id"),
    ("DimEmployee", "employee_id", "FactAttrition", "employee_id"),
    ("DimEmployee", "employee_id", "FactCompensation", "employee_id"),
    ("DimEmployee", "employee_id", "FactReview", "employee_id"),
    ("DimEmployee", "employee_id", "RiskScores", "employee_id"),
    ("DimDate", "date_key", "FactAttrition", "termination_date"),
]


def guid(seed: str) -> str:
    import hashlib
    h = hashlib.md5(seed.encode()).hexdigest()
    return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32]


# M type for each TMDL dataType
M_TYPE = {
    "int64": "Int64.Type",
    "double": "type number",
    "string": "type text",
    "dateTime": "type date",
    "boolean": "type logical",
}


def m_partition(table: str, source: str, cols) -> str:
    """
    File import from data/powerbi/<table>.csv.

    Deliberately NOT a PostgreSQL connection by default. Importing from
    Postgres is the better line on a CV, but it makes the report unopenable
    without the database password, and on Supabase it additionally fails TLS
    because the pooler certificate is not chained to a root Windows trusts.
    A portfolio report a reviewer cannot open is worth nothing.

    No analytical fidelity is lost: each CSV is the output of running the
    actual shipped .sql view (generators/export_for_powerbi.py), so the SQL
    layer still computes every number. To switch back to a live connection,
    see powerbi/REPORT_BUILD_GUIDE.md -- the Postgres M is one edit away.
    """
    transforms = ", ".join(
        "{" + '"' + c + '"' + ", " + M_TYPE.get(t, "type text") + "}"
        for c, t in cols)

    # The full path is INLINED rather than built from a shared DataFolder
    # parameter. Every table referencing one parameter was the only cross-query
    # dependency in the model, and it made Power Query fail all 13 loads with
    # "A cyclic reference was encountered during evaluation". Inlining makes
    # each query completely self-contained, so a dependency cycle is
    # structurally impossible.
    #
    # Note single backslashes: M has no backslash escape, so "\\" would be a
    # literal double separator rather than an escaped one.
    #
    # Cost of inlining: moving the repo means re-running this generator. That
    # is one command, and is documented in REPORT_BUILD_GUIDE.md.
    csv_path = str(ROOT / "data" / "powerbi" / (table + ".csv"))
    return (
        T + "partition " + table + " = m\n"
        + T * 2 + "mode: import\n"
        + T * 2 + "source =\n"
        + T * 4 + "let\n"
        + T * 4 + '    Source = Csv.Document(\n'
        + T * 4 + '        File.Contents("' + csv_path + '"),\n'
        + T * 4 + '        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),\n'
        + T * 4 + '    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),\n'
        + T * 4 + "    Typed = Table.TransformColumnTypes(Headers, {" + transforms + "})\n"
        + T * 4 + "in\n"
        + T * 4 + "    Typed\n"
    )


# Marking the date table is otherwise a manual step in Desktop
# (Table tools > Mark as date table), and skipping it makes every
# time-intelligence measure return wrong numbers SILENTLY -- no error, just
# incorrect values. Serialising it here means it cannot be forgotten:
# `dataCategory: Time` on the table plus `isKey` on the date column is exactly
# what Desktop writes when you mark it by hand.
DATE_TABLE = "DimDate"
DATE_KEY = "date_key"


def emit_table(name: str, source: str, description: str, cols) -> str:
    out = []
    typed_cols = []
    for line in description.split("\n"):
        out.append("/// " + line)
    out.append("table " + name)
    if name == DATE_TABLE:
        out.append(T + "dataCategory: Time")
    out.append(T + "lineageTag: " + guid("table:" + name))
    out.append("")
    col_names = {c for c, _ in cols}
    for col_name, col_type in cols:
        base = col_type.upper().split("(")[0]
        if base not in TYPE_MAP:
            # fail the build rather than silently importing it as text
            raise ValueError(
                "unmapped column type %r on %s.%s -- add it to TYPE_MAP"
                % (col_type, name, col_name))
        dt = TYPE_MAP[base]
        typed_cols.append((col_name, dt))
        out.append(T + "column " + col_name)
        if name == DATE_TABLE and col_name == DATE_KEY:
            out.append(T * 2 + "isKey")
        out.append(T * 2 + "dataType: " + dt)
        sort_col = SORT_BY.get((name, col_name))
        if sort_col and sort_col in col_names:
            out.append(T * 2 + "sortByColumn: " + sort_col)
        if dt == "dateTime":
            out.append(T * 2 + "formatString: yyyy-mm-dd")
        out.append(T * 2 + "lineageTag: " + guid(name + "." + col_name))
        out.append(T * 2 + "summarizeBy: none")
        out.append(T * 2 + "sourceColumn: " + col_name)
        out.append("")
        out.append(T * 2 + "annotation SummarizationSetBy = Automatic")
        out.append("")
    out.append(m_partition(name, source, typed_cols))
    out.append(T + "annotation PBI_ResultType = Table")
    out.append("")
    return "\n".join(out)


def emit_measures_table() -> str:
    out = [
        "/// Measure-only table. Holds every DAX measure so they sit at the top",
        "/// of the field list instead of being buried inside a data table.",
        "table " + MEASURES_TABLE_Q,
        T + "lineageTag: " + guid("table:" + MEASURES_TABLE),
        "",
        # a hidden placeholder column: a table needs at least one column
        T + "column _placeholder",
        T * 2 + "isHidden",
        T * 2 + "dataType: int64",
        T * 2 + "lineageTag: " + guid(MEASURES_TABLE + "._placeholder"),
        T * 2 + "summarizeBy: none",
        T * 2 + "sourceColumn: _placeholder",
        "",
        T * 2 + "annotation SummarizationSetBy = Automatic",
        "",
    ]
    for name, expr, fmt, folder, doc in MEASURES:
        for line in doc.split("\n"):
            out.append(T + "/// " + line)
        expr = expr.strip()
        if "\n" in expr:
            out.append(T + "measure '" + name + "' =")
            for line in expr.split("\n"):
                out.append(T * 3 + line)
        else:
            out.append(T + "measure '" + name + "' = " + expr)
        out.append(T * 2 + "formatString: " + fmt)
        out.append(T * 2 + "displayFolder: " + folder)
        out.append(T * 2 + "lineageTag: " + guid("measure:" + name))
        out.append("")
    out.append(T + "partition " + MEASURES_TABLE_Q + " = m")
    out.append(T * 2 + "mode: import")
    out.append(T * 2 + "source =")
    out.append(T * 4 + "let")
    out.append(T * 4 + '    Source = Table.FromRows({{1}}, {"_placeholder"})')
    out.append(T * 4 + "in")
    out.append(T * 4 + "    Source")
    out.append("")
    out.append(T + "annotation PBI_ResultType = Table")
    out.append("")
    return "\n".join(out)


def main() -> None:
    for d in (TABLES,):
        d.mkdir(parents=True, exist_ok=True)

    con = connect()

    # ---------------------------------------------------------------- tables
    order = []
    for name, source, description in MODEL_TABLES:
        info = con.execute("DESCRIBE SELECT * FROM " + source).fetchall()
        cols = [(r[0], r[1]) for r in info]
        (TABLES / (name + ".tmdl")).write_text(
            emit_table(name, source, description, cols), encoding="utf-8")
        order.append(name)
        print("  " + name + ".tmdl  (" + str(len(cols)) + " columns from " + source + ")")

    (TABLES / (MEASURES_TABLE + ".tmdl")).write_text(emit_measures_table(), encoding="utf-8")
    order.insert(0, MEASURES_TABLE)
    print("  Measures.tmdl  (" + str(len(MEASURES)) + " DAX measures)")

    # ---------------------------------------------------------------- model
    model = [
        "model Model",
        T + "culture: en-US",
        T + "defaultPowerBIDataSourceVersion: powerBI_V3",
        T + "discourageImplicitMeasures",
        T + "sourceQueryCulture: en-US",
        "",
        T + 'annotation PBI_QueryOrder = ["' + '","'.join(order) + '"]',
        "",
        T + "annotation PBI_ProTooling = [\"DaxQueryView\",\"TMDL\"]",
        "",
    ]
    for name in order:
        quoted = MEASURES_TABLE_Q if name == MEASURES_TABLE else name
        model.append("ref table " + quoted)
    model.append("")
    model.append("ref cultureInfo en-US")
    model.append("")
    (DEF / "model.tmdl").write_text("\n".join(model), encoding="utf-8")

    # ---------------------------------------------------------------- relationships
    rel = []
    for from_t, from_c, to_t, to_c in RELATIONSHIPS:
        # TMDL: fromColumn is the MANY side, toColumn is the ONE side
        rel.append("relationship " + guid(from_t + from_c + to_t + to_c))
        rel.append(T + "fromColumn: " + to_t + "." + to_c)
        rel.append(T + "toColumn: " + from_t + "." + from_c)
        rel.append("")
    (DEF / "relationships.tmdl").write_text("\n".join(rel), encoding="utf-8")

    # ---------------------------------------------------------------- expressions
    # No expressions.tmdl at all.
    #
    # It previously declared DataFolder / ServerName / DatabaseName as
    # parameter queries (meta [IsParameterQuery=true]). Every table's M
    # referenced DataFolder, which was the model's only cross-query
    # dependency, and Power Query rejected the whole load with "A cyclic
    # reference was encountered during evaluation". Table paths are now
    # inlined, so no parameters are needed and none are emitted -- there is
    # nothing left for the dependency resolver to trip over.
    #
    # The live-PostgreSQL variant is documented in REPORT_BUILD_GUIDE.md.
    stale = DEF / "expressions.tmdl"
    if stale.exists():
        stale.unlink()


    (DEF / "database.tmdl").write_text(
        # 1606, not 1567. Power BI Desktop upgrades the model to its own
        # compatibility level the first time it saves, and Tabular refuses a
        # DOWNGRADE outright:
        #     Tabular databases do not support CompatibilityLevel downgrade.
        #     Current: '1606'. Requested: '1567'.
        # Regenerating this file with a lower number therefore breaks a project
        # that Desktop has already opened. Upgrades are allowed, so a newer
        # Desktop can still raise it; older ones prompt to upgrade.
        "database\n" + T + "compatibilityLevel: 1606\n", encoding="utf-8")

    (DEF / "cultures").mkdir(exist_ok=True)
    (DEF / "cultures" / "en-US.tmdl").write_text(
        "cultureInfo en-US\n\n"
        + T + "linguisticMetadata =\n"
        + T * 3 + '{\n'
        + T * 3 + '  "Version": "1.0.0",\n'
        + T * 3 + '  "Language": "en-US"\n'
        + T * 3 + '}\n'
        + T * 2 + "contentType: json\n", encoding="utf-8")

    (MODEL / "definition.pbism").write_text(
        '{\n  "version": "4.2",\n  "settings": {}\n}\n', encoding="utf-8")

    (MODEL / ".platform").write_text(
        '{\n'
        '  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",\n'
        '  "metadata": {\n'
        '    "type": "SemanticModel",\n'
        '    "displayName": "WorkforceIQ"\n'
        '  },\n'
        '  "config": {\n'
        '    "version": "2.0",\n'
        '    "logicalId": "' + guid("logical:model") + '"\n'
        '  }\n'
        '}\n', encoding="utf-8")

    # NOTE ON THE $schema URL: Power BI Desktop validates this against
    #   ^https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.[0-9]+.[0-9]+/schema.json$
    # It is pbip/pbipProperties, NOT item/pbip/definitionProperties -- the
    # latter is the shape used by the per-item definition files inside each
    # artifact folder, and using it here fails to open with "Issues were found".
    (PBI / "WorkforceIQ.pbip").write_text(
        '{\n'
        '  "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",\n'
        '  "version": "1.0",\n'
        '  "artifacts": [\n'
        '    {\n'
        '      "report": {\n'
        '        "path": "WorkforceIQ.Report"\n'
        '      }\n'
        '    }\n'
        '  ],\n'
        '  "settings": {\n'
        '    "enableAutoRecovery": true\n'
        '  }\n'
        '}\n', encoding="utf-8")

    # ---------------------------------------------------------------- report
    # The .pbip above points at a report artifact, so that folder has to exist
    # or Desktop refuses to open the project. Four empty, correctly-named pages
    # are emitted; the visuals themselves are specified in REPORT_BUILD_GUIDE.md
    # and dropped on in Desktop, because visual JSON is version-pinned and
    # cannot be validated outside Desktop.
    REPORT.mkdir(parents=True, exist_ok=True)

    (REPORT / ".platform").write_text(
        '{\n'
        '  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",\n'
        '  "metadata": {\n'
        '    "type": "Report",\n'
        '    "displayName": "WorkforceIQ"\n'
        '  },\n'
        '  "config": {\n'
        '    "version": "2.0",\n'
        '    "logicalId": "' + guid("logical:report") + '"\n'
        '  }\n'
        '}\n', encoding="utf-8")

    # binds the report to the semantic model sitting beside it
    (REPORT / "definition.pbir").write_text(
        '{\n'
        '  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",\n'
        '  "version": "1.0",\n'
        '  "datasetReference": {\n'
        '    "byPath": {\n'
        '      "path": "../WorkforceIQ.SemanticModel"\n'
        '    }\n'
        '  }\n'
        '}\n', encoding="utf-8")

    sections = build_sections()
    report = {
        "config": json.dumps({
            "version": "5.55",
            "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
        }),
        "layoutOptimization": 0,
        "resourcePackages": [{
            "resourcePackage": {
                "disabled": False,
                "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json",
                           "type": 202}],
                "name": "SharedResources",
                "type": 2,
            }
        }],
        "sections": sections,
        "filters": "[]",
    }
    (REPORT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    total = sum(len(sec["visualContainers"]) for sec in sections)
    print("wrote report.json: " + str(len(sections)) + " pages, "
          + str(total) + " visuals")

    con.close()
    print("\nwrote PBIP semantic model to " + str(MODEL.relative_to(ROOT)))

    # ---------------------------------------------------------------- measures.dax
    dax = ["// =====================================================================",
           "// WorkforceIQ - all DAX measures",
           "//",
           "// Mirror of the measures in the TMDL model, in one readable file so the",
           "// DAX can be reviewed (or pasted into an existing report) without",
           "// opening Power BI. Generated by generators/build_powerbi.py.",
           "//",
           "// PREREQUISITE: mark DimDate as the date table on DimDate[date_key].",
           "// Every time-intelligence measure below returns wrong numbers silently",
           "// if you skip that step.",
           "// =====================================================================",
           ""]
    current = None
    for name, expr, fmt, folder, doc in MEASURES:
        if folder != current:
            dax.append("")
            dax.append("// ---------------------------------------------------------------")
            dax.append("// " + folder)
            dax.append("// ---------------------------------------------------------------")
            current = folder
        dax.append("")
        for line in doc.split("\n"):
            dax.append("// " + line)
        dax.append("// format: " + fmt)
        expr = expr.strip()
        if "\n" in expr:
            dax.append(name + " =")
            dax.extend("    " + l for l in expr.split("\n"))
        else:
            dax.append(name + " = " + expr)
    (PBI / "measures.dax").write_text("\n".join(dax) + "\n", encoding="utf-8")
    print("wrote powerbi/measures.dax (" + str(len(MEASURES)) + " measures)")


if __name__ == "__main__":
    main()
