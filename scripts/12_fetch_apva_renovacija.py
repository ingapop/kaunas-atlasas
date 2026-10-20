"""
Fetch APVA "Lietuvos renovacijos zemelapis" (renomap.apva.lt) layers for
Kauno miesto savivaldybe.

Source: public ArcGIS FeatureServer behind https://renomap.apva.lt/Kauno-miesto-15/
  - RenomapProjects_view          renovation projects (status 1 = vykdomas, 2 = baigtas)
  - RenomapProjectsPotential_view "Potencialus daugiabuciai renovacijai" (not yet in a project)
  - RenomapProjectsOldPrograms_view "Renovuoti daugiabuciai pagal kitas programas"
    (older programmes: CPVA 1996-2004, owners' initiative 2005-2012, VRM)
Plus:
  - each layer's ?f=json metadata (field aliases / descriptions)
  - the municipality page-data JSON (APVA's own aggregate: bldgspotential,
    projectsdone, projectsexec, projectsdonepct) used to reconcile our counts
  - the site JS bundles that hold APVA's own definitional texts (legend labels,
    "procentine dalis ... nuo visu potencialiai galinciu dalyvauti ..."),
    saved so the wording quoted in METODOLOGIJA.md can be re-checked.

Query: where=municipality like 'Kauno m%' (maxRecordCount 1000 -> paginated
with resultOffset), outSR=4326, outFields=*.

Run from dashboardai/atlasas/:  python3 scripts/12_fetch_apva_renovacija.py
Outputs (data/raw/, git-ignored, never edited by hand):
  data/raw/apva_renomap/<layer>.json            all features (ArcGIS JSON)
  data/raw/apva_renomap/<layer>_meta.json       layer metadata
  data/raw/apva_renomap/kauno_miesto_page_data.json
  data/raw/apva_renomap/site_js/*.js
  data/raw/apva_renomap/fetch_log.json          fetch date + counts
"""
import datetime
import json
import os
import re
import time
import urllib.parse
import urllib.request

BASE = "https://renomap.apva.lt/arcgis/rest/services/Hosted"
LAYERS = [
    "RenomapProjects_view",
    "RenomapProjectsPotential_view",
    "RenomapProjectsOldPrograms_view",
]
WHERE = "municipality like 'Kauno m%'"
PAGE_DATA_URL = "https://renomap.apva.lt/page-data/Kauno-miesto-15/page-data.json"
MUNI_PAGE_URL = "https://renomap.apva.lt/Kauno-miesto-15/"
SITE_ROOT = "https://renomap.apva.lt"
OUT_DIR = "data/raw/apva_renomap"
UA = {"User-Agent": "kaunas-atlas-research/1.0 (public data reproduction)"}
PAGE = 1000


def get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=120).read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  retry {i+1} for {url[:90]}...: {e}")
            time.sleep(5)
    raise SystemExit(f"Failed: {url} ({last})")


def fetch_layer(layer):
    url0 = f"{BASE}/{layer}/FeatureServer/0"
    meta = json.loads(get(url0 + "?f=json"))
    # count first, so pagination completeness can be asserted
    q = {"where": WHERE, "returnCountOnly": "true", "f": "json"}
    count = json.loads(get(url0 + "/query?" + urllib.parse.urlencode(q)))["count"]
    feats, offset = [], 0
    while True:
        q = {
            "where": WHERE, "outFields": "*", "returnGeometry": "true",
            "outSR": "4326", "f": "json", "orderByFields": "objectid",
            "resultOffset": str(offset), "resultRecordCount": str(PAGE),
        }
        page = json.loads(get(url0 + "/query?" + urllib.parse.urlencode(q)))
        if "error" in page:
            raise SystemExit(f"{layer}: {page['error']}")
        got = page.get("features", [])
        feats.extend(got)
        if len(got) < PAGE and not page.get("exceededTransferLimit"):
            break
        offset += len(got)
        if not got:
            break
    ids = [f["attributes"]["objectid"] for f in feats]
    assert len(ids) == len(set(ids)), f"{layer}: duplicate objectids from pagination"
    assert len(feats) == count, f"{layer}: fetched {len(feats)} != count {count}"
    return meta, {"layer": layer, "where": WHERE, "features": feats}, count


def fetch_site_js():
    """Save the JS bundles referenced by the municipality page (definitions)."""
    html = get(MUNI_PAGE_URL).decode("utf-8", "replace")
    root_html = get(SITE_ROOT + "/").decode("utf-8", "replace")
    names = sorted(set(re.findall(r'(?:src|href)="(/[A-Za-z0-9_.-]+\.js)"', html + root_html)))
    js_dir = os.path.join(OUT_DIR, "site_js")
    os.makedirs(js_dir, exist_ok=True)
    saved = []
    for n in sorted(set(names)):
        if "polyfill" in n or "framework" in n:
            continue
        with open(os.path.join(js_dir, os.path.basename(n)), "wb") as f:
            f.write(get(SITE_ROOT + n))
        saved.append(n)
    with open(os.path.join(OUT_DIR, "kauno_miesto_page.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return saved


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log = {
        "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": BASE, "where": WHERE, "layers": {},
    }
    for layer in LAYERS:
        meta, data, count = fetch_layer(layer)
        with open(os.path.join(OUT_DIR, f"{layer}_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        with open(os.path.join(OUT_DIR, f"{layer}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        log["layers"][layer] = {
            "count": count,
            "lastEditDate_utc": datetime.datetime.fromtimestamp(
                meta.get("editingInfo", {}).get("lastEditDate", 0) / 1000,
                tz=datetime.timezone.utc).isoformat() if meta.get("editingInfo") else None,
        }
        print(f"{layer}: {count} features")

    pd = get(PAGE_DATA_URL)
    with open(os.path.join(OUT_DIR, "kauno_miesto_page_data.json"), "wb") as f:
        f.write(pd)
    attrs = json.loads(pd)["result"]["pageContext"]["feature"]["attributes"]
    log["apva_aggregate"] = {k: attrs.get(k) for k in
                             ("bldgspotential", "projectsdone", "projectsexec", "projectsdonepct")}
    print("APVA aggregate:", log["apva_aggregate"])

    log["site_js"] = fetch_site_js()
    with open(os.path.join(OUT_DIR, "fetch_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"Saved -> {OUT_DIR}/ (fetched_at {log['fetched_at']})")


if __name__ == "__main__":
    main()
