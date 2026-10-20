"""
Stage-2 verification of the atlas v2 indicators (PLANAS.md stage 2, 2026-10-20).

Independent re-computation from the RAW files - does NOT import or reuse the code of
scripts 03, 15, 17, 18 or 22. Own loaders, own census allocation, own point-in-polygon,
education flags re-derived from the raw SMIR shapefile, own OSM parsing and polygon
assembly. Then compares every unit x every shown indicator with the shipped site file
v2/data/rodikliai.json (output of 22) and reports differences.

What is checked
  1. Denominators: 2021 census 100 m grid (GyvSur100M2021) -> population, 0-14, 65+ per
     seniunija / apylinke (area-weighted; cells outside the city polygon -> nearest unit).
     Suppressed age groups re-estimated with own code from the cell mean age and OSP
     S3R167_M3010202 group mean ages (same rule as script 15 since 2026-10-20), and the city
     result compared with the OSP 2021-01-01 age structure.
     Also: bounds of the 0-14 / 65+ city shares under the "1-9 published as 9" rule, and
     the population of grid cells NOT labelled "Kauno m." that lie inside the city (K15).
  2. Numerators: SMIR (F-064 rule), OSM health / sport / playgrounds / green / streets,
     APVA renovation points, social-service points.
  3. City value counted directly inside the Registru centras municipality polygon
     (not as a sum of units), and sum(seniunijos) == sum(apylinkes) == city.
  4. Publication thresholds (K19): rates null if pop < 1 500 / children < 250 /
     APVA denominator < 20 / streets < 2 km.
  5. Shipped file: every value of 13 shown indicators for 9 seniunijos + 90 apylinkes + city.

Run from dashboardai/atlasas/:  python3 scripts/25_v2_patikra.py
Outputs: data/processed/geo/v2_patikra.json (summary), data/processed/geo/v2_patikra_skirtumai.csv
Aggregates only; no personal data involved.
"""
import csv
import json
import os
import re
from collections import Counter, defaultdict

import shapefile
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.ops import polygonize, transform, unary_union
from shapely.prepared import prep

RAW = "data/raw"
GEO_RAW = f"{RAW}/geografija"
SITE = "v2/data/rodikliai.json"
OUT_JSON = "data/processed/geo/v2_patikra.json"
OUT_CSV = "data/processed/geo/v2_patikra_skirtumai.csv"
KAUNAS_RC = "19"      # Registru centras SAV_KODAS
KAUNAS_SMIR = "43"    # SMIR Sav_kodas
T_POP, T_KIDS, T_RENO, T_STREET_KM = 1500, 250, 20, 2.0   # K19
SHOWN = ["vaikai", "sveikata", "socialines_paslaugos", "zalieji_plotai", "zalieji_plotai_dalis",
         "pesciuju_takai", "renovacija", "renovacija_pradeta", "sportas", "zaidimu_aiksteles",
         "vaiku_dalis", "senjoru_dalis", "tankis"]

wgs2lks = Transformer.from_crs("EPSG:4326", "EPSG:3346", always_xy=True).transform


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- units
def load_units():
    sen = []
    for ft in jload(f"{GEO_RAW}/adr_gra_seniunijos.json")["features"]:
        p = ft["properties"]
        if str(p["SAV_KODAS"]) == KAUNAS_RC:
            sen.append({"id": str(p["SEN_KODAS"]), "name": p["SEN_PAV"], "g": make_valid(shape(ft["geometry"]))})
    apl = []
    for ft in jload(f"{GEO_RAW}/vrk_apylinkes_2024_seimas.geojson")["features"]:
        p = ft["properties"]
        g = shape(ft["geometry"])
        apl.append({"id": str(p["AREA2_ID"]), "name": p["AREA2_NAME"], "g": make_valid(transform(wgs2lks, g))})
    city = None
    for ft in jload(f"{GEO_RAW}/adr_gra_savivaldybes.json")["features"]:
        if str(ft["properties"]["SAV_KODAS"]) == KAUNAS_RC:
            city = make_valid(shape(ft["geometry"]))
    for u in sen + apl:
        u["p"] = prep(u["g"])
        u["bb"] = u["g"].bounds
    return sen, apl, city


def locate(units, pt):
    hits = [u for u in units
            if u["bb"][0] <= pt.x <= u["bb"][2] and u["bb"][1] <= pt.y <= u["bb"][3] and u["p"].covers(pt)]
    return hits[0]["id"] if hits else None


# ---------------------------------------------------------------- census
def osp_ages():
    """OSP S3R167_M3010202, Kauno m. sav., 2021-01-01, both sexes, single years (own parser)."""
    import xml.etree.ElementTree as ET
    ns = {"g": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"}
    root = ET.parse(f"{GEO_RAW}/osp_S3R167_M3010202_2021.xml").getroot()
    by_age = {}
    for obs in root.iter(f"{{{ns['g']}}}Obs"):
        key = {v.get("id"): v.get("value") for v in obs.find("g:ObsKey", ns)}
        if key["savivaldybesRegdb"] == "19" and key["Lytis"] == "0":
            by_age[key["Demogr_amziusM1411"]] = float(obs.find("g:ObsValue", ns).get("value"))
    yrs = {int(a): v for a, v in by_age.items() if a.isdigit()}
    old = by_age["g085"]
    young = sum(v for a, v in yrs.items() if a < 15)
    mid = sum(v for a, v in yrs.items() if 15 <= a < 65)
    senior = sum(v for a, v in yrs.items() if a >= 65) + old
    means = (sum((a + .5) * v for a, v in yrs.items() if a < 15) / young,
             sum((a + .5) * v for a, v in yrs.items() if 15 <= a < 65) / mid,
             (sum((a + .5) * v for a, v in yrs.items() if a >= 65) + 89.5 * old) / senior)
    tot = by_age["g000g999"]
    return {"n": (young, mid, senior), "tot": tot, "means": means, "shares": (young / tot, mid / tot, senior / tot)}


def mean_age_fill(groups, pop, mean_age, osp):
    """Suppressed groups (published 9 = 1..9): 1 masked -> remainder; 2 -> solve the two linear
    equations (count, age sum); 3 -> closest point to the OSP-share split on both constraints."""
    idx = [i for i in range(3) if groups[i] == 9]
    rest = pop - sum(groups[i] for i in range(3) if i not in idx)
    out = [float(x) for x in groups]
    if not idx:
        return out
    if len(idx) == 1:
        out[idx[0]] = rest
        return out
    m = osp["means"]
    agesum = pop * mean_age - sum(groups[i] * m[i] for i in range(3) if i not in idx)
    if len(idx) == 2:
        i, j = idx
        xi = (agesum - rest * m[j]) / (m[i] - m[j])
        vals = {i: xi, j: rest - xi}
    else:
        p = [osp["shares"][i] * rest for i in range(3)]
        # Lagrange: x = p + l1 + l2*m, with sum x = rest and sum x*m = agesum
        s1, sm, smm = 3.0, sum(m), sum(v * v for v in m)
        r1, r2 = rest - sum(p), agesum - sum(pi * mi for pi, mi in zip(p, m))
        det = s1 * smm - sm * sm
        l1, l2 = (r1 * smm - r2 * sm) / det, (s1 * r2 - sm * r1) / det
        vals = {i: p[i] + l1 + l2 * m[i] for i in range(3)}
    vals = {i: min(9.0, max(1.0, v)) for i, v in vals.items()}
    for _ in range(100):
        gap = rest - sum(vals.values())
        if abs(gap) < 1e-9:
            break
        movable = [i for i in vals if (vals[i] < 9 if gap > 0 else vals[i] > 1)]
        for i in movable:
            vals[i] = min(9.0, max(1.0, vals[i] + gap / len(movable)))
    for i, v in vals.items():
        out[i] = v
    return out


def census():
    """Kauno m. cells with estimated age groups (+ feasible bounds); other-label cells for K15."""
    osp = osp_ages()
    kaunas, other = [], []
    with open(f"{GEO_RAW}/GyvSur100M2021.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m = re.fullmatch(r"100x100mX(\d+)Y(\d+)", r["grid_id"])
            e, n = int(m.group(1)) * 10, int(m.group(2)) * 100
            cell = box(e, n, e + 100, n + 100)
            pop = int(r["pop"])
            if r["gyv_pav"] != "Kauno m.":
                if 480000 < e < 515000 and 6070000 < n < 6095000:   # coarse Kaunas window, LKS-94
                    other.append((cell, pop, r["gyv_pav"]))
                continue
            g = [int(r["all_00_14"]), int(r["all_15_64"]), int(r["all_65_plius"])]
            masked = [i for i in range(3) if g[i] == 9]
            rest = pop - sum(g[i] for i in range(3) if i not in masked)
            lo, hi = list(map(float, g)), list(map(float, g))
            if masked:
                k = len(masked)
                for i in masked:
                    lo[i] = max(1, rest - 9 * (k - 1))
                    hi[i] = min(9, rest - (k - 1))
            est = mean_age_fill(g, pop, float(r["mean_age"]), osp)
            assert abs(sum(est) - pop) < 1e-6
            kaunas.append({"g": cell, "pop": pop, "a014": est[0], "a65": est[2],
                           "a014_lo": lo[0], "a014_hi": hi[0], "a65_lo": lo[2], "a65_hi": hi[2]})
    return kaunas, other, osp


def allocate(cells, units):
    acc = {u["id"]: Counter() for u in units}
    orphans = Counter()
    for c in cells:
        b = c["g"].bounds
        parts = []
        for u in units:
            ub = u["bb"]
            if ub[2] < b[0] or ub[0] > b[2] or ub[3] < b[1] or ub[1] > b[3]:
                continue
            if u["p"].contains(c["g"]):
                parts = [(u["id"], 1.0)]
                break
            a = u["g"].intersection(c["g"]).area
            if a > 0:
                parts.append((u["id"], a))
        if not parts:
            near = min(units, key=lambda u: u["g"].distance(c["g"]))
            parts = [(near["id"], 1.0)]
            orphans["cells"] += 1
            orphans["pop"] += c["pop"]
        tot = sum(a for _, a in parts)
        for uid, a in parts:
            w = a / tot
            for k in ("pop", "a014", "a65"):
                acc[uid][k] += c[k] * w
    return acc, dict(orphans)


# ---------------------------------------------------------------- numerators
def smir_points():
    """F-064 rule re-implemented from the raw SMIR shapefile (2022-08-30)."""
    sf = shapefile.Reader(f"{RAW}/svietimo_istaigos/SMIR_istaigos.shp", encoding="cp1257",
                          encodingErrors="replace")
    out = {}
    for sr in sf.iterShapeRecords():
        r = sr.record
        if r["Sav_kodas"] != KAUNAS_SMIR or r["Isreg_data"] or not sr.shape.points:
            continue
        purpose = (r["Pagr_paskr"] or "") + " " + (r["Paskirtis"] or "")
        preschool = ("darželis" in purpose.lower() or "ikimokyklinio ugdymo grupės" in purpose.lower()
                     or ("Ikimokyklinio ugdymo mokykla" in r["Tipas"]
                         and r["Grupe"] not in ("Aukštoji mokykla", "Kitas švietimo teikėjas")))
        general = r["Grupe"] == "Bendrojo ugdymo mokykla"
        if preschool or general:
            x, y = sr.shape.points[0]
            out.setdefault(r["Inst_kodas"], []).append((Point(x, y), preschool, general))
    return out


def osm_elements(layer):
    return jload(f"{RAW}/osm2/{layer}.json")["elements"]


def osm_pt(e):
    if e["type"] == "node":
        return Point(wgs2lks(e["lon"], e["lat"]))
    if "center" in e:
        return Point(wgs2lks(e["center"]["lon"], e["center"]["lat"]))
    return None


def dedupe(items):
    """items: (cat, name, pt). Same cat + same non-empty name within 100 m -> once."""
    kept = []
    for cat, name, p in items:
        if name and any(c == cat and n == name and p.distance(q) < 100 for c, n, q in kept):
            continue
        kept.append((cat, name, p))
    return kept


def osm_points():
    items = []
    for e in osm_elements("sveikata"):
        t = e.get("tags", {})
        p = osm_pt(e)
        if p is None:
            continue
        cat = "vaistine" if "pharmacy" in (t.get("amenity"), t.get("healthcare")) else "sveikata"
        items.append((cat, (t.get("name") or "").strip().lower(), p))
    health = dedupe(items)
    items = []
    for e in osm_elements("sportas"):
        t = e.get("tags", {})
        p = osm_pt(e)
        if p is None or t.get("access") in ("private", "customers", "no"):
            continue
        lei = t.get("leisure")
        cat = {"sports_centre": "sportas", "sports_hall": "sportas", "stadium": "sportas", "pitch": "sportas",
               "track": "sportas", "fitness_station": "sportas", "playground": "aikstele"}.get(lei)
        if cat:
            items.append((cat, (t.get("name") or "").strip().lower(), p))
    sport = dedupe(items)
    return health, sport, len(health), len(sport)


def green_union():
    polys = []
    for e in osm_elements("zaluma"):
        if e["type"] == "way":
            pts = e.get("geometry") or []
            if len(pts) >= 4 and (pts[0]["lon"], pts[0]["lat"]) == (pts[-1]["lon"], pts[-1]["lat"]):
                polys.append(make_valid(Polygon([wgs2lks(q["lon"], q["lat"]) for q in pts])))
        elif e["type"] == "relation":
            rings = {"outer": [], "inner": []}
            for m in e.get("members", []):
                if m.get("type") == "way" and m.get("geometry"):
                    rings["inner" if m.get("role") == "inner" else "outer"].append(
                        LineString([wgs2lks(q["lon"], q["lat"]) for q in m["geometry"]]))
            if not rings["outer"]:
                continue
            outer = unary_union(list(polygonize(unary_union(rings["outer"]))))
            if rings["inner"]:
                outer = outer.difference(unary_union(list(polygonize(unary_union(rings["inner"])))))
            if not outer.is_empty:
                polys.append(make_valid(outer))
    return unary_union([p for p in polys if p.area > 0])


def street_lines():
    foot = {"footway", "path", "pedestrian", "living_street", "cycleway"}
    road = {"residential", "service", "tertiary", "unclassified"}
    out = []
    for e in osm_elements("gatves"):
        hw = e.get("tags", {}).get("highway")
        g = e.get("geometry") or []
        if len(g) > 1 and (hw in foot or hw in road):
            out.append(("takai" if hw in foot else "gatves", LineString([wgs2lks(q["lon"], q["lat"]) for q in g])))
    return out


def apva_points():
    out = []
    for f in jload(f"{RAW}/apva_renomap/RenomapProjects_view.json")["features"]:
        st = f["attributes"]["status"]
        out.append(("baigta" if st == 2 else "vykdoma" if st == 1 else f"kita{st}",
                    Point(wgs2lks(f["geometry"]["x"], f["geometry"]["y"]))))
    for f in jload(f"{RAW}/apva_renomap/RenomapProjectsPotential_view.json")["features"]:
        out.append(("potencialus", Point(wgs2lks(f["geometry"]["x"], f["geometry"]["y"]))))
    return out


def social_points():
    out = []
    with open("data/processed/social_facilities.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append((r["name"], Point(wgs2lks(float(r["lon"]), float(r["lat"])))))
    return out


# ---------------------------------------------------------------- main
def main():
    sen, apl, city = load_units()
    levels = {"seniunijos": sen, "apylinkes": apl}
    summary = {"vienetai": {"seniunijos": len(sen), "apylinkes": len(apl)}}

    cells, other, osp = census()
    summary["census"] = {"kauno_m_langeliai": len(cells), "kauno_m_gyv": sum(c["pop"] for c in cells)}
    lo014 = sum(c["a014_lo"] for c in cells); hi014 = sum(c["a014_hi"] for c in cells)
    lo65 = sum(c["a65_lo"] for c in cells); hi65 = sum(c["a65_hi"] for c in cells)
    P = summary["census"]["kauno_m_gyv"]
    summary["census"]["0_14_intervalas"] = [round(lo014), round(hi014),
                                            round(100 * lo014 / P, 2), round(100 * hi014 / P, 2)]
    summary["census"]["65_intervalas"] = [round(lo65), round(hi65), round(100 * lo65 / P, 2), round(100 * hi65 / P, 2)]
    summary["census"]["osp_2021_01_01"] = {"gyv": osp["tot"], "0_14": osp["n"][0], "65": osp["n"][2],
                                           "0_14_proc": round(100 * osp["shares"][0], 2),
                                           "65_proc": round(100 * osp["shares"][2], 2)}
    summary["census"]["ivertis_0_14"] = [round(sum(c["a014"] for c in cells)), round(100 * sum(c["a014"] for c in cells) / P, 2)]
    summary["census"]["ivertis_65"] = [round(sum(c["a65"] for c in cells)), round(100 * sum(c["a65"] for c in cells) / P, 2)]
    cp = prep(city)
    oth_in = [(c, p, lab) for c, p, lab in other if cp.intersects(c)]
    summary["census"]["kiti_langeliai_mieste"] = {
        "langeliai": len(oth_in), "gyv_pilni_langeliai": sum(p for _, p, _ in oth_in),
        "gyv_ploto_dalimi": round(sum(p * c.intersection(city).area / c.area for c, p, _ in oth_in)),
        "pavadinimai": dict(Counter(lab for _, _, lab in oth_in).most_common(10))}
    kaunas_out = [c for c in cells if not cp.intersects(c["g"])]
    summary["census"]["kauno_m_langeliai_uz_miesto_ribos"] = {"langeliai": len(kaunas_out),
                                                               "gyv": sum(c["pop"] for c in kaunas_out)}

    den = {}
    for lvl, units in levels.items():
        acc, orph = allocate(cells, units)
        summary["census"][f"orphans_{lvl}"] = orph
        den[lvl] = {uid: {} for uid in acc}
        for src, dst in (("pop", "gyv"), ("a014", "vaik"), ("a65", "s65")):
            # integers that add up to the rounded total: hand out the leftover units to the
            # largest fractional parts
            ids = list(acc)
            base = {u: int(acc[u][src] // 1) for u in ids}
            left = round(sum(acc[u][src] for u in ids)) - sum(base.values())
            for u in sorted(ids, key=lambda u: acc[u][src] - base[u], reverse=True)[:left]:
                base[u] += 1
            for u in ids:
                den[lvl][u][dst] = base[u]

    # numerators
    cnt = {lvl: defaultdict(Counter) for lvl in levels}
    city_cnt = Counter()

    def add(key, p, w=1):
        if cp.covers(p):
            city_cnt[key] += w
        for lvl, units in levels.items():
            uid = locate(units, p)
            if uid:
                cnt[lvl][uid][key] += w

    smir = smir_points()
    summary["smir"] = {"istaigos": len(smir),
                       "darzeliai": sum(1 for v in smir.values() if any(x[1] for x in v)),
                       "bendrojo": sum(1 for v in smir.values() if any(x[2] for x in v)),
                       "kodai_su_keliais_taskais": sum(1 for v in smir.values() if len(v) > 1)}
    for code, recs in smir.items():
        seen = {lvl: set() for lvl in levels}
        seen_city = False
        for p, _, _ in recs:
            if cp.covers(p) and not seen_city:
                city_cnt["vaikai"] += 1
                seen_city = True
            for lvl, units in levels.items():
                uid = locate(units, p)
                if uid and uid not in seen[lvl]:
                    seen[lvl].add(uid)
                    cnt[lvl][uid]["vaikai"] += 1

    health, sport, nh, ns = osm_points()
    for cat, _, p in health:
        add("sveikata", p)
    for cat, _, p in sport:
        add("sportas" if cat == "sportas" else "aikstele", p)
    for cat, p in apva_points():
        add("reno_" + cat, p)
    soc = social_points()
    soc_loc = []
    for name, p in soc:
        add("socialines", p)
        soc_loc.append((name, locate(sen, p)))
    summary["socialiniai_taskai_seniunijose"] = [
        (n, next((u["name"] for u in sen if u["id"] == s), None)) for n, s in soc_loc]

    green = green_union()
    summary["zaluma_sajunga_ha_bbox"] = round(green.area / 1e4, 1)
    city_cnt["zaluma_m2"] = city.intersection(green).area
    for lvl, units in levels.items():
        for u in units:
            cnt[lvl][u["id"]]["zaluma_m2"] = u["g"].intersection(green).area
            cnt[lvl][u["id"]]["plotas_m2"] = u["g"].area
    city_cnt["plotas_m2"] = city.area
    for kind, ln in street_lines():
        lb = ln.bounds
        city_cnt[kind + "_m"] += city.intersection(ln).length
        for lvl, units in levels.items():
            for u in units:
                ub = u["bb"]
                if ub[2] < lb[0] or ub[0] > lb[2] or ub[3] < lb[1] or ub[1] > lb[3]:
                    continue
                cnt[lvl][u["id"]][kind + "_m"] += u["g"].intersection(ln).length

    # ------------------------------------------------ indicators (independent formulas)
    def ind(c, d, is_city):
        gyv, vaik, s65 = d["gyv"], d["vaik"], d["s65"]
        reno_den = c["reno_baigta"] + c["reno_vykdoma"] + c["reno_potencialus"]
        street_km = c["gatves_m"] / 1000
        r = {
            "vaikai": round(1000 * c["vaikai"] / vaik, 2) if vaik else None,
            "sveikata": round(10000 * c["sveikata"] / gyv, 2) if gyv else None,
            "socialines_paslaugos": c["socialines"],
            "zalieji_plotai": round(c["zaluma_m2"] / gyv, 1) if gyv else None,
            "zalieji_plotai_dalis": round(100 * c["zaluma_m2"] / c["plotas_m2"], 1),
            "pesciuju_takai": round(c["takai_m"] / c["gatves_m"], 2) if c["gatves_m"] else None,
            "renovacija": round(100 * c["reno_baigta"] / reno_den, 1) if reno_den else None,
            "renovacija_pradeta": round(100 * (c["reno_baigta"] + c["reno_vykdoma"]) / reno_den, 1) if reno_den else None,
            "sportas": round(10000 * c["sportas"] / gyv, 2) if gyv else None,
            "zaidimu_aiksteles": round(1000 * c["aikstele"] / vaik, 2) if vaik else None,
            "vaiku_dalis": round(100 * vaik / gyv, 1) if gyv else None,
            "senjoru_dalis": round(100 * s65 / gyv, 1) if gyv else None,
            "tankis": round(gyv / (c["plotas_m2"] / 1e6)) if gyv else None,
        }
        k = {"vaikai": c["vaikai"], "sveikata": c["sveikata"], "socialines_paslaugos": c["socialines"],
             "zalieji_plotai": round(c["zaluma_m2"] / 1e4, 1), "zalieji_plotai_dalis": round(c["zaluma_m2"] / 1e4, 1),
             "pesciuju_takai": round(c["takai_m"] / 1000, 1), "renovacija": c["reno_baigta"],
             "renovacija_pradeta": c["reno_baigta"] + c["reno_vykdoma"], "sportas": c["sportas"],
             "zaidimu_aiksteles": c["aikstele"], "vaiku_dalis": vaik, "senjoru_dalis": s65, "tankis": gyv}
        if not is_city:
            for i in ("sveikata", "zalieji_plotai", "sportas", "vaiku_dalis", "senjoru_dalis"):
                if gyv < T_POP:
                    r[i] = None
            for i in ("vaikai", "zaidimu_aiksteles"):
                if vaik < T_KIDS:
                    r[i] = None
            for i in ("renovacija", "renovacija_pradeta"):
                if reno_den < T_RENO:
                    r[i] = None
            if street_km < T_STREET_KM:
                r["pesciuju_takai"] = None
        return r, k, reno_den

    site = jload(SITE)
    diffs, null_counts, n_cmp = [], {}, 0
    # exact match required, except green space (polygon repair can differ by < 0.1)
    tol = {"zalieji_plotai": 0.1, "zalieji_plotai_dalis": 0.1, "tankis": 1}  # tankis: area rounded to 1e-5 km2 in 15
    mine = {}
    for lvl, units in levels.items():
        null_counts[lvl] = Counter()
        for u in units:
            r, k, rd = ind(cnt[lvl][u["id"]], den[lvl][u["id"]], False)
            mine.setdefault(lvl, {})[u["id"]] = {"r": r, "k": k, "reno_den": rd, **den[lvl][u["id"]]}
            sv = site["reiksmes"][lvl][u["id"]]
            for i in SHOWN:
                n_cmp += 1
                a, b = r[i], sv[i]["reiksme"]
                if a is None:
                    null_counts[lvl][i] += 1
                ka, kb = k[i], sv[i]["kiekis"]
                if (a is None) != (b is None) or (a is not None and abs(a - b) > tol.get(i, 0) + 1e-9) \
                        or abs(ka - kb) > (0.1 if isinstance(ka, float) else 0) + 1e-9:
                    diffs.append({"lygis": lvl, "id": u["id"], "vienetas": u["name"], "rodiklis": i,
                                  "patikra": a, "svetaine": b, "patikra_kiekis": k[i], "svetaine_kiekis": sv[i]["kiekis"]})
    # city: direct count inside municipality polygon, census total of Kauno m. cells
    cden = {"gyv": P, "vaik": round(sum(c["a014"] for c in cells)), "s65": round(sum(c["a65"] for c in cells))}
    cr, ck, crd = ind(city_cnt, cden, True)
    for i in SHOWN:
        n_cmp += 1
        a, b = cr[i], site["miestas"][i]["reiksme"]
        ka, kb = ck[i], site["miestas"][i]["kiekis"]
        if a is None or b is None or abs(a - b) > tol.get(i, 0) + 1e-9 \
                or abs(ka - kb) > (0.1 if isinstance(ka, float) else 0) + 1e-9:
            diffs.append({"lygis": "miestas", "id": "miestas", "vienetas": "Kauno m.", "rodiklis": i,
                          "patikra": a, "svetaine": b, "patikra_kiekis": ck[i], "svetaine_kiekis": site["miestas"][i]["kiekis"]})
    summary["miestas_patikra"] = {i: {"reiksme": cr[i], "kiekis": ck[i]} for i in SHOWN}
    summary["miestas_patikra"]["_vardikliai"] = {**cden, "reno_vardiklis": crd, "plotas_km2": round(city.area / 1e6, 3),
                                                 "gatviu_km": round(city_cnt["gatves_m"] / 1000, 1)}

    # sums: seniunijos == apylinkes == city (counts)
    sums = {}
    for key in ("vaikai", "sveikata", "socialines", "sportas", "aikstele", "reno_baigta", "reno_vykdoma",
                "reno_potencialus", "zaluma_m2", "takai_m", "gatves_m"):
        s = sum(cnt["seniunijos"][u["id"]][key] for u in sen)
        a = sum(cnt["apylinkes"][u["id"]][key] for u in apl)
        sums[key] = [round(s, 1), round(a, 1), round(city_cnt[key], 1)]
    for key in ("gyv", "vaik", "s65"):
        sums["den_" + key] = [sum(d[key] for d in den["seniunijos"].values()),
                              sum(d[key] for d in den["apylinkes"].values()), cden[key]]
    summary["sumos_seniunijos_apylinkes_miestas"] = sums
    summary["osm_po_dublikatu"] = {"sveikata_vaistines": nh, "sportas_ir_aiksteles": ns}
    summary["null_patikra"] = {lvl: dict(c) for lvl, c in null_counts.items()}
    summary["palyginta_reiksmiu"] = n_cmp
    summary["skirtumu"] = len(diffs)

    # spot-check table for the report: 2 seniunijos + 2 apylinkes + Zemosios Fredos
    spot = {}
    for lvl, ids in (("seniunijos", ["1911", "9508", "9505"]), ("apylinkes", None)):
        if ids is None:
            ids = sorted(mine["apylinkes"], key=lambda x: -mine["apylinkes"][x]["gyv"])[:1] + \
                  sorted(mine["apylinkes"], key=lambda x: mine["apylinkes"][x]["gyv"])[:1]
        for uid in ids:
            m = mine[lvl][uid]
            spot[f"{lvl}:{uid}"] = {"gyv": m["gyv"], "vaik": m["vaik"], "s65": m["s65"], "reno_den": m["reno_den"],
                                    "r": m["r"], "k": m["k"],
                                    "svetaine": {i: site["reiksmes"][lvl][uid][i] for i in SHOWN}}
    summary["spot"] = spot

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["lygis", "id", "vienetas", "rodiklis", "patikra", "svetaine",
                                          "patikra_kiekis", "svetaine_kiekis"])
        w.writeheader()
        w.writerows(diffs)
    print(json.dumps({k: v for k, v in summary.items() if k != "spot"}, ensure_ascii=False, indent=1))
    print(f"compared {n_cmp} values, {len(diffs)} differences -> {OUT_CSV}")


if __name__ == "__main__":
    main()
