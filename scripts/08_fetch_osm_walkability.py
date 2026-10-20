"""
Simple substitute for "Gyvenamoji aplinka" (living-environment/infrastructure level),
since the real source (Bendrasis planas infrastructure-level GIS layers) needs more
GIS work than is in scope right now.

Proxy used: sidewalk/footway infrastructure ratio - total length of pedestrian
infrastructure (footway/path/pedestrian/living_street) vs. total length of
ordinary streets (residential/service/tertiary), per neighborhood. This tracks
the same concern the original document's own narrative raised for underserved
areas (e.g. Romainiai explicitly flagged as lacking "šaligatviai" - sidewalks).
It's a real, defensible signal, but it is NOT the same thing as the General
Plan's 1-5 infrastructure-level classification - label it clearly as a proxy
in the dashboard.

Output: data/raw/osm_kaunas_streets.csv (osm_id, kind, length_m, lat, lon)
  kind is "footway" or "road" - midpoint (lat,lon) used for nearest-neighborhood
  assignment in 05_assign_to_neighborhoods.py.
"""
import csv
import json
import math
import time
import urllib.request

MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

BBOX = "54.83,23.80,54.96,24.18"
QUERY = f"""
[out:json][timeout:150][bbox:{BBOX}];
(
  way["highway"~"^(footway|path|pedestrian|living_street|cycleway)$"];
  way["highway"~"^(residential|service|tertiary|unclassified)$"];
);
out geom;
"""

OUT_CSV = "data/raw/osm_kaunas_streets.csv"

LAT0 = 54.9
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(LAT0))

FOOTWAY_TYPES = {"footway", "path", "pedestrian", "living_street", "cycleway"}


def fetch():
    last_err = None
    for mirror in MIRRORS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    mirror,
                    data=f"data={QUERY}".encode(),
                    headers={"User-Agent": "kaunas-dashboard-research/1.0 (contact: inga.popovaite@gmail.com)"},
                )
                return json.loads(urllib.request.urlopen(req, timeout=170).read())
            except Exception as e:
                last_err = e
                print(f"  {mirror} attempt {attempt+1} failed: {e}")
                time.sleep(8)
    raise SystemExit(f"All Overpass mirrors failed: {last_err}")


def way_length_and_midpoint(geom):
    pts = [(pt["lat"], pt["lon"]) for pt in geom]
    length = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(pts, pts[1:]):
        dx = (lon2 - lon1) * M_PER_DEG_LON
        dy = (lat2 - lat1) * M_PER_DEG_LAT
        length += math.hypot(dx, dy)
    mid = pts[len(pts) // 2]
    return length, mid[0], mid[1]


def main():
    result = fetch()
    rows = []
    for el in result["elements"]:
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        highway = el.get("tags", {}).get("highway")
        kind = "footway" if highway in FOOTWAY_TYPES else "road"
        length_m, lat, lon = way_length_and_midpoint(geom)
        rows.append({"osm_id": el["id"], "kind": kind, "length_m": round(length_m, 1), "lat": lat, "lon": lon})

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["osm_id", "kind", "length_m", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} street/footway segments -> {OUT_CSV}")
    fw = sum(r["length_m"] for r in rows if r["kind"] == "footway")
    rd = sum(r["length_m"] for r in rows if r["kind"] == "road")
    print(f"Total footway: {fw/1000:.1f} km, total road: {rd/1000:.1f} km, ratio: {fw/rd:.2f}")


if __name__ == "__main__":
    main()
