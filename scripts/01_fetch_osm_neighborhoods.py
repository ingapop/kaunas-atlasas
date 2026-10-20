"""
Fetch named neighborhood/suburb center points for Kaunas from OpenStreetMap (Overpass API).

Why points, not polygons: Kaunas stopped marking informal district boundaries in city
plans after the 2001 general plan (see lt.wikipedia.org/wiki/Kauno_miesto_dalys), and no
current open dataset has polygons for these ~24-58 informal neighborhoods. OSM maps them
only as named points (place=suburb/quarter). We use these points as the geographic
reference set: every other dataset (facilities, GIS layers) gets assigned to its nearest
neighborhood point (see 06_assign_and_score.py), which is a standard substitute for a
true polygon boundary when none exists.

Output: data/raw/osm_kaunas_neighborhoods.csv (name, osm_id, lat, lon)
"""
import csv
import json
import time
import urllib.request

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
BBOX = "54.83,23.80,54.96,24.18"  # south,west,north,east around Kaunas city
QUERY = f"""
[out:json][timeout:90][bbox:{BBOX}];
(
  relation["place"~"suburb|neighbourhood|quarter|city_block"];
  way["place"~"suburb|neighbourhood|quarter"];
  node["place"~"suburb|neighbourhood|quarter"];
);
out tags center;
"""

OUT_CSV = "data/raw/osm_kaunas_neighborhoods.csv"


def fetch(retries=3):
    req = urllib.request.Request(
        OVERPASS_URL,
        data=f"data={QUERY}".encode(),
        headers={"User-Agent": "kaunas-dashboard-research/1.0 (data collection script)"},
    )
    data = urllib.request.urlopen(req, timeout=120).read()
    return json.loads(data)


def main():
    for attempt in range(3):
        try:
            result = fetch()
            break
        except Exception as e:
            print(f"attempt {attempt+1} failed: {e}")
            time.sleep(5)
    else:
        raise SystemExit("Overpass API failed after retries")

    rows = []
    for el in result["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        else:
            center = el.get("center")
            if not center:
                continue
            lat, lon = center["lat"], center["lon"]
        rows.append(
            {
                "name": name,
                "osm_type": el["type"],
                "osm_id": el["id"],
                "place_type": tags.get("place"),
                "lat": lat,
                "lon": lon,
            }
        )

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "osm_type", "osm_id", "place_type", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)

    print(f"Saved {len(rows)} neighborhood points -> {OUT_CSV}")


if __name__ == "__main__":
    main()
