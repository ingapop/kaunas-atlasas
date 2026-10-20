"""
Assign APVA renovation-map points (scripts/12) to the 23 curated atlas
neighbourhoods and aggregate per neighbourhood.

Assignment method is IDENTICAL to the rest of the atlas: nearest curated
neighbourhood centre by Haversine distance. The functions are imported from
scripts/05_assign_to_neighborhoods.py (not copied) so the method cannot drift;
centres come from data/processed/curated_neighborhoods.csv (scripts/09).

Categories (APVA's own, see METODOLOGIJA.md "6. Renovacija"):
  baigta            RenomapProjects_view, status = 2 ("baigtas")
  vykdoma           RenomapProjects_view, status = 1 ("vykdomas" = investment
                    plan approved; NOT necessarily works on site). Sub-counts:
                    contractworkspurchased == "Taip" (APVA "inProgressBought"),
                    finstatus == "Neigyvendinamas" (flagged by APVA as not
                    implementable but still status 1)
  potencialus       RenomapProjectsPotential_view ("Renovacijai potencialus
                    daugiabutis" - not in any project)
  senos_programos   RenomapProjectsOldPrograms_view ("Renovuoti daugiabuciai
                    pagal kitas programas": CPVA 1996-2004, owners' initiative
                    2005-2012). Reported separately, NOT in APVA's denominator.

Denominator (n_viso_apva) = baigta + vykdoma + potencialus, which is APVA's own
"bldgspotential" (4308 for Kauno m. on 2026-10-19). Unit = APVA record (project
or potential building), not a unique address - see duplicate check.

Checks (written to data/processed/renovacija_patikra.csv, summary to console):
  - city totals reconcile to APVA page-data aggregate (asserted)
  - duplicate addresses within / across layers
  - points outside the Kaunas city municipality polygon (APVA's own polygon
    from the page-data JSON, EPSG:3857 -> WGS84)
  - points > 2 km from the nearest curated neighbourhood centre
  - old-programme points within 15 m of a project / potential point (overlap)

Run from dashboardai/atlasas/:  python3 scripts/13_renovacija_vietoves.py
Outputs:
  data/processed/renovacija_taskai.csv     one row per APVA point (addresses, no personal data)
  data/processed/renovacija_vietoves.csv   per neighbourhood + city total row
  data/processed/renovacija_patikra.csv    sanity-check findings
"""
import csv
import importlib.util
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

RAW = "data/raw/apva_renomap"
OUT_POINTS = "data/processed/renovacija_taskai.csv"
OUT_AREAS = "data/processed/renovacija_vietoves.csv"
OUT_CHECKS = "data/processed/renovacija_patikra.csv"
FAR_KM = 2.0
SMALL_DEN = 50  # below this, show counts, not shares (unstable percentages)
CITY_ROW = "Kauno m. (iš viso)"

# --- reuse the atlas assignment method from scripts/05 ---------------------
sys.dont_write_bytecode = True  # no __pycache__ in scripts/
_spec = importlib.util.spec_from_file_location(
    "assign05", os.path.join(os.path.dirname(os.path.abspath(__file__)), "05_assign_to_neighborhoods.py"))
assign05 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assign05)
haversine_km = assign05.haversine_km
nearest_neighborhood = assign05.nearest_neighborhood
load_neighborhoods = assign05.load_neighborhoods


def load(layer):
    with open(os.path.join(RAW, f"{layer}.json"), encoding="utf-8") as f:
        return json.load(f)["features"]


def norm_addr(s):
    s = (s or "").replace(", Kaunas, Kauno m. sav.", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def merc_to_wgs(x, y):
    lon = x / 6378137.0 * 180.0 / math.pi
    lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
    return lon, lat


def point_in_rings(lon, lat, rings):
    """Even-odd rule over all rings (handles holes)."""
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > lat) != (y2 > lat):
                xint = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                if lon < xint:
                    inside = not inside
    return inside


def pct(num, den):
    return round(100.0 * num / den, 1) if den else ""


def main():
    with open(os.path.join(RAW, "kauno_miesto_page_data.json"), encoding="utf-8") as f:
        page = json.load(f)["result"]
    feat = page["pageContext"]["feature"]
    apva = feat["attributes"]
    apva_data = page["data"]
    city_rings = [[merc_to_wgs(x, y) for x, y in ring] for ring in feat["geometry"]["rings"]]

    neighborhoods = load_neighborhoods()
    names = [n["name"] for n in neighborhoods]

    points = []
    for f in load("RenomapProjects_view"):
        a = f["attributes"]
        st = a["status"]
        points.append({
            "kategorija": {2: "baigta", 1: "vykdoma"}[st],
            "sluoksnis": "RenomapProjects_view",
            "id": a["ipno"],
            "adresas": f"{a['street']} {a['houseno']}".strip(),
            "lon": f["geometry"]["x"], "lat": f["geometry"]["y"],
            "statybos_metai": a["buildingyear"] or "",
            "butai": a["buildingapartments"] if a["buildingapartments"] is not None else "",
            "ip_suderinimo_data": a["ipdate"] or "",
            "pabaigos_data": a["projectendyear"] or "",
            "planuojama_pabaiga": a["plannedprojectendyear"] or "",
            "finansavimo_statusas": a["finstatus"] or "",
            "rangos_darbai_nupirkti": a["contractworkspurchased"] or "",
            "darbu_atlikta_proc": a["completeworkpct"] if a["completeworkpct"] is not None else "",
            "pastaba": "",
        })
    for f in load("RenomapProjectsPotential_view"):
        a = f["attributes"]
        points.append({"kategorija": "potencialus", "sluoksnis": "RenomapProjectsPotential_view",
                       "id": a["objectid"], "adresas": a["address"],
                       "lon": f["geometry"]["x"], "lat": f["geometry"]["y"]})
    for f in load("RenomapProjectsOldPrograms_view"):
        a = f["attributes"]
        points.append({"kategorija": "senos_programos", "sluoksnis": "RenomapProjectsOldPrograms_view",
                       "id": a["ipno"], "adresas": a["address"],
                       "lon": f["geometry"]["x"], "lat": f["geometry"]["y"], "pastaba": a.get("note") or ""})

    checks = []
    for p in points:
        name, dist = nearest_neighborhood(p["lat"], p["lon"], neighborhoods)
        p["vietove"] = name
        p["atstumas_iki_centro_km"] = round(dist, 3)
        p["miesto_ribose"] = point_in_rings(p["lon"], p["lat"], city_rings)
        if not p["miesto_ribose"]:
            checks.append(("uz_miesto_ribu", p["kategorija"], p["adresas"], p["id"], name, round(dist, 2), ""))
        if dist > FAR_KM:
            checks.append(("toliau_nei_2km", p["kategorija"], p["adresas"], p["id"], name, round(dist, 2), ""))

    # duplicate addresses
    by_addr = defaultdict(list)
    for p in points:
        by_addr[norm_addr(p["adresas"])].append(p)
    for addr, ps in sorted(by_addr.items()):
        if len(ps) > 1:
            cats = Counter(p["kategorija"] for p in ps)
            kind = "dubliuotas_adresas_tarp_sluoksniu" if len({p["sluoksnis"] for p in ps}) > 1 else "dubliuotas_adresas"
            checks.append((kind, "+".join(f"{k}x{v}" for k, v in sorted(cats.items())), ps[0]["adresas"],
                           ";".join(str(p["id"]) for p in ps), ps[0]["vietove"], "",
                           f"{len(ps)} irasai"))

    # old-programme points that coincide (<= 15 m) with a project/potential point
    OVERLAP_M = 15.0
    others = [p for p in points if p["kategorija"] != "senos_programos"]
    for p in points:
        if p["kategorija"] != "senos_programos":
            continue
        dmin = min(haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) for q in others) * 1000
        if dmin <= OVERLAP_M:
            checks.append(("senos_programos_sutampa_15m", p["kategorija"], p["adresas"], p["id"],
                           p["vietove"], round(dmin / 1000, 3), ""))

    # --- aggregate ------------------------------------------------------
    agg = {n: Counter() for n in names}
    years = {n: [] for n in names}
    for p in points:
        c = agg[p["vietove"]]
        c[p["kategorija"]] += 1
        if p["kategorija"] == "vykdoma":
            if p["rangos_darbai_nupirkti"] == "Taip":
                c["vykdoma_rangos"] += 1
            if p["finansavimo_statusas"] == "Neįgyvendinamas":
                c["vykdoma_neigyv"] += 1
        if p["kategorija"] in ("baigta", "vykdoma") and str(p.get("statybos_metai", ""))[:4].isdigit():
            years[p["vietove"]].append(int(p["statybos_metai"][:4]))

    fields = ["vietove", "n_baigta", "n_vykdoma", "n_vykdoma_rangos_darbai_nupirkti",
              "n_vykdoma_neigyvendinama", "n_potencialus", "n_viso_apva",
              "dalis_baigta_proc", "dalis_baigta_ir_vykdoma_proc", "mazas_vardiklis",
              "n_senos_programos", "dalis_baigta_su_senomis_proc",
              "statybos_metai_mediana", "n_su_statybos_metais"]

    def row(label, c, ys):
        den = c["baigta"] + c["vykdoma"] + c["potencialus"]
        return {
            "vietove": label, "n_baigta": c["baigta"], "n_vykdoma": c["vykdoma"],
            "n_vykdoma_rangos_darbai_nupirkti": c["vykdoma_rangos"],
            "n_vykdoma_neigyvendinama": c["vykdoma_neigyv"],
            "n_potencialus": c["potencialus"], "n_viso_apva": den,
            "dalis_baigta_proc": pct(c["baigta"], den),
            "dalis_baigta_ir_vykdoma_proc": pct(c["baigta"] + c["vykdoma"], den),
            "mazas_vardiklis": "taip" if den < SMALL_DEN else "ne",
            "n_senos_programos": c["senos_programos"],
            # secondary variant: old-programme buildings counted as renovated and
            # added to the denominator (they are outside APVA's 4308)
            "dalis_baigta_su_senomis_proc": pct(c["baigta"] + c["senos_programos"],
                                                den + c["senos_programos"]),
            "statybos_metai_mediana": f"{statistics.median(ys):g}" if ys else "",
            "n_su_statybos_metais": len(ys),
        }

    rows = [row(n, agg[n], years[n]) for n in names]
    total = Counter()
    for n in names:
        total.update(agg[n])
    rows.append(row(CITY_ROW, total, [y for n in names for y in years[n]]))

    # --- reconcile to APVA's own aggregate (hard fail if not) -------------
    t = rows[-1]
    assert t["n_baigta"] == apva["projectsdone"], (t["n_baigta"], apva["projectsdone"])
    assert t["n_vykdoma"] == apva["projectsexec"], (t["n_vykdoma"], apva["projectsexec"])
    assert t["n_viso_apva"] == apva["bldgspotential"], (t["n_viso_apva"], apva["bldgspotential"])
    assert t["dalis_baigta_proc"] == apva["projectsdonepct"], (t["dalis_baigta_proc"], apva["projectsdonepct"])
    assert t["n_vykdoma_rangos_darbai_nupirkti"] == apva_data["inProgressBought"]["totalCount"]

    with open(OUT_AREAS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    pfields = ["kategorija", "sluoksnis", "id", "adresas", "lat", "lon", "vietove",
               "atstumas_iki_centro_km", "miesto_ribose", "statybos_metai", "butai",
               "ip_suderinimo_data", "pabaigos_data", "planuojama_pabaiga",
               "finansavimo_statusas", "rangos_darbai_nupirkti", "darbu_atlikta_proc", "pastaba"]
    with open(OUT_POINTS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pfields, restval="")
        w.writeheader()
        for p in points:
            w.writerow({k: p.get(k, "") for k in pfields})

    with open(OUT_CHECKS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["patikra", "kategorija", "adresas", "id", "vietove", "atstumas_km", "pastaba"])
        w.writerows(checks)

    # --- console summary (ASCII) -----------------------------------------
    done_years = [int(p["pabaigos_data"][:4]) for p in points
                  if p["kategorija"] == "baigta" and p["pabaigos_data"][:4].isdigit()]
    ip_years = [int(p["ip_suderinimo_data"][:4]) for p in points
                if p["kategorija"] in ("baigta", "vykdoma") and p["ip_suderinimo_data"][:4].isdigit()]
    uniq_done = len({norm_addr(p["adresas"]) for p in points if p["kategorija"] == "baigta"})
    uniq_exec = len({norm_addr(p["adresas"]) for p in points if p["kategorija"] == "vykdoma"})
    print(f"City: baigta {t['n_baigta']} / vykdoma {t['n_vykdoma']} / potencialus {t['n_potencialus']} "
          f"/ denominator {t['n_viso_apva']} / done share {t['dalis_baigta_proc']} pct "
          f"/ done+exec {t['dalis_baigta_ir_vykdoma_proc']} pct / old programmes {t['n_senos_programos']}")
    print(f"Reconciled with APVA page-data: projectsdone {apva['projectsdone']}, projectsexec "
          f"{apva['projectsexec']}, bldgspotential {apva['bldgspotential']}, projectsdonepct {apva['projectsdonepct']}")
    print(f"vykdoma: works contracted {t['n_vykdoma_rangos_darbai_nupirkti']}, marked not implementable "
          f"{t['n_vykdoma_neigyvendinama']}")
    ex = [p for p in points if p["kategorija"] == "vykdoma"]
    print("vykdoma by finstatus:", dict(Counter(p["finansavimo_statusas"] or "(tuscia)" for p in ex)))
    print("vykdoma by IP approval year:", dict(sorted(Counter(p["ip_suderinimo_data"][:4] for p in ex).items())))
    old = [p for p in points if p["kategorija"] == "senos_programos"]
    print("senos_programos by note:", dict(Counter(p["pastaba"] for p in old)))
    print(f"Unique addresses: baigta {uniq_done}, vykdoma {uniq_exec}")
    print(f"Completed projects end dates {min(done_years)}-{max(done_years)}; IP approval years "
          f"{min(ip_years)}-{max(ip_years)}")
    print(f"Checks: {Counter(c[0] for c in checks)}")
    print(f"Saved -> {OUT_AREAS}, {OUT_POINTS}, {OUT_CHECKS}")


if __name__ == "__main__":
    main()
