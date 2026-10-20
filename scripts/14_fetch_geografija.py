"""
Stage 1 of the atlas rebuild (PLANAS.md): fetch geography and denominators.

Sources (all public; licence noted in METODOLOGIJA.md "Geografija (2026-10 perkūrimas)"):
  1. Registru centras, Adresu registras open data (CC BY 4.0, data.gov.lt/datasets/1346, 1288):
       adr_gra_seniunijos.json   seniunija polygons, EPSG:3346, whole country
       adr_seniunijos.csv        seniunija classifier with registration dates
       adr_gra_savivaldybes.json municipality polygons (city boundary for clipping/checks)
  2. VRK ArcGIS open data hub (rinkimai-vrk.opendata.arcgis.com; VRK terms: cite VRK,
     do not alter data meaning) - polling-district (apylinke) polygons:
       LRS_2024_A_T1_APL   2024 Seimas election round 1 (latest national), has MUNICIPALITY
       STSM_2023_A_T1_APL  2023 municipal election round 1 (last municipal), AREA1 = municipality
  3. VRK publishing API (tyrimai/vrk-duomenys/VRK_publikavimo_API_v9.pdf):
       rt.json; 1544/1/rpl.json, rpg.json, 1544/1/aktyvumas/aktyvumasRpg{id}.json for the
       Seimas constituencies containing a Kaunas-city apylinke -> registered voters per apylinke (2024);
       1304/1/aktyvumas/aktyvumasRpg21934.json -> registered voters per apylinke (2023 municipal)
  4. Valstybes duomenu agentura, 2021 census 100 m grid (CC BY 4.0, data.gov.lt/datasets/1911):
       GyvSur100M2021.csv (urban settlements; pop + 0-14 / 15-64 / 65+)
  6. Statistics Lithuania (OSP) open data API, table S3R167_M3010202 (resident population at the
     beginning of the year by municipality x single year of age x sex), 2021:
       osp_S3R167_M3010202_2021.xml  -> group mean ages and age shares used in script 15 to
       estimate suppressed census age groups (added 2026-10-20). Only this file:
       python3 scripts/14_fetch_geografija.py osp
  5. Evidence for the 2025 seniunija reform (kaunas.lt): council protocol 2025-07-08 (TP-6,
     decision T-572) PDF and the municipality news page of 2025-07-10.

Run from dashboardai/atlasas/:  python3 scripts/14_fetch_geografija.py
Outputs: data/raw/geografija/ (git-ignored) + fetch_log.json (URL, date, sha256, bytes).
Existing files are re-downloaded (snapshot = latest run); the log records the date.
"""
import datetime
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

OUT = "data/raw/geografija"
UA = {"User-Agent": "Mozilla/5.0 (kaunas-atlas research; public data reproduction)"}
RC = "https://www.registrucentras.lt/aduomenys/?byla="
VRK_HUB = "https://services5.arcgis.com/1eJSoE398Vejvs53/arcgis/rest/services"
VRK_API = "https://www.vrk.lt/statiniai/puslapiai/rinkimai"
SEIMAS_2024 = ("1544", "1", "2150")      # vr_id, round, rt_id (2024-10-13 Seimas, round 1)
MUNI_2023 = ("1304", "1", "1922", "21934")  # vr_id, round, rt_id, Kauno miesto rpg_id
CENSUS_100M = ("https://data.gov.lt/datasets/1911/versions/243/dynamic-resource/"
               "GyvSur100M2021/csv/download/")
OSP_POP_2021 = ("https://osp-rs.stat.gov.lt/rest_xml/data/S3R167_M3010202"
                "?startPeriod=2021&endPeriod=2021")
REFORM_DOCS = {
    "kaunas_taryba_2025-07-08_protokolas_TP-6.pdf":
        "https://www.kaunas.lt/wp-content/uploads/sites/13/2025/07/t251006.pdf",
    "kaunas_lt_pokyciai_seniunijose_2025-07-10.html":
        "https://www.kaunas.lt/seniunijos/pokyciai-miesto-seniunijose/",
}

log = {"fetched_at": None, "files": {}}


def get(url, retries=3, timeout=300):
    last = None
    for i in range(retries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  retry {i + 1}: {url[:100]} ({e})")
            time.sleep(5)
    raise SystemExit(f"Failed: {url} ({last})")


def save(name, url, data=None):
    data = get(url) if data is None else data
    path = os.path.join(OUT, name)
    with open(path, "wb") as f:
        f.write(data)
    log["files"][name] = {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "downloaded": datetime.date.today().isoformat()}
    print(f"{name}: {len(data):,} bytes")
    return data


def arcgis_layer(service, where, name):
    base = f"{VRK_HUB}/{service}/FeatureServer/0"
    save(f"{name}_meta.json", base + "?f=json")
    cnt = json.loads(get(base + "/query?" + urllib.parse.urlencode(
        {"where": where, "returnCountOnly": "true", "f": "json"})))["count"]
    feats, off = [], 0
    while True:
        q = {"where": where, "outFields": "*", "returnGeometry": "true", "outSR": "4326",
             "f": "geojson", "orderByFields": "OBJECTID", "resultOffset": off, "resultRecordCount": 500}
        page = json.loads(get(base + "/query?" + urllib.parse.urlencode(q)))
        got = page.get("features", [])
        feats += got
        off += len(got)
        if len(got) < 500:
            break
    assert len(feats) == cnt, (service, len(feats), cnt)
    fc = {"type": "FeatureCollection", "source": base, "where": where, "features": feats}
    save(f"{name}.geojson", base + "/query?where=" + urllib.parse.quote(where),
         json.dumps(fc, ensure_ascii=False).encode("utf-8"))
    return cnt


def fetch_osp_only():
    """Fetch only the OSP age table and merge its entry into the existing fetch_log.json."""
    logf = os.path.join(OUT, "fetch_log.json")
    if os.path.exists(logf):
        with open(logf, encoding="utf-8") as f:
            log.update(json.load(f))
    save("osp_S3R167_M3010202_2021.xml", OSP_POP_2021)
    with open(logf, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def main():
    import sys
    if sys.argv[1:] == ["osp"]:
        os.makedirs(OUT, exist_ok=True)
        fetch_osp_only()
        return
    os.makedirs(OUT, exist_ok=True)
    log["fetched_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    # 1. Registru centras
    for f in ("adr_gra_seniunijos.json", "adr_seniunijos.csv", "adr_gra_savivaldybes.json",
              "adr_savivaldybes.csv"):
        save(f, RC + f)

    # 2. VRK polygons
    n24 = arcgis_layer("LRS_2024_A_T1_APL", "MUNICIPALITY = 'Kauno miesto'", "vrk_apylinkes_2024_seimas")
    n23 = arcgis_layer("STSM_2023_A_T1_APL", "AREA1_NAME = 'Kauno miesto'", "vrk_apylinkes_2023_savivalda")
    print(f"VRK polygons: 2024 Seimas {n24}, 2023 municipal {n23}")

    # 3. VRK publishing API - registered voters per apylinke
    save("vrk_rt.json", f"{VRK_API}/rt.json")
    vr, rt, rt_id = SEIMAS_2024
    save("vrk_2024_rpl.json", f"{VRK_API}/{vr}/{rt}/rpl.json")
    save("vrk_2024_rpg.json", f"{VRK_API}/{vr}/{rt}/rpg.json")
    # constituencies that contain at least one Kaunas-city apylinke (from the polygons'
    # AREA2_ID = rpl id); Kazliskiu apylinke belongs to Garliavos constituency
    rpl = json.loads(open(os.path.join(OUT, "vrk_2024_rpl.json"), encoding="utf-8").read())["data"]
    rpl_rpg = {x["id"]: x["rpg_id"] for x in rpl}
    with open(os.path.join(OUT, "vrk_apylinkes_2024_seimas.geojson"), encoding="utf-8") as f:
        ids = {str(ft["properties"]["AREA2_ID"]) for ft in json.load(f)["features"]}
    rpg_ids = sorted({rpl_rpg[i] for i in ids})
    print(f"Kaunas apylinkes span {len(rpg_ids)} Seimas constituencies: {rpg_ids}")
    for rid in rpg_ids:
        save(f"vrk_2024_aktyvumasRpg{rid}.json", f"{VRK_API}/{vr}/{rt}/aktyvumas/aktyvumasRpg{rid}.json")
    vr, rt, rt_id, rpg_id = MUNI_2023
    save("vrk_2023_rpl.json", f"{VRK_API}/{vr}/{rt}/rpl.json")
    save(f"vrk_2023_aktyvumasRpg{rpg_id}.json",
         f"{VRK_API}/{vr}/{rt}/aktyvumas/aktyvumasRpg{rpg_id}.json")

    # 4. Census grid
    save("GyvSur100M2021.csv", CENSUS_100M)

    # 6. OSP population by single year of age, 2021 (age-group estimation in script 15)
    save("osp_S3R167_M3010202_2021.xml", OSP_POP_2021)

    # 5. Reform evidence
    for name, url in REFORM_DOCS.items():
        save(name, url)

    with open(os.path.join(OUT, "fetch_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"Saved -> {OUT}/ (fetched_at {log['fetched_at']})")


if __name__ == "__main__":
    main()
