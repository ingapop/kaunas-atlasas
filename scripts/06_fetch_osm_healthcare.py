"""
Fetch healthcare facility points (clinics, hospitals, doctors' offices, pharmacies) for
Kaunas from OpenStreetMap via Overpass API.

Why OSM instead of the official sources: the national license registry
(data.gov.lt/datasets/1623) and the VASPVT license search
(licencijavimas.vaspvt.gov.lt) are both JS/form-rendered with no scriptable bulk
export found. OSM's own healthcare POI tagging is a reasonable public substitute
for facility *locations* (not license/capacity detail).

Output: data/raw/osm_kaunas_healthcare.csv (name, kind, lat, lon)
"""
import csv
import json
import time
import urllib.request

MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

BBOX = "54.83,23.80,54.96,24.18"
QUERY = f"""
[out:json][timeout:90][bbox:{BBOX}];
(
  node["amenity"~"^(clinic|hospital|doctors|pharmacy)$"];
  way["amenity"~"^(clinic|hospital|doctors|pharmacy)$"];
  node["healthcare"];
  way["healthcare"];
);
out tags center;
"""

OUT_CSV = "data/raw/osm_kaunas_healthcare.csv"


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
                return json.loads(urllib.request.urlopen(req, timeout=120).read())
            except Exception as e:
                last_err = e
                print(f"  {mirror} attempt {attempt+1} failed: {e}")
                time.sleep(8)
    raise SystemExit(f"All Overpass mirrors failed: {last_err}")


def main():
    result = fetch()
    rows = []
    for el in result["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        kind = tags.get("amenity") or tags.get("healthcare")
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center")
            if not c:
                continue
            lat, lon = c["lat"], c["lon"]
        rows.append({"name": name or "", "kind": kind, "lat": lat, "lon": lon})

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "kind", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {len(rows)} healthcare points -> {OUT_CSV}")


if __name__ == "__main__":
    main()
