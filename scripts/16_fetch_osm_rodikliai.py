"""
Stage 2 of the atlas rebuild: fetch OpenStreetMap layers WITH geometry (Overpass API),
for point-in-polygon / clipping against seniunijos and apylinkes (script 17).

Bounding box = Kaunas city polygon bounds (Registru centras, script 15) + small margin.
NOTE: the old scripts 06-08 used bbox 54.83,23.80,54.96,24.18, which cuts off the
city's western edge (city extends to lon 23.753) - fixed here.

Layers and tags (documented in METODOLOGIJA.md):
  sveikata   - same tags as scripts/06: amenity=clinic|hospital|doctors|pharmacy, healthcare=*
  kultura    - amenity=library|theatre|arts_centre|community_centre|cinema, tourism=museum
  sportas    - leisure=sports_centre|stadium|sports_hall|swimming_pool (swimming_pool with
               access=private|customers excluded later); leisure=pitch|track|fitness_station
               (outdoor grounds, counted separately); leisure=playground (separate)
  zaluma     - same tags as scripts/07 (leisure=park|garden|nature_reserve,
               landuse=forest|grass|meadow|recreation_ground, natural=wood|grassland),
               ways AND multipolygon relations for all of them, full geometry
  gatves     - same tags as scripts/08: footway|path|pedestrian|living_street|cycleway vs
               residential|service|tertiary|unclassified, full line geometry
Also: kaunospc.lt contacts page -> only the list of street addresses is kept (the page lists
staff names and phone numbers; they are NOT stored).

Run from dashboardai/atlasas/:  python3 scripts/16_fetch_osm_rodikliai.py [layer ...]
(check fetch_log.json: all layers should share one OSM base date; re-fetch a layer if a mirror
returned a stale snapshot)
Outputs: data/raw/osm2/<layer>.json (raw Overpass JSON), data/raw/osm2/kaunospc_adresai.json,
         data/raw/osm2/fetch_log.json
"""
import datetime
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

OUT = "data/raw/osm2"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = {"User-Agent": "kaunas-atlas-research/1.0 (public data reproduction)"}
BBOX = "54.815,23.745,54.965,24.105"  # S,W,N,E: city bounds 54.8207-54.9596, 23.7533-24.1003

QUERIES = {
    "sveikata": """
  nwr["amenity"~"^(clinic|hospital|doctors|pharmacy)$"];
  nwr["healthcare"];
);
out tags center;""",
    "kultura": """
  nwr["amenity"~"^(library|theatre|arts_centre|community_centre|cinema)$"];
  nwr["tourism"="museum"];
);
out tags center;""",
    "sportas": """
  nwr["leisure"~"^(sports_centre|stadium|sports_hall|swimming_pool|pitch|track|fitness_station|playground)$"];
);
out tags center;""",
    "zaluma": """
  way["leisure"~"^(park|garden|nature_reserve)$"];
  way["landuse"~"^(forest|grass|meadow|recreation_ground)$"];
  way["natural"~"^(wood|grassland)$"];
  relation["type"="multipolygon"]["leisure"~"^(park|garden|nature_reserve)$"];
  relation["type"="multipolygon"]["landuse"~"^(forest|grass|meadow|recreation_ground)$"];
  relation["type"="multipolygon"]["natural"~"^(wood|grassland)$"];
  relation["leisure"~"^(park|garden|nature_reserve)$"];
);
out geom;""",
    "gatves": """
  way["highway"~"^(footway|path|pedestrian|living_street|cycleway)$"];
  way["highway"~"^(residential|service|tertiary|unclassified)$"];
);
out tags geom;""",
}
KAUNOSPC = "https://www.kaunospc.lt/kontaktai-2/"
log = {"fetched_at": None, "bbox": BBOX, "files": {}}


def overpass(body):
    q = f"[out:json][timeout:300][bbox:{BBOX}];\n(" + body
    last = None
    for m in MIRRORS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(m, data=("data=" + urllib.parse.quote(q)).encode(), headers=UA)
                data = urllib.request.urlopen(req, timeout=360).read()
                json.loads(data)
                return data, m
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  {m} attempt {attempt + 1}: {e}")
                time.sleep(10)
    raise SystemExit(f"Overpass failed: {last}")


def main():
    """Optional CLI args = subset of layers to (re)fetch, e.g. `... kultura`."""
    import sys
    only = set(sys.argv[1:])
    os.makedirs(OUT, exist_ok=True)
    logf = os.path.join(OUT, "fetch_log.json")
    if only and os.path.exists(logf):
        log.update(json.load(open(logf, encoding="utf-8")))
    log["fetched_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    for name, body in QUERIES.items():
        if only and name not in only:
            continue
        data, mirror = overpass(body)
        with open(os.path.join(OUT, f"{name}.json"), "wb") as f:
            f.write(data)
        d = json.loads(data)
        log["files"][name] = {"mirror": mirror, "query_body": body.strip(), "elements": len(d["elements"]),
                              "osm_base": d.get("osm3s", {}).get("timestamp_osm_base"),
                              "sha256": hashlib.sha256(data).hexdigest()}
        print(f"{name}: {len(d['elements'])} elements (OSM base {log['files'][name]['osm_base']})")
        time.sleep(5)

    # kaunospc.lt - keep addresses only
    if only and "kaunospc" not in only:
        with open(logf, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
        return
    raw = urllib.request.urlopen(urllib.request.Request(KAUNOSPC, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}),
        timeout=120).read().decode("utf-8", "replace")
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S)
    txt = html.unescape(re.sub(r"<[^>]+>", "\n", txt))
    addrs = sorted(set(m.group(0).strip() for m in re.finditer(
        r"[A-ZĄČĘĖĮŠŲŪŽ][\w.\- ]{1,40}?(?:g\.|pr\.|al\.|pl\.)\s*\d+[A-Z]?(?:/\d+)?", txt)))
    upd = re.search(r"Informacija atnaujinta (\d{4}-\d{2}-\d{2})", txt)
    with open(os.path.join(OUT, "kaunospc_adresai.json"), "w", encoding="utf-8") as f:
        json.dump({"url": KAUNOSPC, "page_says_updated": upd.group(1) if upd else None,
                   "addresses": addrs}, f, ensure_ascii=False, indent=1)
    log["files"]["kaunospc_adresai"] = {"url": KAUNOSPC, "n_addresses": len(addrs)}
    print(f"kaunospc.lt: {len(addrs)} distinct address strings (page updated {upd.group(1) if upd else '?'})")

    with open(os.path.join(OUT, "fetch_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
