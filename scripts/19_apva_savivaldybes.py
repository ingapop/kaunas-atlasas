"""
APVA renovation map - municipality comparison (registry only, not on the public page).

Source: renomap.apva.lt public ArcGIS layers
  Hosted/RenomapMunicipalities_view   per municipality: projectsdone, projectsexec,
                                      bldgspotential, projectsdonepct
  Hosted/RenoMapStatLandingTotal_view national totals as shown on the landing page
Denominator everywhere = APVA "bldgspotential" (buildings APVA considers able to take
part in renovation programmes), NOT all multi-apartment buildings (see F-057/F-058).

Checks: each municipality's projectsdonepct == round(100*done/potential, 1);
national 15.92 % compared with (a) sum-based share and (b) unweighted mean.
Rank = position by projectsdonepct, descending (ties share the best rank).

Run from dashboardai/atlasas/:  python3 scripts/19_apva_savivaldybes.py
Outputs: data/raw/apva_renomap/municipalities.json, stat_total.json (raw);
         data/processed/apva_savivaldybes.csv
"""
import csv
import datetime
import json
import statistics
import urllib.parse
import urllib.request

B = "https://renomap.apva.lt/arcgis/rest/services/Hosted"
UA = {"User-Agent": "kaunas-atlas-research/1.0 (public data reproduction)"}


def q(layer, fields="*"):
    url = f"{B}/{layer}/FeatureServer/0/query?" + urllib.parse.urlencode(
        {"where": "1=1", "outFields": fields, "returnGeometry": "false", "f": "json"})
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()
    return url, json.loads(raw)


def main():
    today = datetime.date.today().isoformat()
    u1, mun = q("RenomapMunicipalities_view",
                "nameabbr,namefull,areano,projectsdone,projectsexec,bldgspotential,projectsdonepct")
    u2, tot = q("RenoMapStatLandingTotal_view")
    for name, url, d in (("municipalities.json", u1, mun), ("stat_total.json", u2, tot)):
        with open(f"data/raw/apva_renomap/{name}", "w", encoding="utf-8") as f:
            json.dump({"url": url, "fetched": today, "data": d}, f, ensure_ascii=False)
    rows = [x["attributes"] for x in mun["features"]]
    assert len(rows) == 60, len(rows)
    for r in rows:
        assert round(100 * r["projectsdone"] / r["bldgspotential"], 1) == r["projectsdonepct"], r
    rows.sort(key=lambda r: -r["projectsdonepct"])
    for r in rows:
        r["rangas"] = 1 + sum(1 for o in rows if o["projectsdonepct"] > r["projectsdonepct"])
    t = tot["features"][0]["attributes"]
    done, pot = sum(r["projectsdone"] for r in rows), sum(r["bldgspotential"] for r in rows)
    print(f"National (APVA landing layer): {t['projectsdonepct']} pct, done {t['projectsdone']}, "
          f"exec {t['projectsexec']}")
    print(f"Sum over 60 municipalities: done {done}, potential {pot}, share {100 * done / pot:.2f} pct; "
          f"unweighted mean {statistics.mean(r['projectsdonepct'] for r in rows):.2f} pct")
    for r in rows:
        if r["nameabbr"] in ("Kauno m. sav.", "Vilniaus m. sav.", "Šiaulių m. sav.", "Klaipėdos m. sav.",
                             "Panevėžio m. sav.", "Alytaus m. sav."):
            print(f"  {r['nameabbr']}: {r['projectsdonepct']} pct ({r['projectsdone']}/{r['bldgspotential']}), "
                  f"rank {r['rangas']}/60")
    with open("data/processed/apva_savivaldybes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rangas", "nameabbr", "projectsdone", "projectsexec",
                                          "bldgspotential", "projectsdonepct", "areano", "gauta"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in w.fieldnames[:-1]} | {"gauta": today})


if __name__ == "__main__":
    main()
