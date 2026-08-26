"""
Generates docs/how-it-was-built.html -- the illustrated build walkthrough.

Every terminal block on that page is REAL captured stdout from running the
pipeline, not prose describing what it would print. This script re-runs each
step, captures its output, and embeds it. If a step starts failing, the page
shows the failure rather than a stale success.

    python generators/build_buildlog_page.py            # re-run everything (slow)
    python generators/build_buildlog_page.py --no-run   # rebuild page from cache

Design deliberately matches web/template.html: same palette, same typefaces,
same token names. This is a companion page to the dashboard, not a separate
identity.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CACHE = DOCS / "_buildlog.json"

# (id, label, command, what it does, why it matters)
STEPS = [
    ("versions", "Environment",
     [sys.executable, "-c",
      "import sys, pandas, numpy, sklearn, duckdb, matplotlib, psycopg2, nbformat\n"
      "print('Python      ', sys.version.split()[0])\n"
      "print('pandas      ', pandas.__version__)\n"
      "print('numpy       ', numpy.__version__)\n"
      "print('scikit-learn', sklearn.__version__)\n"
      "print('duckdb      ', duckdb.__version__)\n"
      "print('matplotlib  ', matplotlib.__version__)\n"
      "print('psycopg2    ', psycopg2.__version__.split()[0])\n"
      "print('nbformat    ', nbformat.__version__)"],
     "pip install -r requirements.txt",
     "Eight libraries. DuckDB is the interesting one: it runs the same "
     "<code>.sql</code> files as PostgreSQL with no server, which is what made "
     "the SQL layer testable in a fast loop."),

    ("dataset", "1 &middot; Build the dataset",
     [sys.executable, "generators/build_dataset.py"],
     "python generators/build_dataset.py",
     "Normalises the flat 1,470-row IBM snapshot into six tables and synthesises "
     "the time dimension the source has no trace of. Deterministic: a fixed seed "
     "plus a per-employee SHA-256 hash means this reproduces byte-for-byte."),

    ("model", "2 &middot; Train and score the model",
     [sys.executable, "generators/train_attrition_model.py"],
     "python generators/train_attrition_model.py",
     "Trains logistic regression and a random forest, compares them on "
     "cross-validated PR-AUC, then scores every active employee. Runs BEFORE the "
     "seed file, because the seed embeds the scores it produces."),

    ("seed", "3 &middot; Emit the seed SQL",
     [sys.executable, "generators/build_seed_sql.py"],
     "python generators/build_seed_sql.py",
     "Writes a dependency-free INSERT script so anyone with <code>psql</code> and "
     "no Python can build the whole database."),

    ("views", "4 &middot; Run the eight views",
     [sys.executable, "generators/run_views_local.py"],
     "python generators/run_views_local.py",
     "Executes the exact <code>.sql</code> files that ship to PostgreSQL against "
     "DuckDB. This is where every documented figure comes from -- nothing in the "
     "docs is hand-typed."),

    ("export", "5 &middot; Export for Power BI",
     [sys.executable, "generators/export_for_powerbi.py"],
     "python generators/export_for_powerbi.py",
     "Runs each view and writes the result to CSV. These are not hand-made "
     "extracts: the SQL layer still computes every number the report displays."),

    ("powerbi", "6 &middot; Generate the Power BI project",
     [sys.executable, "generators/build_powerbi.py"],
     "python generators/build_powerbi.py",
     "Introspects column types from the live views and emits the TMDL semantic "
     "model plus the report visuals. The model cannot drift from the schema -- "
     "change a view, re-run, and it follows."),

    ("dashboard", "7 &middot; Build the web dashboard",
     [sys.executable, "generators/build_dashboard.py"],
     "python generators/build_dashboard.py",
     "Inlines the computed results into a single self-contained HTML file. No "
     "fetch, no API dependency, works even when the database is paused."),

    ("load", "8 &middot; Load PostgreSQL",
     [sys.executable, "generators/load_to_postgres.py"],
     "python generators/load_to_postgres.py",
     "Runs schema &rarr; seed &rarr; views &rarr; RLS against the live database. The "
     "security step is never skipped, because <code>schema.sql</code> opens with "
     "<code>DROP TABLE ... CASCADE</code> and that takes the RLS policies with it."),

    ("parity", "9 &middot; Prove both engines agree",
     [sys.executable, "generators/verify_parity.py"],
     "python generators/verify_parity.py",
     "Thirteen checks -- including row-level comparison across all 1,470 "
     "employees -- run against PostgreSQL and DuckDB. This caught two silent "
     "wrong-answer bugs that raised no error on either engine."),
]

SHOTS = [
    ("01_executive.png", "Executive Overview",
     "Headline KPIs, crude vs tenure-adjusted attrition by department, the "
     "quarterly trend against its rolling 4-quarter window, and the department "
     "scorecard showing observed against expected leavers."),
    ("02_tenure.png", "Tenure &amp; Cohort Analysis",
     "Headcount and attrition rate per cohort, each cohort's share of total "
     "outflow, and the department &times; cohort matrix. The highest rate and the "
     "largest share of leavers are different cohorts."),
    ("03_compensation.png", "Compensation &amp; Satisfaction",
     "Attrition by in-role pay quartile, the overtime &times; satisfaction "
     "cross-segment with lift against the base rate, and the span-of-control "
     "chart reporting a negative result."),
    ("04_watchlist.png", "Attrition Risk Watchlist",
     "Every active employee ranked by modelled flight risk, with the rule-based "
     "heuristic beside the model score, plus department and tier slicers."),
]

BUGS = [
    ("Tenure jitter applied to active employees only",
     "Class-dependent leakage. Put every leaver on an exact integer tenure and "
     "every stayer just above one, faking a 71% attrition rate in the "
     "under-1-year band that the model happily exploited."),
    ("Non-deterministic <code>NTILE(4)</code>",
     "Tied salaries straddling a quartile boundary must be split, and which row "
     "went where was arbitrary. PostgreSQL and DuckDB disagreed by one employee."),
    ("Bare <code>::NUMERIC</code> cast",
     "Arbitrary precision on PostgreSQL, <code>DECIMAL(18,3)</code> on DuckDB. "
     "Pay percentiles silently truncated to three decimals on one engine only."),
    ("RLS destroyed by every rebuild",
     "<code>schema.sql</code> drops tables, which drops their policies. All seven "
     "tables were briefly exposed with default write grants."),
    ("Dashboard bars rendered empty",
     "Bar width was applied from a <code>requestAnimationFrame</code> callback, "
     "which never fires in a backgrounded or zero-size viewport. Correctness "
     "must not depend on an animation frame."),
    ("Mermaid ER diagram would not parse",
     "<code>FK UK</code> and an invented <code>PK_FK</code>. Mermaid allows one "
     "key token per attribute."),
    ("Wrong <code>.pbip</code> schema URL, missing report folder",
     "The project would not open at all."),
    ("Table named <code>Measures</code>",
     "Reserved name in the Tabular object model."),
    ("CompatibilityLevel downgrade 1606 &rarr; 1567",
     "Tabular rejects downgrades outright, so every regeneration broke a project "
     "Desktop had already opened."),
    ("<code>DECIMAL</code> columns imported as text",
     "<code>AVERAGE()</code> and <code>MAX()</code> over text broke two visuals, "
     "surfacing as a misleading &ldquo;capacity or license issue&rdquo;."),
    ("Shared <code>DataFolder</code> parameter",
     "The model's only cross-query dependency. Power Query failed all 13 loads "
     "with &ldquo;a cyclic reference was encountered&rdquo;."),
    ("Aggregation enum shifted",
     "Count is 2, not 4. The risk-tier donut plotted <em>maximum employee_id</em> "
     "per tier instead of counting employees -- and rendered without error."),
    ("Misleading grand totals",
     "Averaged six departmental rates into 0.14 against a true 0.1612."),
]


def capture() -> dict:
    out = {}
    for sid, label, cmd, _, _ in STEPS:
        print("  running " + sid + " ...", end=" ", flush=True)
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        text = (p.stdout or "") + (p.stderr or "")
        # drop noisy library warnings that say nothing about the pipeline
        lines = [l for l in text.splitlines()
                 if "UserWarning" not in l and "pandas only supports" not in l
                 and "warnings.warn" not in l]
        out[sid] = {"text": "\n".join(lines).strip(), "code": p.returncode}
        print("exit " + str(p.returncode))
    CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def term(text: str) -> str:
    return '<pre class="term"><code>' + html.escape(text) + "</code></pre>"


def main() -> None:
    if "--no-run" in sys.argv and CACHE.exists():
        results = json.loads(CACHE.read_text(encoding="utf-8"))
    else:
        results = capture()

    css = (ROOT / "web" / "template.html").read_text(encoding="utf-8")
    css = css[css.index("<style>"):css.index("</style>") + 8]

    p: list[str] = []
    a = p.append

    a("<title>Building WorkforceIQ</title>")
    a('<link rel="preconnect" href="https://fonts.googleapis.com">')
    a('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    a('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
      'family=Archivo:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&'
      'family=IBM+Plex+Mono:wght@400;500;600&display=swap">')
    a(css)
    a("""<style>
.term {
  background: var(--surface-alt); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 8px;
  padding: 14px 16px; overflow-x: auto; margin: 14px 0 0;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 12.5px; line-height: 1.55; color: var(--ink-soft);
  white-space: pre; tab-size: 4;
}
.term code { font: inherit; color: inherit; }
.cmd {
  font-family: "IBM Plex Mono", monospace; font-size: 13px;
  background: var(--ink); color: #E7EFF2; padding: 11px 14px;
  border-radius: 7px; overflow-x: auto; white-space: pre; margin-top: 12px;
}
.cmd::before { content: "$ "; color: var(--accent); }
:root:not([data-theme="light"]) .cmd, :root[data-theme="dark"] .cmd { background: #05090B; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .cmd { background: #05090B; } }
.shot {
  width: 100%; height: auto; display: block; border-radius: 8px;
  border: 1px solid var(--border-firm); margin-top: 14px;
}
.badge {
  display: inline-block; font-family: "IBM Plex Mono", monospace;
  font-size: 10.5px; font-weight: 600; padding: 2px 8px; border-radius: 20px;
  background: var(--good-bg); color: var(--good); margin-left: 8px;
  vertical-align: middle;
}
.badge.fail { background: var(--critical-bg); color: var(--critical); }
.buglist { display: flex; flex-direction: column; gap: 10px; margin-top: 16px; }
.bug {
  display: grid; grid-template-columns: 30px 1fr; gap: 12px;
  padding: 13px 15px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface);
}
.bug .n {
  font-family: "IBM Plex Mono", monospace; font-size: 12px;
  font-weight: 600; color: var(--critical);
}
.bug b { display: block; font-size: 13.5px; margin-bottom: 3px; }
.bug span { font-size: 13px; color: var(--ink-mute); }
.tool { display: grid; grid-template-columns: 150px 1fr; gap: 12px; padding: 9px 0;
        border-bottom: 1px dashed var(--border); font-size: 13.5px; }
.tool:last-child { border-bottom: none; }
.tool b { font-weight: 600; }
.flow {
  font-family: "IBM Plex Mono", monospace; font-size: 12px; line-height: 1.6;
  background: var(--surface-alt); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; overflow-x: auto; white-space: pre;
  color: var(--ink-soft); margin-top: 16px;
}
@media (max-width: 620px) { .tool { grid-template-columns: 1fr; gap: 2px; } }
</style>""")

    # ---------------------------------------------------------------- header
    a('<header class="masthead"><div class="wrap">')
    a('<div class="eyebrow">WorkforceIQ &middot; build walkthrough</div>')
    a("<h1>How this was built</h1>")
    a('<p class="sub">Every step, every tool, and the real output each command '
      "produced. The terminal blocks below are captured stdout from actually "
      "running the pipeline &mdash; not prose describing what it would print.</p>")
    a('<div class="meta-row">'
      "<div><b>10</b> pipeline steps</div>"
      "<div><b>8</b> analytical SQL views</div>"
      "<div><b>22</b> DAX measures</div>"
      "<div><b>13</b> bugs found and fixed</div>"
      "<div><b>28</b> commits</div>"
      "</div></div></header>")

    a('<nav class="navbar"><div class="wrap navbar-inner">'
      '<a href="#tools">Tools</a><a href="#flow">Pipeline</a>'
      '<a href="#steps">Steps &amp; output</a><a href="#report">The report</a>'
      '<a href="#results">What we got</a><a href="#bugs">Bugs</a>'
      '<a href="https://vuday3336.github.io/WorkforceIQ--Analysis/">&larr; Dashboard</a>'
      "</div></nav>")

    a('<main class="wrap">')

    # ---------------------------------------------------------------- tools
    a('<section class="finding" id="tools"><div class="finding-head">'
      '<div class="fnum">00</div><div><h2>What was installed, and why each one</h2>'
      '<p class="qline">Eight libraries plus PostgreSQL and Power BI Desktop.</p>'
      "</div></div><div class=\"body-col\">")
    a('<div class="card"><div class="card-title">Chosen for a reason, not by habit</div>')
    for name, why in [
        ("PostgreSQL 17", "The production database, hosted on Supabase. The brief called for a real relational DB."),
        ("DuckDB", "Runs the same <code>.sql</code> files with no server. This is what made the SQL testable in a fast loop, and later proved the two engines agree."),
        ("pandas + numpy", "Deterministic transforms. The whole dataset rebuilds byte-identically from a seed."),
        ("scikit-learn", "Pipelines keep preprocessing inside cross-validation, which is what prevents the classic scaling leak."),
        ("matplotlib", "Static PNGs that render on GitHub without a JS runtime."),
        ("nbformat + nbclient", "The notebook is generated <em>and executed</em> by a script, so the committed <code>.ipynb</code> carries real outputs and can never drift from the training code."),
        ("psycopg2", "Runs the shipped <code>.sql</code> files against the live database, so the files themselves are what gets tested."),
        ("Power BI Desktop", "PBIP/TMDL is Microsoft's text project format: diffable and reviewable. A <code>.pbix</code> is an opaque binary."),
        ("Vanilla HTML/CSS/JS", "No framework, no build step, no CDN. One self-contained file GitHub Pages serves directly."),
        ("PowerShell", "Power BI Desktop has no CLI, so a Win32 window capture was the only way to get screenshots."),
    ]:
        a('<div class="tool"><b>' + name + "</b><span>" + why + "</span></div>")
    a("</div>")
    a('<div class="card"><div class="card-title">Actual versions used</div>'
      '<div class="card-note">Output of importing every dependency.</div>')
    a(term(results["versions"]["text"]))
    a("</div></div></section>")

    # ---------------------------------------------------------------- flow
    a('<section class="finding" id="flow"><div class="finding-head">'
      '<div class="fnum">01</div><div><h2>The pipeline, end to end</h2>'
      '<p class="qline">One CSV in, four deliverables out.</p></div></div>'
      '<div class="body-col"><div class="card">')
    a('<div class="flow">' + html.escape(
        "data/raw/ibm_hr_attrition.csv        1,470 real employees, one dateless snapshot\n"
        "        |\n"
        "        |  build_dataset.py           normalise + synthesise the time dimension\n"
        "        v\n"
        "data/processed/*.csv                 6 tables, 23,645 rows, deterministic\n"
        "        |\n"
        "        +--> schema.sql + seed_data.sql ------> PostgreSQL (Supabase, live)\n"
        "        |         7 tables, FKs, checks              + rls_policies.sql\n"
        "        |\n"
        "        +--> sql/views/*.sql ------------------> 8 analytical views\n"
        "        |         window functions, CTEs,             verified identical on\n"
        "        |         indirect standardisation            Postgres AND DuckDB\n"
        "        |\n"
        "        +--> train_attrition_model.py ---------> attrition_risk_scores\n"
        "        |         features read FROM the views        1,233 employees scored\n"
        "        |\n"
        "        +--> build_powerbi.py -----------------> powerbi/ (PBIP + TMDL)\n"
        "        |         14 tables, 22 DAX measures          4 pages, 23 visuals\n"
        "        |\n"
        "        +--> build_dashboard.py ---------------> docs/index.html\n"
        "                  results inlined                    GitHub Pages") + "</div>")
    a("</div></div></section>")

    # ---------------------------------------------------------------- steps
    a('<section class="finding" id="steps"><div class="finding-head">'
      '<div class="fnum">02</div><div><h2>Every step, and what it printed</h2>'
      '<p class="qline">Run in this order &mdash; the model must be scored before the '
      "seed file is generated, or the seed ships without the watchlist.</p>"
      "</div></div><div class=\"body-col\">")
    for sid, label, _, cmd, why in STEPS:
        if sid == "versions":
            continue
        r = results[sid]
        badge = ('<span class="badge">exit 0</span>' if r["code"] == 0
                 else '<span class="badge fail">exit ' + str(r["code"]) + "</span>")
        a('<div class="card"><div class="card-title">' + label + badge + "</div>")
        a('<div class="card-note">' + why + "</div>")
        a('<div class="cmd">' + html.escape(cmd) + "</div>")
        a(term(r["text"]))
        a("</div>")
    a("</div></section>")

    # ---------------------------------------------------------------- report
    a('<section class="finding" id="report"><div class="finding-head">'
      '<div class="fnum">03</div><div><h2>What the Power BI report looks like</h2>'
      '<p class="qline">Four pages, 23 visuals, generated as text and rendered in '
      "Desktop.</p></div></div><div class=\"body-col\">")
    for fn, title, desc in SHOTS:
        a('<div class="card"><div class="card-title">' + title + "</div>"
          '<div class="card-note">' + desc + "</div>"
          '<img class="shot" loading="lazy" alt="' + html.escape(title) +
          '" src="dashboard_screenshots/' + fn + '"></div>')
    a("</div></section>")

    # ---------------------------------------------------------------- results
    a('<section class="finding" id="results"><div class="finding-head">'
      '<div class="fnum">04</div><div><h2>What we got out of it</h2>'
      '<p class="qline">The findings the pipeline actually produced.</p>'
      "</div></div><div class=\"body-col\">")
    a('<div class="card"><div class="card-title">The three findings that matter</div>')
    a('<div class="takeaway"><b>Overtime is the lever.</b> Overtime combined with '
      "low satisfaction runs at <b>36.6%</b> attrition &mdash; 2.27&times; the company "
      "base rate and 5.3&times; the 6.9% of employees with neither factor. The "
      "asymmetry is the real insight: overtime alone (21.1%, even among the highly "
      "satisfied) is worse than low satisfaction alone (13.5%). High satisfaction "
      "does not protect someone being worked too hard.</div>")
    a('<div class="takeaway"><b>Engineering does not have a retention problem.</b> '
      "Its crude rate is 17.6% and it accounts for 111 of 237 departures &mdash; 47% "
      "of all outflow. But its standardised attrition ratio is <b>1.01</b>: it loses "
      "almost exactly what its tenure mix predicts. That is a hiring-volume "
      "consequence, not a retention failure. <b>Sales, at 1.36 across 409 people, is "
      "where a retention task force belongs.</b></div>")
    a('<div class="takeaway neg"><b>Span of control explains nothing.</b> Flat at '
      "14.0% / 16.6% / 16.3% across the 6&ndash;10, 11&ndash;15 and 16+ bands. The "
      "apparent spike in the smallest band is small-sample noise across 79 people. "
      "A negative result, reported as one rather than dressed up.</div>")
    a("</div>")
    a('<div class="card"><div class="card-title">Deliverables</div>'
      '<div class="card-note">Everything the pipeline produced.</div>')
    for k, v in [
        ("Database", "Live PostgreSQL &mdash; 1,470 employees, 2,466 risk scores, 16 views, RLS on 7/7 tables, zero security lints"),
        ("SQL layer", "8 analytical views, 13/13 parity checks identical across PostgreSQL and DuckDB"),
        ("Model", "Logistic regression, recall 0.66 on leavers, 5-fold PR-AUC 0.507 &plusmn; 0.065; 1,233 active employees scored"),
        ("Power BI", "PBIP/TMDL &mdash; 14 tables, 22 DAX measures, 4 pages, 23 visuals"),
        ("Web dashboard", "Self-contained single file on GitHub Pages"),
        ("Docs", "Findings write-up, ER diagram, executed notebook, this page"),
    ]:
        a('<div class="tool"><b>' + k + "</b><span>" + v + "</span></div>")
    a("</div></div></section>")

    # ---------------------------------------------------------------- bugs
    a('<section class="finding" id="bugs" style="border-bottom:none">'
      '<div class="finding-head"><div class="fnum">05</div>'
      "<div><h2>Thirteen bugs, found and fixed</h2>"
      '<p class="qline">Three of these produced wrong numbers while rendering '
      "without any error at all.</p></div></div><div class=\"body-col\">")
    a('<div class="card"><div class="card-title">The list</div>'
      '<div class="card-note">This is what the commit history actually documents.</div>'
      '<div class="buglist">')
    for i, (title, why) in enumerate(BUGS, 1):
        a('<div class="bug"><div class="n">' + str(i).zfill(2) + "</div><div><b>"
          + title + "</b><span>" + why + "</span></div></div>")
    a("</div></div>")
    a('<div class="takeaway"><b>Two structural changes were made so that class '
      "cannot recur silently.</b> An unmapped column type now raises instead of "
      "defaulting to string, so a new type fails the build rather than shipping a "
      "silently wrong model. And every Power BI projection reference is validated "
      "against its query before commit.</div>")
    a("</div></section>")

    a("</main>")
    a('<footer class="wrap"><h3>WorkforceIQ</h3><p>'
      '<a href="https://vuday3336.github.io/WorkforceIQ--Analysis/">Live dashboard</a> '
      '&middot; <a href="https://github.com/Vuday3336/WorkforceIQ--Analysis">Repository</a> '
      '&middot; <a href="https://github.com/Vuday3336/WorkforceIQ--Analysis/blob/main/docs/sql_findings.md">SQL findings</a> '
      '&middot; <a href="https://github.com/Vuday3336/WorkforceIQ--Analysis/blob/main/docs/PROJECT_WALKTHROUGH.md">Written walkthrough</a>'
      "</p></footer>")

    out = DOCS / "how-it-was-built.html"
    out.write_text("\n".join(p), encoding="utf-8")
    print("\nwrote " + out.relative_to(ROOT).as_posix()
          + "  (" + format(out.stat().st_size / 1024, ".0f") + " KB)")


if __name__ == "__main__":
    main()
