"""
Inlines web/data.json into web/template.html to produce web/index.html.

The published dashboard has to be a single self-contained file: the Artifact
CSP blocks external requests, and GitHub Pages should not need a second
round-trip for a 30 KB payload. So the JSON is baked into a
<script type="application/json"> block at build time.

    python generators/build_dashboard.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

def main() -> None:
    tpl = (WEB / "template.html").read_text(encoding="utf-8")
    data = (WEB / "data.json").read_text(encoding="utf-8")
    assert "/*__DATA__*/" in tpl, "placeholder missing from template.html"
    # guard against breaking out of the script block
    assert "</script" not in data, "payload contains a script terminator"
    out = tpl.replace("/*__DATA__*/", data)

    # Written to two places on purpose:
    #   web/index.html  - stable path the published Artifact is deployed from
    #   docs/index.html - GitHub Pages only serves from the repo root or /docs,
    #                     and the root is not the place for a build output
    # Both are generated; edit web/template.html, never either index.html.
    for target in (WEB / "index.html", ROOT / "docs" / "index.html"):
        target.write_text(out, encoding="utf-8")
        print("wrote %s  (%.0f KB)" % (target.relative_to(ROOT).as_posix(),
                                       target.stat().st_size / 1024))

if __name__ == "__main__":
    main()
