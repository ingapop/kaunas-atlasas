"""
Extract Kaunas city education institutions (schools, kindergartens, etc.) from the
national Švietimo ir mokslo institucijų registras (ŠMIR) open-data shapefile.

Source: https://www.geoportal.lt/download/opendata/svietimo_istaigos/LT_svietimo_istaigos.zip
(downloaded by hand into data/raw/LT_svietimo_istaigos.zip, unzipped to data/raw/svietimo_istaigos/)
Snapshot dated 2022-08-30 per file metadata - facility locations change rarely, but note
the vintage when presenting this.

Kaunas city savivaldybė code in this dataset is Sav_kodas == '43' (confirmed by filtering
Adresas starting with "Kaunas,").

Coordinates are in LKS-1994 / EPSG:3346 (Lithuanian grid) - reprojected to WGS84 here.

Children's-infrastructure flags (added 2026-10-19, see METODOLOGIJA.md sec. 1):
  ikimokyklinis   = 1 if the institution provides preschool education:
                    (a) Paskirtis / Pagr_paskr mentions a preschool purpose
                        ("darzelis", "lopselis-darzelis", "mokykla-darzelis",
                        "Ikimokyklinio ugdymo grupes ..."), OR
                    (b) Tipas includes "Ikimokyklinio ugdymo mokykla" AND Grupe is
                        not "Aukstoji mokykla" / "Kitas svietimo teikejas"
                        (excludes VDU and LSMU Kauno klinikos, whose purpose
                        fields name no preschool activity).
  bendrojo_ugdymo = 1 if Grupe == "Bendrojo ugdymo mokykla" (primary, basic,
                    pro-gymnasium, gymnasium). Vocational centres that also run
                    gymnasium classes (Grupe "Profesinio mokymo istaiga") are NOT
                    counted.
  vaikai          = ikimokyklinis OR bendrojo_ugdymo. Counted once per inst_kodas
                    in scripts/05 (the registry has one duplicated code).
NOT counted: universities, colleges, vocational, adult and non-formal education,
free teachers ("Laisvasis mokytojas"), other providers.

Output: data/processed/education_facilities.csv
"""
import csv
import re

import shapefile
from pyproj import Transformer

SHP_PATH = "data/raw/svietimo_istaigos/SMIR_istaigos.shp"
OUT_CSV = "data/processed/education_facilities.csv"
KAUNAS_SAV_KODAS = "43"

PRESCHOOL_PURPOSE = re.compile(r"(darželis|Ikimokyklinio ugdymo grupės)", re.IGNORECASE)
PRESCHOOL_TYPE = "Ikimokyklinio ugdymo mokykla"
TYPE_ONLY_EXCLUDED_GROUPS = {"Aukštoji mokykla", "Kitas švietimo teikėjas"}
GENERAL_ED_GROUP = "Bendrojo ugdymo mokykla"


def flags(rec):
    purpose = f"{rec['Pagr_paskr']} ; {rec['Paskirtis']}"
    pre = bool(PRESCHOOL_PURPOSE.search(purpose)) or (
        PRESCHOOL_TYPE in rec["Tipas"] and rec["Grupe"] not in TYPE_ONLY_EXCLUDED_GROUPS)
    gen = rec["Grupe"] == GENERAL_ED_GROUP
    return int(pre), int(gen), int(pre or gen)


transformer = Transformer.from_crs("EPSG:3346", "EPSG:4326", always_xy=True)


def main():
    sf = shapefile.Reader(SHP_PATH, encoding="cp1257", encodingErrors="replace")
    rows = []
    for shape_rec in sf.iterShapeRecords():
        rec = shape_rec.record
        if rec["Sav_kodas"] != KAUNAS_SAV_KODAS:
            continue
        if rec["Isreg_data"]:  # deregistered/closed institution
            continue
        if not shape_rec.shape.points:
            continue
        x, y = shape_rec.shape.points[0]
        lon, lat = transformer.transform(x, y)
        pre, gen, kids = flags(rec)
        rows.append(
            {
                "inst_kodas": rec["Inst_kodas"],
                "name": rec["Pav_LT"],
                "type": rec["Tipas"],
                "group": rec["Grupe"],
                "address": rec["Adresas"],
                "registered": rec["Ireg_data"],
                "purpose": f"{rec['Pagr_paskr']} ; {rec['Paskirtis']}".strip(" ;"),
                "parent_inst": rec["Mot_inst_k"],
                "ikimokyklinis": pre,
                "bendrojo_ugdymo": gen,
                "vaikai": kids,
                "lat": lat,
                "lon": lon,
            }
        )

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["inst_kodas", "name", "type", "group", "address", "registered", "purpose",
                        "parent_inst", "ikimokyklinis", "bendrojo_ugdymo", "vaikai", "lat", "lon"],
        )
        w.writeheader()
        w.writerows(rows)

    print(f"Saved {len(rows)} active Kaunas education institutions -> {OUT_CSV}")
    types = {}
    for r in rows:
        types[r["type"]] = types.get(r["type"], 0) + 1
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {c:4d}  {t}")
    for flag in ("ikimokyklinis", "bendrojo_ugdymo", "vaikai"):
        codes = {r["inst_kodas"] for r in rows if r[flag]}
        print(f"{flag}: {sum(r[flag] for r in rows)} rows, {len(codes)} unique inst_kodas")
    both = {r["inst_kodas"] for r in rows if r["ikimokyklinis"] and r["bendrojo_ugdymo"]}
    print(f"preschool AND general-ed (school-kindergartens etc.): {len(both)} unique")


if __name__ == "__main__":
    main()
