"""Package atlas v2 data for file:// use and build a single self-contained HTML for embedding (K13).

1. v2/data/atlas_data.js  – window.ATLAS_DATA = {"data/...": {...}}; loaded by v2/index.html via
   <script src>, so the page also works opened straight from disk (fetch is blocked on file://).
2. v2/dist/atlasas.html   – index.html with that script inlined: one file, only Google Fonts external.
   Works from file://, any static host, or a Wix "Embed HTML" block.
Plain JS, not <script type="application/json">: some hosts drop or re-insert non-JS script tags.

Run after scripts/22_v2_build_data.py:
    python3 scripts/23_v2_standalone.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "v2"
SRC = ROOT / "index.html"
DATA_JS = ROOT / "data" / "atlas_data.js"
OUT = ROOT / "dist" / "atlasas.html"
FILES = ["data/seniunijos.geojson", "data/apylinkes.geojson", "data/rodikliai.json", "data/programa.json"]
TAG = '<script src="data/atlas_data.js"></script>'


def main():
    data = {rel: json.loads((ROOT / rel).read_text(encoding="utf-8")) for rel in FILES}
    # compact; escape "</" so the payload cannot close an inline <script> tag
    js = "window.ATLAS_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n"
    DATA_JS.write_text(js, encoding="utf-8")

    html = SRC.read_text(encoding="utf-8")
    assert html.count(TAG) == 1, f"{TAG} not found once in index.html"
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html.replace(TAG, f"<script>{js}</script>"), encoding="utf-8")
    for f in (DATA_JS, OUT):
        print(f"wrote {f.relative_to(ROOT.parent)}  {f.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
