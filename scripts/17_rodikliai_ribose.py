"""
Stage 2 of the atlas rebuild: indicators per seniunija and per rinkimu apylinke,
by real boundaries (point-in-polygon; polygons and lines clipped), per resident.

Inputs
  units + denominators   data/processed/geo/{seniunijos,apylinkes}.csv (script 15);
                         full-resolution geometry rebuilt with script 15's own loaders
  vaikai                 data/processed/education_facilities.csv (script 03, flags of 2026-10-19)
  sveikata, kultura,     data/raw/osm2/*.json (script 16)
  sportas, zaluma, gatves
  socialines paslaugos   data/processed/social_facilities.csv (15 points, 2026-09-14) checked
                         against data/raw/osm2/kaunospc_adresai.json
  renovacija             data/raw/apva_renomap/*.json (script 12)

Rules
  - Point features: counted in the unit whose polygon contains the point (EPSG:3346).
    OSM node+way duplicates of the same object (same category and same name within
    100 m) are counted once; unnamed objects are all counted.
  - Education: each inst_kodas counted once per unit.
  - Green space: union of all green polygons (removes double counting of overlaps,
    e.g. grass inside a park), clipped to each unit; reported in ha and m2 per resident.
  - Footway / street ratio: lines clipped to each unit, lengths summed by kind.
  - Rates use 2021 census population (script 15): per 10 000 residents; children's
    infrastructure and playgrounds per 1 000 residents aged 0-14.
  - Units with a 2021 population below SMALL_POP are flagged (rates unstable).

Outputs (data/processed/geo/)
  rodikliai_seniunijos.csv, rodikliai_apylinkes.csv   counts + rates
  rodikliai.json                                       web bundle (meta + both levels + city)
  rodikliai_patikra.json                               totals / checks
"""
import csv
import importlib.util
import json
import math
import os
import re
import sys
from collections import Counter

from pyproj import Transformer
from shapely import STRtree, make_valid
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, polygonize, unary_union

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "geo15", os.path.join(os.path.dirname(os.path.abspath(__file__)), "15_geografija_kaunas.py"))
geo15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geo15)

OUT = "data/processed/geo"
OSM = "data/raw/osm2"
APVA = "data/raw/apva_renomap"
SMALL_POP = 1000
to_lks = Transformer.from_crs(4326, 3346, always_xy=True).transform

FOOTWAY = {"footway", "path", "pedestrian", "living_street", "cycleway"}
ROAD = {"residential", "service", "tertiary", "unclassified"}
NOT_PUBLIC = {"private", "customers", "no"}


def pt(lon, lat):
    return Point(to_lks(lon, lat))


def osm_points(layer, classify):
    """classify(tags) -> category or None. Returns list of (category, name, Point)."""
    with open(os.path.join(OSM, f"{layer}.json"), encoding="utf-8") as f:
        els = json.load(f)["elements"]
    out = []
    for e in els:
        tags = e.get("tags", {})
        cat = classify(tags)
        if not cat:
            continue
        if e["type"] == "node":
            lon, lat = e["lon"], e["lat"]
        elif "center" in e:
            lon, lat = e["center"]["lon"], e["center"]["lat"]
        else:
            continue
        out.append((cat, (tags.get("name") or "").strip().lower(), pt(lon, lat)))
    # dedupe node/way duplicates: same category + same non-empty name within 100 m
    keep = []
    for c, n, p in out:
        if n and any(c == c2 and n == n2 and p.distance(p2) < 100 for c2, n2, p2 in keep):
            continue
        keep.append((c, n, p))
    return keep, len(out) - len(keep)


def cls_health(t):
    if t.get("amenity") == "pharmacy" or t.get("healthcare") == "pharmacy":
        return "vaistines"
    return "sveikatos_istaigos"


def cls_culture(t):
    if t.get("amenity") == "library":
        return "bibliotekos"
    if t.get("tourism") == "museum":
        return "muziejai"
    if t.get("amenity") in ("theatre", "cinema", "arts_centre", "community_centre"):
        return "kulturos_centrai_teatrai_kinai"
    return None


def cls_sport(t):
    lei = t.get("leisure")
    if t.get("access") in NOT_PUBLIC:
        return None
    if lei in ("sports_centre", "sports_hall", "stadium"):
        return "sporto_centrai"
    if lei in ("pitch", "track", "fitness_station"):
        return "lauko_aikstynai"
    if lei == "playground":
        return "zaidimu_aikst"
    return None  # swimming_pool: 52 objects, none named, 25 tagged private - excluded


def green_geometry():
    with open(os.path.join(OSM, "zaluma.json"), encoding="utf-8") as f:
        els = json.load(f)["elements"]
    polys = []
    for e in els:
        if e["type"] == "way" and e.get("geometry"):
            c = [to_lks(p["lon"], p["lat"]) for p in e["geometry"]]
            if len(c) >= 4 and c[0] == c[-1]:
                polys.append(make_valid(Polygon(c)))
        elif e["type"] == "relation":
            outer, inner = [], []
            for m in e.get("members", []):
                if m.get("type") == "way" and m.get("geometry"):
                    ls = LineString([to_lks(p["lon"], p["lat"]) for p in m["geometry"]])
                    (inner if m.get("role") == "inner" else outer).append(ls)
            o = unary_union(list(polygonize(linemerge(outer)))) if outer else None
            if o is None or o.is_empty:
                continue
            if inner:
                i = unary_union(list(polygonize(linemerge(inner))))
                o = o.difference(i)
            polys.append(make_valid(o))
    polys = [p for p in polys if p.area > 0]
    return unary_union(polys), len(polys)


def street_lines():
    with open(os.path.join(OSM, "gatves.json"), encoding="utf-8") as f:
        els = json.load(f)["elements"]
    out = []
    for e in els:
        hw = e.get("tags", {}).get("highway")
        kind = "pesciuju" if hw in FOOTWAY else ("gatviu" if hw in ROAD else None)
        if kind and e.get("geometry") and len(e["geometry"]) > 1:
            out.append((kind, LineString([to_lks(p["lon"], p["lat"]) for p in e["geometry"]])))
    return out


def apva_points():
    out = []
    for layer, cat in (("RenomapProjects_view", None), ("RenomapProjectsPotential_view", "potencialus"),
                       ("RenomapProjectsOldPrograms_view", "senos_programos")):
        with open(os.path.join(APVA, f"{layer}.json"), encoding="utf-8") as f:
            for ft in json.load(f)["features"]:
                c = cat or {2: "baigta", 1: "vykdoma"}[ft["attributes"]["status"]]
                out.append((c, pt(ft["geometry"]["x"], ft["geometry"]["y"])))
    return out


def education_points():
    out = []
    with open("data/processed/education_facilities.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append((r["inst_kodas"], r["ikimokyklinis"] == "1", r["bendrojo_ugdymo"] == "1",
                        pt(float(r["lon"]), float(r["lat"]))))
    return out


def social_points():
    with open(os.path.join(OSM, "kaunospc_adresai.json"), encoding="utf-8") as f:
        current = {re.sub(r"[\s.]", "", a).lower() for a in json.load(f)["addresses"]}
    out = []
    with open("data/processed/social_facilities.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = re.sub(r"[\s.]", "", r["address"].split(",")[0]).lower()
            out.append((r["name"], key in current, pt(float(r["lon"]), float(r["lat"]))))
    return out


def unit_index(units, p):
    for i in units["tree"].query(p, predicate="intersects"):
        return int(i)
    return None


def build_units():
    levels = {}
    for lvl, loader, key in (("seniunijos", geo15.seniunijos, "id"), ("apylinkes", geo15.apylinkes, "id")):
        geoms = loader()
        with open(os.path.join(OUT, f"{lvl}.csv"), encoding="utf-8") as f:
            den = {r["id"]: r for r in csv.DictReader(f)}
        rows = []
        for g in geoms:
            d = den[g[key]]
            rows.append({"id": g[key], "pavadinimas": g["pavadinimas"], "geom": g["geom"],
                         "gyv": int(d["gyventojai_2021"]), "gyv_0_14": int(d["gyv_0_14"]),
                         "gyv_65": int(d["gyv_65_plius"]), "c": Counter(), "m": Counter()})
        levels[lvl] = {"rows": rows, "tree": STRtree([r["geom"] for r in rows])}
    return levels


def rate(n, den, per):
    return round(n / den * per, 2) if den else None


def main():
    L = build_units()
    checks = {}

    health, d_h = osm_points("sveikata", cls_health)
    culture, d_c = osm_points("kultura", cls_culture)
    sport, d_s = osm_points("sportas", cls_sport)
    checks["osm_dedup_removed"] = {"sveikata": d_h, "kultura": d_c, "sportas": d_s}
    edu = education_points()
    soc = social_points()
    checks["socialiniai_taskai"] = {"is_viso": len(soc), "rasti_kaunospc_2026_10_19": sum(1 for s in soc if s[1]),
                                    "nerasti": [s[0] for s in soc if not s[1]]}
    apva = apva_points()
    green, n_green = green_geometry()
    lines = street_lines()
    checks["zaluma_poligonu"] = n_green
    checks["zaluma_sajunga_ha"] = round(green.area / 1e4, 1)

    for lvl, U in L.items():
        rows = U["rows"]
        seen_inst = [set() for _ in rows]
        outside = Counter()
        for cat, _, p in health + culture + sport:
            i = unit_index(U, p)
            if i is None:
                outside[cat] += 1
            else:
                rows[i]["c"][cat] += 1
        for code, pre, gen, p in edu:
            i = unit_index(U, p)
            if i is None:
                outside["svietimas"] += 1
                continue
            if (pre or gen) and code not in seen_inst[i]:
                seen_inst[i].add(code)
                rows[i]["c"]["vaikai_viso"] += 1
                rows[i]["c"]["darzeliai"] += int(pre)
                rows[i]["c"]["bendrojo_ugdymo_mokyklos"] += int(gen)
        for name, current, p in soc:
            i = unit_index(U, p)
            if i is not None:
                rows[i]["c"]["socialiniu_paslaugu_taskai"] += 1
        for cat, p in apva:
            i = unit_index(U, p)
            if i is None:
                outside["apva"] += 1
            else:
                rows[i]["c"]["reno_" + cat] += 1
        for r in rows:
            r["m"]["zaluma_m2"] = r["geom"].intersection(green).area
        for kind, ln in lines:
            for i in U["tree"].query(ln, predicate="intersects"):
                rows[int(i)]["m"][kind + "_m"] += rows[int(i)]["geom"].intersection(ln).length
        checks[f"{lvl}_uz_ribu"] = dict(outside)

    # tables
    def table(rows, label_city=None):
        out = []
        for r in rows:
            c, m, gyv, vaik = r["c"], r["m"], r["gyv"], r["gyv_0_14"]
            reno_den = c["reno_baigta"] + c["reno_vykdoma"] + c["reno_potencialus"]
            out.append({
                "id": r["id"], "pavadinimas": r["pavadinimas"], "plotas_km2": round(r["geom"].area / 1e6, 3),
                "gyventojai_2021": gyv, "gyv_0_14_2021": vaik, "gyv_65_plius_2021": r["gyv65"] if "gyv65" in r else r["gyv_65"],
                "mazas_vardiklis": "taip" if gyv < SMALL_POP else "ne",
                "vaikai_istaigos": c["vaikai_viso"], "darzeliai": c["darzeliai"],
                "bendrojo_ugdymo_mokyklos": c["bendrojo_ugdymo_mokyklos"],
                "vaikai_istaigos_1000_vaiku": rate(c["vaikai_viso"], vaik, 1000),
                "sveikatos_istaigos": c["sveikatos_istaigos"], "vaistines": c["vaistines"],
                "sveikata_10000_gyv": rate(c["sveikatos_istaigos"] + c["vaistines"], gyv, 10000),
                "socialiniu_paslaugu_taskai": c["socialiniu_paslaugu_taskai"],
                "bibliotekos": c["bibliotekos"], "muziejai": c["muziejai"],
                "kulturos_centrai_teatrai_kinai": c["kulturos_centrai_teatrai_kinai"],
                "kultura_10000_gyv": rate(c["bibliotekos"] + c["muziejai"] + c["kulturos_centrai_teatrai_kinai"],
                                          gyv, 10000),
                "sporto_centrai": c["sporto_centrai"], "lauko_aikstynai": c["lauko_aikstynai"],
                "sportas_10000_gyv": rate(c["sporto_centrai"] + c["lauko_aikstynai"], gyv, 10000),
                "zaidimu_aikst": c["zaidimu_aikst"],
                "zaidimu_aikst_1000_vaiku": rate(c["zaidimu_aikst"], vaik, 1000),
                "zaluma_ha": round(m["zaluma_m2"] / 1e4, 1),
                "zaluma_m2_gyv": round(m["zaluma_m2"] / gyv, 1) if gyv else None,
                "zaluma_ploto_dalis_proc": round(100 * m["zaluma_m2"] / r["geom"].area, 1),
                "pesciuju_km": round(m["pesciuju_m"] / 1000, 1), "gatviu_km": round(m["gatviu_m"] / 1000, 3),
                "pesciuju_gatviu_santykis": round(m["pesciuju_m"] / m["gatviu_m"], 2) if m["gatviu_m"] else None,
                "reno_baigta": c["reno_baigta"], "reno_vykdoma": c["reno_vykdoma"],
                "reno_potencialus": c["reno_potencialus"], "reno_vardiklis": reno_den,
                "reno_baigta_proc": round(100 * c["reno_baigta"] / reno_den, 1) if reno_den else None,
                "reno_baigta_vykdoma_proc": round(100 * (c["reno_baigta"] + c["reno_vykdoma"]) / reno_den, 1)
                if reno_den else None,
                "reno_senos_programos": c["reno_senos_programos"],
            })
        return out

    result = {}
    for lvl, U in L.items():
        rows = U["rows"]
        city = {"id": "miestas", "pavadinimas": "Kauno m. (iš viso)", "geom": unary_union([r["geom"] for r in rows]),
                "gyv": sum(r["gyv"] for r in rows), "gyv_0_14": sum(r["gyv_0_14"] for r in rows),
                "gyv_65": sum(r["gyv_65"] for r in rows), "c": sum((r["c"] for r in rows), Counter()),
                "m": sum((r["m"] for r in rows), Counter())}
        tab = table(rows + [city])
        result[lvl] = tab
        with open(os.path.join(OUT, f"rodikliai_{lvl}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(tab[0].keys()))
            w.writeheader()
            w.writerows(tab)

    s_city, a_city = result["seniunijos"][-1], result["apylinkes"][-1]
    for k, v in s_city.items():
        if isinstance(v, (int, float)) and k not in ("plotas_km2",) and not k.endswith(("_proc", "_gyv", "_vaiku", "santykis")):
            checks.setdefault("miestas_seniunijos_vs_apylinkes", {})[k] = [v, a_city[k]]
    meta = {
        "sukurta": "scripts/17_rodikliai_ribose.py",
        "vardikliai": "2021 m. surašymo 100 m gardelė (VDA, data.gov.lt/datasets/1911), paskirstyta pagal ribas (script 15)",
        "ribos": {"seniunijos": "Registrų centras, Adresų registras (CC BY 4.0)",
                  "apylinkes": "VRK, 2024 m. Seimo rinkimų apylinkių ribos (rinkimai-vrk.opendata.arcgis.com)"},
        "mazas_vardiklis_jei_gyv_maziau": SMALL_POP,
    }
    with open(os.path.join(OUT, "rodikliai.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, **result}, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "rodikliai_patikra.json"), "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=1)
    print(json.dumps(checks, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
