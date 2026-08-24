"""
WorkforceIQ - dataset builder.

Turns the flat IBM HR Analytics snapshot (1,470 rows, one row per employee)
into the normalised Everline Corp schema, and synthesises the time dimension
the source does not have.

WHAT IS REAL vs WHAT IS SYNTHESISED
-----------------------------------
Real (taken unmodified from the source CSV):
    every driver variable and the attrition label itself -- MonthlyIncome,
    JobSatisfaction, EnvironmentSatisfaction, WorkLifeBalance, OverTime,
    PerformanceRating, StockOptionLevel, PercentSalaryHike, DistanceFromHome,
    Age, Gender, MaritalStatus, Education, YearsAtCompany, JobLevel, Attrition.

Synthesised (the source is a single dateless snapshot, so these must be
constructed for any time-intelligence or SCD modelling to be possible):
    - employee names
    - hire_date / termination_date
    - the per-year rows in compensation_history and performance_reviews
    - the manager -> report hierarchy
    - voluntary vs involuntary split on terminations
    - the Everline 6-department overlay (a documented role -> department map)

Consequence for the analysis: findings about *drivers* (overtime, satisfaction,
pay percentile, tenure) are real signal from the source data. The quarter-over-
quarter *trend shape* is partly by construction and is labelled as such in the
README -- it is not presented as a discovered finding.

Deterministic: fixed seed + per-employee hash, so re-running reproduces every
table byte for byte.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ibm_hr_attrition.csv"
OUT = ROOT / "data" / "processed"

# Analysis anchor. FY2024 + FY2025 are the two complete fiscal years the
# leadership team cares about; the snapshot is taken at the close of FY2025.
AS_OF = date(2025, 12, 31)

SEED = 20260824

# --- Everline Corp narrative overlay -------------------------------------
# The source has 3 departments (R&D / Sales / HR). The business scenario calls
# for 6. This mapping is deterministic and documented in docs/sql_findings.md
# so the overlay is fully reversible back to the source roles.
ROLE_MAP = {
    "Sales Executive":           ("Sales",            "Sales Executive"),
    "Sales Representative":      ("Sales",            "Sales Representative"),
    "Research Scientist":        ("Engineering",      "Software Engineer"),
    "Research Director":         ("Engineering",      "Engineering Director"),
    "Laboratory Technician":     ("Engineering",      "QA Engineer"),
    "Manufacturing Director":    ("Operations",       "Operations Director"),
    "Healthcare Representative": ("Customer Support", "Support Representative"),
    "Human Resources":           ("HR",               "HR Generalist"),
    "Manager":                   ("Finance",          "Finance Manager"),
}

DEPARTMENTS = [
    (1, "Sales",            "Go-To-Market"),
    (2, "Engineering",      "Technology"),
    (3, "HR",               "Corporate Services"),
    (4, "Finance",          "Corporate Services"),
    (5, "Operations",       "Technology"),
    (6, "Customer Support", "Go-To-Market"),
]
DEPT_ID = {name: did for did, name, _ in DEPARTMENTS}

EDUCATION = {1: "Below College", 2: "College", 3: "Bachelor", 4: "Master", 5: "Doctor"}

FIRST_NAMES = [
    "Aarav", "Priya", "Daniel", "Mei", "Sofia", "Omar", "Hannah", "Luis", "Nadia",
    "Ethan", "Chloe", "Rahul", "Fatima", "Marcus", "Yuki", "Elena", "Tobias",
    "Amara", "Jonas", "Isabel", "Kwame", "Lena", "Diego", "Anika", "Peter",
    "Rosa", "Samir", "Grace", "Viktor", "Leila", "Andre", "Mira", "Caleb",
    "Sanne", "Hugo", "Zara", "Felix", "Nina", "Oscar", "Ingrid", "Tariq",
    "Claire", "Nikolai", "Aisha", "Bruno", "Freya", "Idris", "Camila", "Soren",
    "Divya",
]
LAST_NAMES = [
    "Mehta", "Okafor", "Lindqvist", "Tanaka", "Moreau", "Haddad", "Novak",
    "Ferreira", "Kowalski", "Bergman", "Nakamura", "Sharma", "Rossi", "Dubois",
    "Andersen", "Petrov", "Silva", "Marchetti", "Vandenberg", "Kaur",
    "Fitzgerald", "Osei", "Larsen", "Bianchi", "Duarte", "Halvorsen", "Ibrahim",
    "Nowak", "Castellanos", "Whitfield", "Aoki", "Delacroix", "Reyes",
    "Simonsen", "Varga", "Bakker", "Cardoso", "Eriksen", "Grimaldi", "Hassan",
    "Jokinen", "Krishnan", "Lombardi", "Mbeki", "Navarro", "Ortega", "Pashkov",
    "Quintero", "Ramos", "Steiner",
]


def h(key: str, salt: str) -> float:
    """Stable [0,1) hash -- lets every derived field be reproducible per employee."""
    d = hashlib.sha256((salt + ":" + key).encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64


def hint(key: str, salt: str, lo: int, hi: int) -> int:
    """Stable integer in [lo, hi]."""
    return lo + int(h(key, salt) * (hi - lo + 1))


def back_years(d: date, years: float) -> date:
    return d - timedelta(days=int(round(years * 365.25)))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = pd.read_csv(RAW, encoding="utf-8-sig")
    src = src.sort_values("EmployeeNumber").reset_index(drop=True)

    departments = pd.DataFrame(
        DEPARTMENTS, columns=["department_id", "department_name", "division"]
    )

    # ---------------------------------------------------------------- employees
    ramp = np.array([1.00, 1.05, 1.12, 1.20, 1.28, 1.38, 1.48, 1.60])
    ramp = np.cumsum(ramp / ramp.sum())

    emp_rows = []
    for _, r in src.iterrows():
        eid = int(r.EmployeeNumber)
        k = str(eid)
        dept_name, job_role = ROLE_MAP[r.JobRole]
        terminated = r.Attrition == "Yes"

        # Leavers: place the termination inside the 2-year observation window,
        # then back-date the hire by their real tenure. Stayers: back-date the
        # hire from the snapshot date by their real tenure.
        #
        # The source records tenure only in whole years, so a within-year
        # offset has to be added to turn it into a real date. CRITICALLY that
        # offset must be drawn the SAME WAY for leavers and stayers. An
        # earlier version of this generator jittered stayers only, which put
        # every leaver exactly on an integer tenure and every stayer just
        # above one -- a class-dependent gap that the model could exploit as
        # free signal, and which showed up as an implausible 71% attrition
        # rate in the under-1-year band. Same salt, same range, both classes.
        within_year = timedelta(days=hint(k, "tenure_offset", 0, 364))
        if terminated:
            q = min(int(np.searchsorted(ramp, h(k, "termq"))), 7)
            q_start = date(2024 + q // 4, 1 + 3 * (q % 4), 1)
            term_date = q_start + timedelta(days=hint(k, "termd", 0, 88))
            hire_date = back_years(term_date, float(r.YearsAtCompany)) - within_year
        else:
            term_date = None
            hire_date = back_years(AS_OF, float(r.YearsAtCompany)) - within_year

        emp_rows.append(
            dict(
                employee_id=eid,
                first_name=FIRST_NAMES[hint(k, "fn", 0, len(FIRST_NAMES) - 1)],
                last_name=LAST_NAMES[hint(k, "ln", 0, len(LAST_NAMES) - 1)],
                department_id=DEPT_ID[dept_name],
                job_role=job_role,
                job_level=int(r.JobLevel),
                hire_date=hire_date,
                gender=r.Gender,
                age=int(r.Age),
                marital_status=r.MaritalStatus,
                education_level=EDUCATION[int(r.Education)],
                distance_from_home=int(r.DistanceFromHome),
                business_travel=r.BusinessTravel,
                current_status="Terminated" if terminated else "Active",
                # carried through the pipeline, not written to the employees table
                _term_date=term_date,
                _attrition=r.Attrition,
                _income=int(r.MonthlyIncome),
                _hike=int(r.PercentSalaryHike),
                _stock=int(r.StockOptionLevel),
                _perf=int(r.PerformanceRating),
                _jobsat=int(r.JobSatisfaction),
                _envsat=int(r.EnvironmentSatisfaction),
                _wlb=int(r.WorkLifeBalance),
                _ot=r.OverTime,
                _tenure=float(r.YearsAtCompany),
            )
        )
    emp = pd.DataFrame(emp_rows)

    # ---------------------------------------------------------------- managers
    # A realistic three-level tree per department: department head -> (middle
    # managers, only where the department is big enough to need them) -> line
    # managers -> individual contributors. Seniority decides who manages whom.
    #
    # Line-manager spans are deliberately UNEVEN (roughly 4-20 reports) so
    # vw_manager_span_attrition has real variation to test against. Flattening
    # every remainder onto one person would invent a 60-person span and make
    # the span-of-control analysis meaningless.
    emp["manager_id"] = pd.NA

    def spread(reports, managers, salt):
        """Hand out reports to managers with uneven, deterministic spans."""
        if not managers:
            return {}
        raw = [4 + int(h(str(m), salt) * 17) for m in managers]
        total = sum(raw)
        spans = [max(1, int(round(s / total * len(reports)))) for s in raw]
        out, cursor = {}, 0
        for m, s in zip(managers, spans):
            for e in reports[cursor : cursor + s]:
                out[e] = m
            cursor += s
        for j, e in enumerate(reports[cursor:]):  # rounding remainder, round-robin
            out[e] = managers[j % len(managers)]
        return out

    for _, grp in emp.groupby("department_id"):
        ordered = grp.sort_values(["job_level", "age"], ascending=False)["employee_id"].tolist()
        head, rest = ordered[0], ordered[1:]

        # roughly 13 individual contributors per line manager
        n_line = max(1, int(np.ceil(len(rest) / 13)))
        line_mgrs, ics = rest[:n_line], rest[n_line:]

        assign = {}
        if n_line > 9:
            # large department: insert a middle layer so that the department
            # head does not end up with fifty direct reports
            n_mid = max(2, int(np.ceil(n_line / 7)))
            mid_mgrs, line_mgrs = line_mgrs[:n_mid], line_mgrs[n_mid:]
            for m in mid_mgrs:
                assign[m] = head
            assign.update(spread(line_mgrs, mid_mgrs, "midspan"))
        else:
            for m in line_mgrs:
                assign[m] = head

        assign.update(spread(ics, line_mgrs, "span"))
        # the department head reports to nobody
        emp["manager_id"] = emp["employee_id"].map(assign).fillna(emp["manager_id"])


    # ---------------------------------------------------------------- comp history
    # Walk the real current salary backwards through the real hike percentage to
    # produce an SCD Type 2 style effective-dated history.
    comp_rows = []
    comp_id = 1
    for _, r in emp.iterrows():
        k = str(r.employee_id)
        end = r._term_date or AS_OF
        n_rows = int(min(r._tenure, 6)) + 1
        income = float(r._income)
        stock = int(r._stock)
        for i in range(n_rows):
            eff = end - timedelta(days=int(365.25 * i))
            if eff < r.hire_date:
                eff = r.hire_date
            hike = r._hike if i == 0 else max(3, r._hike + hint(k + ":" + str(i), "hike", -6, 4))
            comp_rows.append(
                dict(
                    comp_id=comp_id,
                    employee_id=r.employee_id,
                    effective_date=eff,
                    monthly_income=int(round(income)),
                    salary_hike_pct=round(float(hike), 1),
                    stock_option_level=stock,
                )
            )
            comp_id += 1
            income = income / (1 + hike / 100.0)  # step back one review cycle
            if stock > 0 and h(k + ":" + str(i), "stockdrop") < 0.45:
                stock -= 1
            if eff == r.hire_date:
                break
    comp = pd.DataFrame(comp_rows).sort_values(["employee_id", "effective_date"])

    # ---------------------------------------------------------------- reviews
    def jitter(base: int, key: str, salt: str) -> int:
        return int(np.clip(base + hint(key, salt, -1, 1), 1, 4))

    rev_rows = []
    rev_id = 1
    for _, r in emp.iterrows():
        k = str(r.employee_id)
        end = r._term_date or AS_OF
        n_rows = int(min(r._tenure, 5)) + 1
        for i in range(n_rows):
            rd = end - timedelta(days=int(365.25 * i) + hint(k + ":" + str(i), "revd", 0, 20))
            if rd < r.hire_date:
                break
            if i == 0:  # most recent review = the real source values
                perf, js, es, wlb, ot = r._perf, r._jobsat, r._envsat, r._wlb, r._ot
            else:
                sfx = k + ":" + str(i)
                perf = int(np.clip(r._perf + hint(sfx, "perf", -1, 0), 3, 4))
                js = jitter(r._jobsat, sfx, "js")
                es = jitter(r._envsat, sfx, "es")
                wlb = jitter(r._wlb, sfx, "wlb")
                ot = "Yes" if h(sfx, "ot") < (0.55 if r._ot == "Yes" else 0.18) else "No"
            rev_rows.append(
                dict(
                    review_id=rev_id,
                    employee_id=r.employee_id,
                    review_date=rd,
                    performance_rating=perf,
                    job_satisfaction=js,
                    environment_satisfaction=es,
                    work_life_balance=wlb,
                    overtime_flag=ot,
                    manager_id=r.manager_id if pd.notna(r.manager_id) else None,
                )
            )
            rev_id += 1
    rev = pd.DataFrame(rev_rows).sort_values(["employee_id", "review_date"])

    # ---------------------------------------------------------------- attrition events
    leavers = emp[emp._attrition == "Yes"].copy()
    att = pd.DataFrame(
        dict(
            event_id=range(1, len(leavers) + 1),
            employee_id=leavers.employee_id.values,
            termination_date=leavers._term_date.values,
            attrition_flag=1,
            # The source has no voluntary/involuntary split; ~80/20 is the
            # commonly cited industry mix and is applied deterministically.
            voluntary_flag=[1 if h(str(e), "vol") < 0.80 else 0 for e in leavers.employee_id],
        )
    )

    # ---------------------------------------------------------------- date dimension
    days = pd.date_range(date(2015, 1, 1), date(2026, 12, 31), freq="D")
    dim_date = pd.DataFrame(
        dict(
            date_key=days.date,
            year=days.year,
            quarter=days.quarter,
            month=days.month,
            month_name=days.strftime("%B"),
            year_quarter=[str(y) + " Q" + str(q) for y, q in zip(days.year, days.quarter)],
            year_month=days.strftime("%Y-%m"),
            is_month_end=days.is_month_end,
        )
    )

    # ---------------------------------------------------------------- write
    emp_out = emp[
        [
            "employee_id", "first_name", "last_name", "department_id", "job_role",
            "job_level", "hire_date", "gender", "age", "marital_status",
            "education_level", "distance_from_home", "business_travel",
            "manager_id", "current_status",
        ]
    ].copy()
    emp_out["manager_id"] = emp_out["manager_id"].astype("Int64")

    tables = {
        "departments": departments,
        "employees": emp_out,
        "compensation_history": comp,
        "performance_reviews": rev,
        "attrition_events": att,
        "dim_date": dim_date,
    }
    for name, df in tables.items():
        df.to_csv(OUT / (name + ".csv"), index=False)
        print("{:24s} {:>7,} rows".format(name, len(df)))

    print("")
    print("base attrition rate  :", round(float((emp._attrition == "Yes").mean()), 4))
    print("departments          :", emp_out.department_id.nunique())
    print("distinct managers    :", int(emp_out.manager_id.nunique()))
    print("employees w/o manager:", int(emp_out.manager_id.isna().sum()))
    print("comp rows / employee :", round(len(comp) / len(emp), 2))
    print("review rows / emp    :", round(len(rev) / len(emp), 2))


if __name__ == "__main__":
    main()
