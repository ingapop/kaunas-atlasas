"""
Stage 1 of the atlas rebuild: Kaunas city geography + denominators.

Reads data/raw/geografija/ (script 14). Writes data/processed/geo/:
  seniunijos.geojson / apylinkes.geojson   web layers: WGS84, simplified (SIMPLIFY_M),
                                           coordinates rounded to 5 decimals (~1 m)
  seniunijos.csv / apylinkes.csv           denominators per unit (no geometry)
  apylinke_seniunija.csv                   crosswalk: area and 2021-population share of each
                                           apylinke in each seniunija
  geografija_patikra.json                  checks and totals

Units
  seniunijos  - Registru centras, Adresu registras (SAV_KODAS 19 = Kauno miesto sav.), 9 units.
  apylinkes   - VRK polygons of the 2024-10-13 Seimas election (MUNICIPALITY = Kauno miesto),
                90 units; joined to VRK publishing-API registered voters by AREA2_ID = rpl_id.

Population denominator (2021 census, 100 m grid, VDA, data.gov.lt/datasets/1911)
  - Cell square = lower-left corner (easting = X*10, northing = Y*100 in LKS-94); checked:
    fewer people fall outside the city polygon than with a centre anchor.
  - Cell population is split between units by the share of the 100 m square inside each
    unit (areal weighting). Cells labelled "Kauno m." whose square lies entirely outside the
    city polygon go to the nearest unit (reported).
  - Age groups: in the grid, values 1-9 of a sub-group are published as 9 (verified: every
    cell without a "9" has 0-14 + 15-64 + 65+ == pop exactly; for cells with "9" the
    remainder pop - known groups always lies within [n9, 9*n9]).
    One masked group -> its value is the remainder (exact).
    Two or three masked groups -> estimated from the cell's published mean age (2026-10-20;
    until then: equal split of the remainder, which overstated 0-14 by ~2 100 and 65+ by
    ~1 300 at city level against OSP - see CHANGELOG):
      x = argmin |x - p|^2  s.t.  sum(x) = remainder,  sum(x * m) = pop * mean_age - known
      m = mean age of each group, p = remainder split by group shares, both from OSP
      S3R167_M3010202 (Kauno m. sav., 2021-01-01, single years; 85+ taken as 89.5);
      then clipped to the published range 1..9 and re-balanced to the remainder.
    Groups add up to pop exactly; within masked cells the age split is an estimate.

Analysis uses full-resolution geometry in EPSG:3346; only the web GeoJSON is simplified.
"""
import csv
import glob
import json
import math
import os
import re

import numpy as np
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

RAW = "data/raw/geografija"
OUT = "data/processed/geo"
KAUNAS_SAV = "19"
SIMPLIFY_M = 5.0
AGE = ["all_00_14", "all_15_64", "all_65_plius"]
OSP_AGE = "osp_S3R167_M3010202_2021.xml"  # script 14
OSP_SAV = "19"                            # OSP code of Kauno m. sav.

to_lks = Transformer.from_crs(4326, 3346, always_xy=True).transform
to_wgs = Transformer.from_crs(3346, 4326, always_xy=True).transform


def load_json(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return json.load(f)


def web_geom(g):
    g = g.simplify(SIMPLIFY_M, preserve_topology=True)
    g = transform(to_wgs, g)
    m = mapping(g)

    def rnd(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], 5), round(c[1], 5)]
        return [rnd(x) for x in c]
    m["coordinates"] = rnd(m["coordinates"])
    return m


def seniunijos():
    feats = [f for f in load_json("adr_gra_seniunijos.json")["features"]
             if str(f["properties"]["SAV_KODAS"]) == KAUNAS_SAV]
    out = []
    for f in feats:
        p = f["properties"]
        out.append({"id": p["SEN_KODAS"], "pavadinimas": p["SEN_PAV"].replace(" sen.", ""),
                    "registruota": p["SEN_R"], "rc_formavimo_data": p["formav_dat"],
                    "geom": shape(f["geometry"])})
    return sorted(out, key=lambda x: x["pavadinimas"])


def apylinkes():
    feats = load_json("vrk_apylinkes_2024_seimas.geojson")["features"]
    voters = {}
    for fn in glob.glob(os.path.join(RAW, "vrk_2024_aktyvumasRpg*.json")):
        d = load_json(os.path.basename(fn))["data"]
        rows = d["aktyvumas"] if isinstance(d, dict) else d
        for x in rows:
            if x.get("rpl_id"):
                voters[str(x["rpl_id"])] = int(x["rinkeju_skaicius"])
    out = []
    for f in feats:
        p = f["properties"]
        rid = str(p["AREA2_ID"])
        assert rid in voters, rid
        out.append({"id": rid, "nr": p["AREA2_NO"], "pavadinimas": p["AREA2_NAME"],
                    "seimo_apygarda": p["AREA1_NAME"], "rinkejai_2024": voters[rid],
                    "geom": transform(to_lks, shape(f["geometry"]))})
    return sorted(out, key=lambda x: (x["seimo_apygarda"], int(x["nr"])))


def osp_age_profile():
    """Kauno m. sav., 2021-01-01, both sexes: mean age (years) and share of 0-14 / 15-64 / 65+."""
    with open(os.path.join(RAW, OSP_AGE), encoding="utf-8") as f:
        txt = f.read()
    n = {}
    for obs in re.findall(r"<g:Obs>(.*?)</g:Obs>", txt, re.S):
        key, rest = obs.split("</g:ObsKey>")
        k = dict(re.findall(r'id="([^"]+)" value="([^"]*)"', key))
        if k.get("savivaldybesRegdb") != OSP_SAV or k.get("Lytis") != "0":
            continue
        a = k["Demogr_amziusM1411"]
        v = re.search(r'ObsValue value="([^"]+)"', rest).group(1)
        if a.isdigit():
            n[int(a) + 0.5] = int(float(v))
        elif a == "g085":
            n[89.5] = int(float(v))
        elif a == "g000g999":
            total = int(float(v))
    assert sum(n.values()) == total, (sum(n.values()), total)
    groups = [(0, 15), (15, 65), (65, 200)]
    cnt = [sum(v for a, v in n.items() if lo <= a < hi) for lo, hi in groups]
    mean = [sum(a * v for a, v in n.items() if lo <= a < hi) / c for (lo, hi), c in zip(groups, cnt)]
    return {"total": total, "counts": cnt, "mean_age": np.array(mean), "share": np.array(cnt) / total}


def fill_masked(vals, pop, mean_age, prof):
    """vals: [0-14, 15-64, 65+] with 9 = masked (1..9). Returns float estimates summing to pop."""
    g = np.array(vals, dtype=float)
    mask = g == 9
    k = int(mask.sum())
    rem = pop - g[~mask].sum()
    assert k <= rem <= 9 * k, (vals, pop)
    if k == 1:
        g[mask] = rem
        return g
    m = prof["mean_age"]
    p = prof["share"][mask] / prof["share"][mask].sum() * rem
    C = np.vstack([np.ones(k), m[mask]])
    b = np.array([rem, pop * mean_age - g[~mask] @ m[~mask]])
    x = np.clip(p + C.T @ np.linalg.solve(C @ C.T, b - C @ p), 1, 9)
    for _ in range(50):
        diff = rem - x.sum()
        if abs(diff) < 1e-9:
            break
        free = (x < 9) if diff > 0 else (x > 1)
        x[free] += diff / free.sum()
        x = np.clip(x, 1, 9)
    assert abs(x.sum() - rem) < 1e-6, (vals, pop, x)
    g[mask] = x
    return g


def census_cells(stats=None):
    prof = osp_age_profile()
    cells = []
    eq = np.zeros(3)
    with open(os.path.join(RAW, "GyvSur100M2021.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["gyv_pav"] != "Kauno m.":
                continue
            m = re.match(r"100x100mX(\d+)Y(\d+)$", r["grid_id"])
            x0, y0 = int(m.group(1)) * 10, int(m.group(2)) * 100
            pop = int(r["pop"])
            raw = [int(r[g]) for g in AGE]
            est = fill_masked(raw, pop, float(r["mean_age"]), prof) if 9 in raw else np.array(raw, float)
            if 9 in raw:  # previous method, kept for the check only
                rem = pop - sum(v for v in raw if v != 9)
                eq += [rem / raw.count(9) if v == 9 else v for v in raw]
            else:
                eq += raw
            assert abs(est.sum() - pop) < 1e-6
            cells.append({"geom": box(x0, y0, x0 + 100, y0 + 100), "pop": pop,
                          **{g: float(v) for g, v in zip(AGE, est)}})
    if stats is not None:
        tot = sum(c["pop"] for c in cells)
        new = [sum(c[g] for c in cells) for g in AGE]
        stats["amziaus_grupes_miestas"] = {
            "vidutinio_amziaus_metodas": [round(v) for v in new],
            "vidutinio_amziaus_metodas_proc": [round(100 * v / tot, 2) for v in new],
            "lygaus_padalijimo_metodas_iki_2026_10_20": [round(v) for v in eq],
            "lygaus_padalijimo_metodas_proc": [round(100 * v / tot, 2) for v in eq],
            "osp_2021_01_01": prof["counts"], "osp_2021_01_01_proc": [round(100 * v, 2) for v in prof["share"]],
            "osp_grupiu_vid_amzius": [round(v, 2) for v in prof["mean_age"]],
        }
    return cells


def allocate(cells, units, key):
    tree = STRtree([u["geom"] for u in units])
    for u in units:
        u.update({"gyventojai_2021": 0.0, **{g: 0.0 for g in AGE}})
    orphan_cells = orphan_pop = 0
    for c in cells:
        idx = tree.query(c["geom"])
        shares = [(i, units[i]["geom"].intersection(c["geom"]).area) for i in idx]
        shares = [(i, a) for i, a in shares if a > 0]
        tot = sum(a for _, a in shares)
        if tot == 0:
            i = min(range(len(units)), key=lambda k: units[k]["geom"].distance(c["geom"]))
            shares, tot = [(i, 1.0)], 1.0
            orphan_cells += 1
            orphan_pop += c["pop"]
        for i, a in shares:
            w = a / tot
            units[i]["gyventojai_2021"] += c["pop"] * w
            for g in AGE:
                units[i][g] += c[g] * w
    return {"orphan_cells": orphan_cells, "orphan_pop": orphan_pop, "level": key}


def largest_remainder(vals):
    """Round floats to ints so that the ints sum to round(sum(vals)) (Hamilton method)."""
    floors = [int(math.floor(v)) for v in vals]
    short = round(sum(vals)) - sum(floors)
    order = sorted(range(len(vals)), key=lambda i: -(vals[i] - floors[i]))
    for i in order[:short]:
        floors[i] += 1
    return floors


def write_layer(units, name, props):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: u[k] for k in props}, "geometry": web_geom(u["geom"])}
        for u in units]}
    with open(os.path.join(OUT, f"{name}.geojson"), "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, f"{name}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=props)
        w.writeheader()
        for u in units:
            w.writerow({k: u[k] for k in props})


def main():
    os.makedirs(OUT, exist_ok=True)
    age_stats = {}
    sen, apl, cells = seniunijos(), apylinkes(), census_cells(age_stats)
    city = unary_union([s["geom"] for s in sen])
    sav = [f for f in load_json("adr_gra_savivaldybes.json")["features"]
           if str(f["properties"]["SAV_KODAS"]) == KAUNAS_SAV]
    city_rc = shape(sav[0]["geometry"])
    apl_union = unary_union([a["geom"] for a in apl])

    checks = {
        "seniunijos_n": len(sen),
        "apylinkes_n": len(apl),
        "city_area_km2": round(city_rc.area / 1e6, 3),
        "seniunijos_vs_city_symdiff_km2": round(city.symmetric_difference(city_rc).area / 1e6, 4),
        "apylinkes_outside_city_km2": round(apl_union.difference(city_rc).area / 1e6, 4),
        "city_not_covered_by_apylinkes_km2": round(city_rc.difference(apl_union).area / 1e6, 4),
        "census_cells": len(cells),
        "census_pop": sum(c["pop"] for c in cells),
        "rinkejai_2024_total": sum(a["rinkejai_2024"] for a in apl),
        **age_stats,
    }
    checks["alloc_seniunijos"] = allocate(cells, sen, "seniunijos")
    checks["alloc_apylinkes"] = allocate(cells, apl, "apylinkes")

    for units in (sen, apl):
        for u in units:
            u["plotas_km2"] = round(u["geom"].area / 1e6, 5)  # 5 dp: density in 22 is exact to 1 gyv./km2
        # largest-remainder rounding: unit integers add up to the rounded city total
        # (plain rounding gave 303 203 / 303 197 instead of 303 202; 2026-10-20)
        for src, dst in (("gyventojai_2021", "gyventojai_2021"), ("all_00_14", "gyv_0_14"),
                         ("all_15_64", "gyv_15_64"), ("all_65_plius", "gyv_65_plius")):
            vals = [u.pop(src) for u in units]
            for u, v in zip(units, largest_remainder(vals)):
                u[dst] = v
    checks["seniunijos_pop_sum"] = sum(s["gyventojai_2021"] for s in sen)
    checks["apylinkes_pop_sum"] = sum(a["gyventojai_2021"] for a in apl)

    # crosswalk apylinke -> seniunija via the overlay pieces (area share AND 2021 population
    # share); voters per seniunija = sum(voters of apylinke * population share in seniunija)
    pieces = []
    for a in apl:
        for s in sen:
            g = a["geom"].intersection(s["geom"])
            if g.area > 0:
                pieces.append({"apl": a, "sen": s, "geom": g, "area_share": g.area / a["geom"].area})
    allocate(cells, pieces, "pieces")
    cross = []
    for a in apl:
        ps = [p for p in pieces if p["apl"] is a]
        pop = sum(p["gyventojai_2021"] for p in ps)
        for p in ps:
            p["pop_share"] = p["gyventojai_2021"] / pop if pop else p["area_share"]
        main_p = max(ps, key=lambda p: (p["pop_share"], p["area_share"]))
        a["seniunija"] = main_p["sen"]["pavadinimas"]
        a["seniunijos_gyv_dalis_proc"] = round(100 * main_p["pop_share"], 1)
        for p in sorted(ps, key=lambda p: -p["pop_share"]):
            if p["area_share"] < 0.001 and p["pop_share"] < 0.001:
                continue  # boundary slivers
            cross.append({"apylinke_id": a["id"], "apylinke": a["pavadinimas"],
                          "seniunija": p["sen"]["pavadinimas"],
                          "ploto_dalis_proc": round(100 * p["area_share"], 1),
                          "gyv_2021_dalis_proc": round(100 * p["pop_share"], 1)})
    with open(os.path.join(OUT, "apylinke_seniunija.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["apylinke_id", "apylinke", "seniunija", "ploto_dalis_proc",
                                          "gyv_2021_dalis_proc"])
        w.writeheader()
        w.writerows(cross)
    checks["apylinkes_split_pop_lt95"] = sum(1 for a in apl if a["seniunijos_gyv_dalis_proc"] < 95)

    for s in sen:
        s["rinkejai_2024_ivertis"] = round(sum(p["apl"]["rinkejai_2024"] * p["pop_share"]
                                               for p in pieces if p["sen"] is s))
        s["apylinkiu_sk"] = sum(1 for a in apl if a["seniunija"] == s["pavadinimas"])
    checks["rinkejai_2024_seniunijose_suma"] = sum(s["rinkejai_2024_ivertis"] for s in sen)

    write_layer(sen, "seniunijos", ["id", "pavadinimas", "registruota", "rc_formavimo_data", "plotas_km2",
                                    "gyventojai_2021", "gyv_0_14", "gyv_15_64", "gyv_65_plius",
                                    "apylinkiu_sk", "rinkejai_2024_ivertis"])
    write_layer(apl, "apylinkes", ["id", "nr", "pavadinimas", "seimo_apygarda", "seniunija",
                                   "seniunijos_gyv_dalis_proc", "plotas_km2", "rinkejai_2024",
                                   "gyventojai_2021", "gyv_0_14", "gyv_15_64", "gyv_65_plius"])
    for n in ("seniunijos", "apylinkes"):
        checks[f"{n}_geojson_bytes"] = os.path.getsize(os.path.join(OUT, f"{n}.geojson"))
    with open(os.path.join(OUT, "geografija_patikra.json"), "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=1)
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    for s in sen:
        print(f"{s['pavadinimas']:24s} {s['plotas_km2']:7.2f} km2  pop2021 {s['gyventojai_2021']:6d}  "
              f"0-14 {s['gyv_0_14']:5d}  65+ {s['gyv_65_plius']:5d}  apylinkes {s['apylinkiu_sk']:2d}  "
              f"voters2024 {s['rinkejai_2024_ivertis']}")


if __name__ == "__main__":
    main()
