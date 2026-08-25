"""
Builds the WorkforceIQ database on a live PostgreSQL instance.

Runs the shipped .sql files in dependency order -- schema, seed, then every
view -- so this script exercises exactly the artefacts in the repo rather
than a parallel Python code path. If this succeeds, the .sql files are known
good.

    pip install -r requirements.txt
    cp .env.example .env      # then put your connection string in it
    python generators/load_to_postgres.py

    python generators/load_to_postgres.py --views-only   # re-create views only
    python generators/load_to_postgres.py --verify       # run sanity queries

Connection string comes from DATABASE_URL in .env or the environment:

    postgresql://postgres.REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres

Supabase offers three endpoints and only one of them works here:

    session pooler      pooler.supabase.com:5432   <- use this one
    transaction pooler  pooler.supabase.com:6543   rejects multi-statement DDL
    direct              db.<ref>.supabase.co:5432  IPv6-only since 2024, so it
                                                   fails on most home networks

The session pooler is the endpoint that is both IPv4-reachable and in session
mode, which is what the multi-statement DDL below requires.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def dsn() -> str:
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(
            "DATABASE_URL is not set.\n"
            "Copy .env.example to .env and fill in your Postgres connection string."
        )
    # Fail loudly on the two mistakes that otherwise surface as a confusing
    # error halfway through the DDL.
    if "YOUR_PASSWORD" in url or "YOUR-PASSWORD" in url:
        sys.exit("DATABASE_URL still contains the password placeholder. Edit .env.")
    if ":6543/" in url:
        sys.exit(
            "That is the TRANSACTION pooler (port 6543), which cannot run the\n"
            "multi-statement DDL this script sends. Use the SESSION pooler:\n"
            "change the port from 6543 to 5432 in DATABASE_URL."
        )
    return url


def run_file(cur, path: Path) -> None:
    print("  running " + path.relative_to(ROOT).as_posix() + " ...", end=" ", flush=True)
    cur.execute(path.read_text(encoding="utf-8"))
    print("ok")


VERIFY = [
    ("row counts", """
        SELECT 'departments' AS t, COUNT(*) FROM departments
        UNION ALL SELECT 'employees',            COUNT(*) FROM employees
        UNION ALL SELECT 'compensation_history', COUNT(*) FROM compensation_history
        UNION ALL SELECT 'performance_reviews',  COUNT(*) FROM performance_reviews
        UNION ALL SELECT 'attrition_events',     COUNT(*) FROM attrition_events
        UNION ALL SELECT 'attrition_risk_scores',COUNT(*) FROM attrition_risk_scores
        UNION ALL SELECT 'dim_date',             COUNT(*) FROM dim_date
        ORDER BY 1
    """),
    ("department attrition, tenure-controlled", """
        SELECT department_name, crude_attrition_rate,
               standardised_attrition_ratio, verdict
        FROM vw_department_attrition_controlled
    """),
    ("overtime x satisfaction", """
        SELECT overtime_flag, satisfaction_bucket, employees,
               attrition_rate, lift_vs_base
        FROM vw_overtime_satisfaction_attrition
        ORDER BY lift_vs_base DESC
    """),
    ("watchlist tiers", """
        SELECT model_name, risk_tier, COUNT(*)
        FROM vw_attrition_risk_watchlist
        GROUP BY 1, 2 ORDER BY 1, 2
    """),
]


def main() -> None:
    views_only = "--views-only" in sys.argv
    verify_only = "--verify" in sys.argv

    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    cur = conn.cursor()
    print("connected")

    if not verify_only:
        if not views_only:
            print("\n[1/4] schema")
            run_file(cur, SQL / "schema.sql")
            print("\n[2/4] seed data (this one takes a moment)")
            run_file(cur, SQL / "seed_data.sql")
        print("\n[3/4] analytical views")
        for path in sorted((SQL / "views").glob("*.sql")):
            run_file(cur, path)
        # Always last, and never skipped. schema.sql starts with
        # DROP TABLE ... CASCADE, which takes every RLS policy with it -- so
        # security has to be reapplied on every build or a rebuild silently
        # leaves the tables open.
        print("\n[4/4] row-level security and grants")
        run_file(cur, SQL / "rls_policies.sql")

    print("\n--- verification ---")
    for title, query in VERIFY:
        cur.execute(query)
        rows = cur.fetchall()
        print("\n" + title)
        for row in rows:
            print("  " + "  ".join(str(v) for v in row))

    cur.close()
    conn.close()
    print("\ndone")


if __name__ == "__main__":
    main()
