"""
Fetch green-space polygons (parks, gardens, forests, grass, meadows) for Kaunas
from OpenStreetMap via Overpass API. Unlike the municipal ArcGIS service
(digital.kaunas.lt), OSM's Overpass API returns full polygon geometry with no
token required, so this covers the "Aplinka" (environment) dimension without
needing the geocoding step originally planned for the municipal dataset's
address-only records.

Computes each polygon's centroid (simple average of exterior ring points -
adequate for later nearest-neighborhood assignment, not for precise area work)
and area in hectares via the shoelace formula on an equirectangular
approximation (fine at this latitude/scale).

Output: data/raw/osm_kaunas_greenspace.csv (osm_id, kind, name, area_ha, lat, lon)
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
[out:json][timeout:120][bbox:{BBOX}];
(
  way["leisure"~"^(park|garden|nature_reserve)$"];
  way["landuse"~"^(forest|grass|meadow|recreation_ground)$"];
  way["natural"~"^(wood|grassland)$"];
  relation["leisure"~"^(park|garden|nature_reserve)$"];
);
out geom;
"""

OUT_CSV = "data/raw/osm_kaunas_greenspace.csv"

LAT0 = 54.9  # reference latitude for equirectangular approximation
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(LAT0))


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
                return json.loads(urllib.request.urlopen(req, timeout=150).read())
            except Exception as e:
                last_err = e
                print(f"  {mirror} attempt {attempt+1} failed: {e}")
                time.sleep(8)
    raise SystemExit(f"All Overpass mirrors failed: {last_err}")


def polygon_area_and_centroid(coords_latlon):
    # project to local meters, shoelace formula
    pts = [((lon - coords_latlon[0][1]) * M_PER_DEG_LON, (lat - coords_latlon[0][0]) * M_PER_DEG_LAT) for lat, lon in coords_latlon]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    area2 = 0.0
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area = area2 / 2.0
    if abs(area) < 1e-6:
        lats = [p[0] for p in coords_latlon]
        lons = [p[1] for p in coords_latlon]
        return 0.0, sum(lats) / len(lats), sum(lons) / len(lons)
    cx /= 6 * area
    cy /= 6 * area
    lat_c = coords_latlon[0][0] + cy / M_PER_DEG_LAT
    lon_c = coords_latlon[0][1] + cx / M_PER_DEG_LON
    area_ha = abs(area) / 10000.0
    return area_ha, lat_c, lon_c


def main():
    result = fetch()
    rows = []
    for el in result["elements"]:
        geom = el.get("geometry")
        if not geom or len(geom) < 3:
            continue
        tags = el.get("tags", {})
        kind = tags.get("leisure") or tags.get("landuse") or tags.get("natural")
        name = tags.get("name", "")
        coords = [(pt["lat"], pt["lon"]) for pt in geom]
        area_ha, lat_c, lon_c = polygon_area_and_centroid(coords)
        if area_ha < 0.01:  # drop slivers/noise
            continue
        rows.append(
            {"osm_id": el["id"], "kind": kind, "name": name, "area_ha": round(area_ha, 4), "lat": lat_c, "lon": lon_c}
        )

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["osm_id", "kind", "name", "area_ha", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} green space polygons -> {OUT_CSV}")
    print(f"Total green area: {sum(r['area_ha'] for r in rows):.1f} ha")


if __name__ == "__main__":
    main()
