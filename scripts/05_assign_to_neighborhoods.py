"""
Assign every facility point to its nearest named neighborhood (from
data/raw/osm_kaunas_neighborhoods.csv), then count facilities per neighborhood
per dimension. This is the "simple counts" scoring approach: no polygon
boundaries exist for these informal areas (see 01_fetch_osm_neighborhoods.py),
so nearest-point assignment substitutes for a spatial join.

NOTE: despite the "05" prefix, this must run AFTER 09_build_curated_neighborhoods.py
(and after all the OSM fetch scripts 06/07/08) since it depends on their outputs.
The numbering reflects when each script was first written, not run order - see
the sequence described in METODOLOGIJA.md / CHANGELOG.md.

Reads:
  data/processed/curated_neighborhoods.csv (curated ~24 named areas, not the raw 58)
  data/processed/education_facilities.csv
  data/processed/social_facilities.csv
  data/raw/osm_kaunas_healthcare.csv
  data/raw/osm_kaunas_greenspace.csv
  data/raw/osm_kaunas_streets.csv

Output: data/processed/neighborhood_facility_counts.csv
"""
import csv
import math

NEIGHBORHOODS_CSV = "data/processed/curated_neighborhoods.csv"
OUT_CSV = "data/processed/neighborhood_facility_counts.csv"

# (source csv, lat field, lon field, dimension label, category field or None, category filter or None)
# Children's dimension (changed 2026-10-19): flags are set in scripts/03; each
# institution (inst_kodas) is counted once - see UNIQUE_KEY. The former
# "vaikai_mokyklos_visos" (all 543 registry entries incl. universities, free
# teachers, adult ed) is no longer produced.
SOURCES = [
    ("data/processed/education_facilities.csv", "lat", "lon", "vaikai_darzeliai", "ikimokyklinis", "1"),
    ("data/processed/education_facilities.csv", "lat", "lon", "vaikai_bendrojo_ugdymo_mokyklos", "bendrojo_ugdymo", "1"),
    ("data/processed/education_facilities.csv", "lat", "lon", "vaikai_viso", "vaikai", "1"),
    ("data/processed/social_facilities.csv", "lat", "lon", "pazeidziami_senjorai_paslaugu_taskai", None, None),
    ("data/raw/osm_kaunas_healthcare.csv", "lat", "lon", "sveikata_istaigos_visos", None, None),
    ("data/raw/osm_kaunas_healthcare.csv", "lat", "lon", "sveikata_vaistines", "kind", "pharmacy"),
]

# weighted sources: (source csv, lat field, lon field, dimension label, weight field, category field, category filter)
# count each value of this field once per dimension (if the field exists in the source)
UNIQUE_KEY = "inst_kodas"

WEIGHTED_SOURCES = [
    ("data/raw/osm_kaunas_greenspace.csv", "lat", "lon", "aplinka_zaliuju_plotu_ha", "area_ha", None, None),
    ("data/raw/osm_kaunas_streets.csv", "lat", "lon", "gyv_aplinka_pesciuju_km", "length_m", "kind", "footway"),
    ("data/raw/osm_kaunas_streets.csv", "lat", "lon", "gyv_aplinka_gatviu_km", "length_m", "kind", "road"),
]


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_neighborhoods():
    with open(NEIGHBORHOODS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def nearest_neighborhood(lat, lon, neighborhoods):
    best, best_d = None, float("inf")
    for n in neighborhoods:
        d = haversine_km(lat, lon, float(n["lat"]), float(n["lon"]))
        if d < best_d:
            best, best_d = n["name"], d
    return best, best_d


def main():
    neighborhoods = load_neighborhoods()
    counts = {n["name"]: {} for n in neighborhoods}
    dims_seen = set()

    for path, latf, lonf, dim, catfield, catval in SOURCES:
        dims_seen.add(dim)
        seen_keys = set()
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get(latf) or row[latf] == "None":
                    continue
                if catfield and row.get(catfield) != catval:
                    continue
                key = row.get(UNIQUE_KEY)
                if key:
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                lat, lon = float(row[latf]), float(row[lonf])
                name, dist = nearest_neighborhood(lat, lon, neighborhoods)
                counts[name][dim] = counts[name].get(dim, 0) + 1
        print(f"  {dim}: {sum(c.get(dim, 0) for c in counts.values())}")

    for path, latf, lonf, dim, weightf, catfield, catval in WEIGHTED_SOURCES:
        dims_seen.add(dim)
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get(latf) or row[latf] == "None":
                    continue
                if catfield and row.get(catfield) != catval:
                    continue
                lat, lon = float(row[latf]), float(row[lonf])
                weight = float(row[weightf]) if row.get(weightf) else 0.0
                if "_km" in dim:
                    weight /= 1000.0
                name, dist = nearest_neighborhood(lat, lon, neighborhoods)
                counts[name][dim] = counts[name].get(dim, 0.0) + weight

    dims = sorted(dims_seen)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["neighborhood"] + dims)
        for n in neighborhoods:
            name = n["name"]
            row = []
            for d in dims:
                v = counts[name].get(d, 0)
                row.append(round(v, 2) if isinstance(v, float) else v)
            w.writerow([name] + row)

    print(f"Saved neighborhood facility counts -> {OUT_CSV}")


if __name__ == "__main__":
    main()
