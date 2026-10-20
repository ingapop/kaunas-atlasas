"""
Fetch environment GIS layers (green space, flood risk) from Kaunas's public ArcGIS
REST service and save each as raw GeoJSON-ish JSON (attributes + polygon rings, WGS84).

Source: digital.kaunas.lt/arcgis/rest/services/Aplinkos_apsauga/Aplinkos_apsaugos_viesi_teminiai
Confirmed public (no auth token needed), unlike the Sveikatingumas folder which requires one.

Layers pulled (by id, discovered via /MapServer?f=json):
  17 - Zeldynu_teritorijos_2025  (green space polygons, area in ha)
  22 - flood risk, 10% probability (snowmelt/heavy rain)
  23 - flood risk, 1% probability
  24 - flood risk, 0.1% probability
Layers 12/15 (other green-space variants), 21/35 (noise, other flood) returned no
metadata via this endpoint at the time of writing - skipped.
"""
import json
import time
import urllib.request

BASE = "https://digital.kaunas.lt/arcgis/rest/services/Aplinkos_apsauga/Aplinkos_apsaugos_viesi_teminiai/MapServer"

LAYERS = {
    17: "green_space_2025",
    22: "flood_risk_10pct",
    23: "flood_risk_1pct",
    24: "flood_risk_0_1pct",
}


def fetch_layer(layer_id, offset=0, batch=1000):
    url = (
        f"{BASE}/{layer_id}/query?where=1%3D1&outFields=*&returnGeometry=true"
        f"&outSR=4326&f=json&resultOffset={offset}&resultRecordCount={batch}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "kaunas-dashboard-research/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def fetch_all(layer_id):
    features = []
    offset = 0
    while True:
        data = fetch_layer(layer_id, offset)
        feats = data.get("features", [])
        features.extend(feats)
        if not data.get("exceededTransferLimit"):
            break
        offset += len(feats)
        time.sleep(0.5)
    return features


def main():
    for layer_id, slug in LAYERS.items():
        print(f"fetching layer {layer_id} ({slug})...")
        try:
            features = fetch_all(layer_id)
        except Exception as e:
            print(f"  FAILED: {e}")
            continue
        out_path = f"data/raw/env_{slug}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(features, f, ensure_ascii=False)
        print(f"  saved {len(features)} features -> {out_path}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
