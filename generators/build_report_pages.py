"""
Builds the visual layer of the Power BI report (report.json).

Emits real, laid-out visuals for all four pages rather than empty canvases, so
opening the .pbip gives a finished report instead of a blank starting point.

FORMAT NOTE
    This is the legacy `report.json` layout that PBIP uses when the enhanced
    PBIR format is not enabled. Each visual is a "visualContainer" whose
    `config` is a JSON STRING (not an object) containing:

        singleVisual.visualType    which chart to draw
        singleVisual.projections   which query fields land in which well
        singleVisual.prototypeQuery  the semantic query: From (table aliases)
                                     + Select (columns / measures / aggregates)

    The `Name` of each Select entry is the queryRef that projections point at,
    so those two have to agree exactly or the visual renders empty.

    The format is version-pinned to Power BI Desktop, and there is no way to
    validate it without Desktop. If a visual fails to render, it is almost
    always a projections/prototypeQuery queryRef mismatch.
"""
from __future__ import annotations

import hashlib
import json

CANVAS_W, CANVAS_H = 1280.0, 720.0

# Power BI QueryAggregateFunction codes. Count is 2, NOT 4 -- Min and Max sit
# after it. The first version had these as 0,1,2,3,4 = Sum,Avg,Min,Max,Count,
# which silently shifted three of them: the watchlist showed "Min of
# risk_score" where Max was intended, and the risk-tier donut plotted the
# MAXIMUM employee_id per tier instead of a count of employees. Both rendered
# without error, which is what made it easy to miss.
SUM, AVG, COUNT, MIN, MAX = 0, 1, 2, 3, 4


def vid(seed: str) -> str:
    """Stable 20-char alphanumeric visual id."""
    return "v" + hashlib.md5(seed.encode()).hexdigest()[:19]


# ---------------------------------------------------------------- query parts
def col(alias: str, table: str, column: str) -> dict:
    return {
        "Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": column},
        "Name": table + "." + column,
    }


def measure(alias: str, table: str, name: str) -> dict:
    return {
        "Measure": {"Expression": {"SourceRef": {"Source": alias}}, "Property": name},
        "Name": table + "." + name,
    }


def agg(alias: str, table: str, column: str, func: int = SUM) -> dict:
    label = {SUM: "Sum", AVG: "Avg", COUNT: "Count", MIN: "Min", MAX: "Max"}[func]
    return {
        "Aggregation": {
            "Expression": {"Column": {"Expression": {"SourceRef": {"Source": alias}},
                                      "Property": column}},
            "Function": func,
        },
        "Name": label + "(" + table + "." + column + ")",
    }


def frm(*tables: tuple[str, str]) -> list:
    """(alias, entity) pairs -> prototypeQuery From clause."""
    return [{"Name": a, "Entity": e, "Type": 0} for a, e in tables]


def ref(item: dict) -> dict:
    return {"queryRef": item["Name"]}


# ---------------------------------------------------------------- containers
# Turns off the grand-total row. On the department scorecard the total was
# averaging six departmental rates (0.14 against the true 0.1612) and averaging
# six standardised ratios into a meaningless 0.85; on the watchlist it reduced
# a per-employee probability to a single aggregate. Both invite misreading.
NO_TOTALS = {"total": [{"properties": {"totals": {"expr": {"Literal": {"Value": "false"}}}}}]}


def visual(name_seed, vtype, x, y, w, h, from_, select, projections,
           title=None, objects=None, sort=None, z=0):
    single = {
        "visualType": vtype,
        "projections": projections,
        "prototypeQuery": {"Version": 2, "From": from_, "Select": select},
        "drillFilterOtherVisuals": True,
        "objects": objects or {},
    }
    if sort:
        single["prototypeQuery"]["OrderBy"] = sort

    vc_objects = {}
    if title:
        vc_objects["title"] = [{
            "properties": {
                "show": {"expr": {"Literal": {"Value": "true"}}},
                "text": {"expr": {"Literal": {"Value": "'" + title.replace("'", "''") + "'"}}},
                "fontSize": {"expr": {"Literal": {"Value": "11D"}}},
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#1F5673'"}}}}},
            }
        }]
    if vc_objects:
        single["vcObjects"] = vc_objects

    config = {
        "name": vid(name_seed),
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
        "singleVisual": single,
    }
    return {
        "x": float(x), "y": float(y), "z": float(z),
        "width": float(w), "height": float(h),
        "config": json.dumps(config),
        "filters": "[]",
    }


def textbox(name_seed, x, y, w, h, paragraphs, z=0):
    """A static commentary box. Paragraphs are lists of (text, bold) runs."""
    para = []
    for runs in paragraphs:
        spans = []
        for text, bold in runs:
            span = {"value": text}
            if bold:
                span["textStyle"] = {"fontWeight": "bold"}
            spans.append(span)
        para.append({"textRuns": spans})

    config = {
        "name": vid(name_seed),
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
        "singleVisual": {
            "visualType": "textbox",
            "drillFilterOtherVisuals": True,
            "objects": {"general": [{"properties": {"paragraphs": para}}]},
        },
    }
    return {
        "x": float(x), "y": float(y), "z": float(z),
        "width": float(w), "height": float(h),
        "config": json.dumps(config),
        "filters": "[]",
    }


def card(name_seed, x, y, w, h, measure_name, title):
    m = measure("m", "_Measures", measure_name)
    return visual(name_seed, "card", x, y, w, h,
                  frm(("m", "_Measures")), [m], {"Values": [ref(m)]}, title=title)


# ---------------------------------------------------------------- pages
def page_executive() -> list:
    v = []
    cards = [
        ("Active Headcount", "Active headcount"),
        ("Attrition Rate", "Attrition rate"),
        ("Terminated Count", "Departures"),
        ("Avg Tenure (Years)", "Avg tenure (yrs)"),
        ("High Risk Employees", "High-risk now"),
    ]
    for i, (m, label) in enumerate(cards):
        v.append(card("exec.card." + m, 16 + i * 248, 12, 240, 96, m, label))

    # crude vs tenure-adjusted -- the most important visual in the report
    a = "d"
    c_dept = col(a, "DepartmentControlled", "department_name")
    c_crude = agg(a, "DepartmentControlled", "crude_attrition_rate", AVG)
    c_adj = agg(a, "DepartmentControlled", "tenure_adjusted_rate", AVG)
    v.append(visual(
        "exec.bar", "clusteredBarChart", 16, 120, 616, 300,
        frm((a, "DepartmentControlled")), [c_dept, c_crude, c_adj],
        {"Category": [ref(c_dept)], "Y": [ref(c_crude), ref(c_adj)]},
        title="Crude vs tenure-adjusted attrition by department"))

    # quarterly trend, rolling vs point-in-time
    b = "q"
    q_lab = col(b, "AttritionByDepartment", "year_quarter")
    q_roll = agg(b, "AttritionByDepartment", "rolling_4q_attrition_rate", AVG)
    q_qtr = agg(b, "AttritionByDepartment", "attrition_rate_qtr", AVG)
    v.append(visual(
        "exec.line", "lineChart", 648, 120, 616, 300,
        frm((b, "AttritionByDepartment")), [q_lab, q_roll, q_qtr],
        {"Category": [ref(q_lab)], "Y": [ref(q_roll), ref(q_qtr)]},
        title="Attrition trend - rolling 4q vs single quarter"))

    # department scorecard
    s = "s"
    s_dept = col(s, "DepartmentControlled", "department_name")
    s_head = agg(s, "DepartmentControlled", "headcount", SUM)
    s_obs = agg(s, "DepartmentControlled", "observed_leavers", SUM)
    s_exp = agg(s, "DepartmentControlled", "expected_leavers", SUM)
    s_crude = agg(s, "DepartmentControlled", "crude_attrition_rate", AVG)
    s_sar = agg(s, "DepartmentControlled", "standardised_attrition_ratio", AVG)
    s_verdict = col(s, "DepartmentControlled", "verdict")
    v.append(visual(
        "exec.table", "tableEx", 16, 432, 856, 272,
        frm((s, "DepartmentControlled")),
        [s_dept, s_head, s_obs, s_exp, s_crude, s_sar, s_verdict],
        {"Values": [ref(s_dept), ref(s_head), ref(s_obs), ref(s_exp),
                    ref(s_crude), ref(s_sar), ref(s_verdict)]},
        title="Department scorecard - observed vs expected leavers",
        objects=NO_TOTALS))

    v.append(textbox("exec.note", 888, 432, 376, 272, [
        [("The finding that changes a decision", True)],
        [("", False)],
        [("Engineering's crude rate is 17.6% and it accounts for 111 of 237 "
          "departures - 47% of all outflow. On the old quarterly report it "
          "looked like the problem to solve.", False)],
        [("", False)],
        [("Its standardised ratio is 1.01: it loses almost exactly what its "
          "tenure mix predicts. That is a hiring-volume consequence, not a "
          "retention failure.", False)],
        [("", False)],
        [("Sales (1.36 across 409 people) is where a retention task force "
          "belongs.", True)],
    ]))
    return v


def page_tenure() -> list:
    v = []
    t = "t"
    t_coh = col(t, "TenureCohort", "tenure_cohort")
    t_emp = agg(t, "TenureCohort", "employees", SUM)
    t_rate = agg(t, "TenureCohort", "attrition_rate", AVG)
    t_share = agg(t, "TenureCohort", "share_of_all_leavers", AVG)
    t_lift = agg(t, "TenureCohort", "lift_vs_company", AVG)

    v.append(visual(
        "ten.combo", "lineClusteredColumnComboChart", 16, 16, 616, 330,
        frm((t, "TenureCohort")), [t_coh, t_emp, t_rate],
        {"Category": [ref(t_coh)], "Y": [ref(t_emp)], "Y2": [ref(t_rate)]},
        title="Headcount and attrition rate by tenure cohort"))

    v.append(visual(
        "ten.share", "clusteredColumnChart", 648, 16, 616, 330,
        frm((t, "TenureCohort")), [t_coh, t_share],
        {"Category": [ref(t_coh)], "Y": [ref(t_share)]},
        title="Share of all leavers by cohort"))

    d = "c"
    d_dept = col(d, "DimEmployee", "department_name")
    d_coh = col(d, "DimEmployee", "tenure_cohort")
    d_rate = measure("m", "_Measures", "Attrition Rate")
    v.append(visual(
        "ten.matrix", "pivotTable", 16, 362, 792, 342,
        frm((d, "DimEmployee"), ("m", "_Measures")), [d_dept, d_coh, d_rate],
        {"Rows": [ref(d_dept)], "Columns": [ref(d_coh)], "Values": [ref(d_rate)]},
        title="Attrition rate by department and tenure cohort"))

    v.append(textbox("ten.note", 824, 362, 440, 342, [
        [("Rate and volume point at different cohorts", True)],
        [("", False)],
        [("The 0-1 yr cohort has the highest rate at 36.4%, but it is only "
          "6.8% of everyone who actually leaves - 44 people.", False)],
        [("", False)],
        [("The 1-3 yr cohort runs at 28.9% across 298 people and accounts for "
          "36.3% of all departures.", False)],
        [("", False)],
        [("Ranking on rate alone would send the retention budget to "
          "onboarding. The money belongs in the 1-3 year experience.", True)],
        [("", False)],
        [("The first three years carry 43% of all departures from 23% of "
          "headcount.", False)],
    ]))
    return v


def page_compensation() -> list:
    v = []
    q = "q"
    q_lab = col(q, "CompQuartile", "income_quartile_label")
    q_rate = agg(q, "CompQuartile", "attrition_rate", AVG)
    v.append(visual(
        "comp.quartile", "clusteredColumnChart", 16, 16, 616, 330,
        frm((q, "CompQuartile")), [q_lab, q_rate],
        {"Category": [ref(q_lab)], "Y": [ref(q_rate)]},
        title="Attrition by pay quartile within job role"))

    o = "o"
    o_ot = col(o, "OvertimeSatisfaction", "overtime_flag")
    o_sat = col(o, "OvertimeSatisfaction", "satisfaction_bucket")
    o_rate = agg(o, "OvertimeSatisfaction", "attrition_rate", AVG)
    o_lift = agg(o, "OvertimeSatisfaction", "lift_vs_base", AVG)
    v.append(visual(
        "comp.matrix", "pivotTable", 648, 16, 616, 330,
        frm((o, "OvertimeSatisfaction")), [o_ot, o_sat, o_rate, o_lift],
        {"Rows": [ref(o_ot)], "Columns": [ref(o_sat)],
         "Values": [ref(o_rate), ref(o_lift)]},
        title="THE HEADLINE - overtime x satisfaction, rate and lift vs base"))

    s = "s"
    s_band = col(s, "SpanBand", "span_band")
    s_rate = agg(s, "SpanBand", "attrition_rate", AVG)
    v.append(visual(
        "comp.span", "clusteredColumnChart", 16, 362, 616, 342,
        frm((s, "SpanBand")), [s_band, s_rate],
        {"Category": [ref(s_band)], "Y": [ref(s_rate)]},
        title="Manager span of control - a negative result"))

    v.append(textbox("comp.note", 648, 362, 616, 342, [
        [("Overtime is the lever. Satisfaction only modulates it.", True)],
        [("", False)],
        [("Overtime AND low satisfaction: 36.6% attrition - 2.27x the company "
          "base rate, and 5.3x the 6.9% of employees with neither factor.", False)],
        [("", False)],
        [("The asymmetry is the real insight: overtime alone (21.1%, even "
          "among the highly satisfied) is worse than low satisfaction alone "
          "(13.5%). High satisfaction does not protect someone being worked "
          "too hard.", False)],
        [("", False)],
        [("On span of control (left): no relationship. Flat at 14.0% / 16.6% "
          "/ 16.3% across 6-10, 11-15 and 16+ reports. The spike in the "
          "smallest band is small-sample noise across 79 people. Do not "
          "restructure the org on it.", False)],
    ]))
    return v


def page_watchlist() -> list:
    v = []
    e = "e"
    r = "r"

    sl_dept = col(e, "DimEmployee", "department_name")
    v.append(visual("watch.slicer.dept", "slicer", 16, 16, 240, 180,
                    frm((e, "DimEmployee")), [sl_dept],
                    {"Values": [ref(sl_dept)]}, title="Department"))

    sl_tier = col(r, "RiskScores", "risk_tier")
    v.append(visual("watch.slicer.tier", "slicer", 16, 204, 240, 180,
                    frm((r, "RiskScores")), [sl_tier],
                    {"Values": [ref(sl_tier)]}, title="Risk tier"))

    d_tier = col(r, "RiskScores", "risk_tier")
    d_count = agg(r, "RiskScores", "employee_id", COUNT)
    v.append(visual("watch.donut", "donutChart", 16, 392, 240, 200,
                    frm((r, "RiskScores")), [d_tier, d_count],
                    {"Category": [ref(d_tier)], "Y": [ref(d_count)]},
                    title="Employees by tier"))

    v.append(card("watch.card.high", 16, 600, 240, 104,
                  "High Risk Employees", "High-risk employees"))

    w_name = col(e, "DimEmployee", "employee_name")
    w_dept = col(e, "DimEmployee", "department_name")
    w_role = col(e, "DimEmployee", "job_role")
    w_mgr = col(e, "DimEmployee", "manager_name")
    w_score = agg(r, "RiskScores", "risk_score", MAX)
    w_tier = col(r, "RiskScores", "risk_tier")
    w_ten = agg(e, "DimEmployee", "tenure_years", MAX)
    w_pay = col(e, "DimEmployee", "income_quartile_label")
    w_ot = col(e, "DimEmployee", "overtime_flag")
    w_rule = measure("m", "_Measures", "Flight Risk Score (Rule-Based)")

    v.append(visual(
        "watch.table", "tableEx", 272, 16, 992, 576,
        frm((e, "DimEmployee"), (r, "RiskScores"), ("m", "_Measures")),
        [w_name, w_dept, w_role, w_mgr, w_score, w_tier, w_rule, w_ten, w_pay, w_ot],
        {"Values": [ref(w_name), ref(w_dept), ref(w_role), ref(w_mgr),
                    ref(w_score), ref(w_tier), ref(w_rule), ref(w_ten),
                    ref(w_pay), ref(w_ot)]},
        title="Active employees by modelled flight risk (highest first)",
        objects=NO_TOTALS,
        sort=[{"Direction": 2, "Expression": {"Aggregation": {
            "Expression": {"Column": {"Expression": {"SourceRef": {"Source": r}},
                                      "Property": "risk_score"}},
            "Function": MAX}}}]))

    v.append(textbox("watch.note", 272, 600, 992, 104, [
        [("Model output, not a verdict.", True),
         (" Scores are associations, not causes, and carry roughly +/-0.06 "
          "PR-AUC of uncertainty. Use as a prompt for a conversation - never "
          "as an input to a performance or employment decision. Employee "
          "names are synthetic. Filter RiskScores to model_name = "
          "'logistic_regression' and the latest scored_date.", False)],
    ]))
    return v


PAGES = [
    ("Executive Overview", page_executive),
    ("Tenure & Cohort Analysis", page_tenure),
    ("Compensation & Satisfaction", page_compensation),
    ("Attrition Risk Watchlist", page_watchlist),
]


def build_sections() -> list:
    sections = []
    for i, (name, builder) in enumerate(PAGES):
        sections.append({
            "config": "{}",
            "displayName": name,
            "displayOption": 1,
            "filters": "[]",
            "height": CANVAS_H,
            "name": vid("page:" + name),
            "ordinal": i,
            "visualContainers": builder(),
            "width": CANVAS_W,
        })
    return sections
