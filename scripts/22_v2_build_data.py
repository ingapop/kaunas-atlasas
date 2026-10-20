"""
Assemble the atlas v2 site data (v2/data/) from the pipeline outputs.

Inputs (all produced by earlier scripts – this one only reshapes, never computes
indicators of its own beyond the census context ratios below):
  data/processed/geo/seniunijos.geojson, apylinkes.geojson   (stage 1, data-analyst)
  data/processed/v2_rodikliai.json                          (stage 2, data-analyst; optional)
  v2/data/programa.json                                     (scripts/20_programa_struktura.py)

Outputs (what v2/index.html loads):
  v2/data/seniunijos.geojson, v2/data/apylinkes.geojson  – properties trimmed to what the page shows
  v2/data/rodikliai.json                                 – indicator catalogue + values (contract below)

Until stage 2 lands, rodikliai.json carries only the census 2021 context
ratios (density, share 0–14, share 65+) computed from the populations already
in the geo files, and `"busena": "DEMO"` so the page shows a draft banner.

rodikliai.json contract (same as data/processed/v2_rodikliai.json):
  { sugeneruota, busena, rodikliai: [{id, pavadinimas, vienetas, aprasymas, kryptis, saltinis, data, faktai}],
    reiksmes: {seniunijos: {id: {rodiklis: {reiksme, kiekis}}}, apylinkes: {...}},
    miestas: {rodiklis: {reiksme, kiekis}} }

Run from dashboardai/atlasas/:  python3 scripts/22_v2_build_data.py
"""
import json
from datetime import date
from pathlib import Path

GEO = Path("data/processed/geo")
STAGE2 = Path("data/processed/v2_rodikliai.json")
OUT = Path("v2/data")

KEEP = {
    "seniunijos": ["id", "pavadinimas", "plotas_km2", "gyventojai_2021", "gyv_0_14", "gyv_65_plius", "apylinkiu_sk"],
    "apylinkes": ["id", "nr", "pavadinimas", "seniunija", "plotas_km2", "gyventojai_2021", "gyv_0_14", "gyv_65_plius"],
}

# Census 2021 context ratios – population-based, no facility data involved.
CONTEXT = [
    {"id": "tankis", "tipas": "Tenka 1 km²", "skaiciavimas": "gyventojų skaičius ÷ teritorijos plotas (km²)",
     "pavadinimas": "Gyventojų tankis", "vienetas": "gyv./km²",
     "aprasymas": "Kiek gyventojų tenka vienam kvadratiniam kilometrui (2021 m.).",
     "kryptis": "kontekstas", "saltinis": "2021 m. gyventojų ir būstų surašymas, 100 m gardelė (Valstybės duomenų agentūra)",
     "data": "2021-01-01", "faktai": "F-082"},
    {"id": "vaiku_dalis", "tipas": "Gyventojų dalis", "skaiciavimas": "0–14 m. vaikų skaičius ÷ visų gyventojų skaičius × 100",
     "pavadinimas": "Vaikų dalis", "vienetas": "proc. gyventojų (0–14 m.)",
     "aprasymas": "Kokią gyventojų dalį sudaro 0–14 metų vaikai.",
     "kryptis": "kontekstas", "saltinis": "2021 m. gyventojų ir būstų surašymas, 100 m gardelė (Valstybės duomenų agentūra)",
     "data": "2021-01-01", "faktai": None},
    {"id": "senjoru_dalis", "tipas": "Gyventojų dalis", "skaiciavimas": "65 m. ir vyresnių gyventojų skaičius ÷ visų gyventojų skaičius × 100",
     "pavadinimas": "Vyresnio amžiaus gyventojų dalis", "vienetas": "proc. gyventojų (65+ m.)",
     "aprasymas": "Kokią gyventojų dalį sudaro 65 metų ir vyresni žmonės.",
     "kryptis": "kontekstas", "saltinis": "2021 m. gyventojų ir būstų surašymas, 100 m gardelė (Valstybės duomenų agentūra)",
     "data": "2021-01-01", "faktai": None},
]
MIN_POP = 500  # below this a share is too unstable to colour – published as null

# Indicators kept out of the site until their data is publishable (faktai.md status).
HIDE = {"kultura"}  # F-075 ginčytina – OSM coverage too thin


def context_values(p):
    pop, km2 = p.get("gyventojai_2021") or 0, p.get("plotas_km2") or 0
    ok = pop >= MIN_POP
    return {
        "tankis": {"reiksme": round(pop / km2) if km2 and ok else None, "kiekis": pop},
        "vaiku_dalis": {"reiksme": round(100 * p["gyv_0_14"] / pop, 1) if ok else None, "kiekis": p["gyv_0_14"]},
        "senjoru_dalis": {"reiksme": round(100 * p["gyv_65_plius"] / pop, 1) if ok else None, "kiekis": p["gyv_65_plius"]},
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    values, city = {}, {"gyv": 0, "km2": 0.0, "v": 0, "s": 0}
    for level, keep in KEEP.items():
        gj = json.loads((GEO / f"{level}.geojson").read_text(encoding="utf-8"))
        values[level] = {}
        for f in gj["features"]:
            p = f["properties"]
            values[level][p["id"]] = context_values(p)
            if level == "seniunijos":
                city["gyv"] += p["gyventojai_2021"]; city["km2"] += p["plotas_km2"]
                city["v"] += p["gyv_0_14"]; city["s"] += p["gyv_65_plius"]
            f["properties"] = {k: p.get(k) for k in keep}
        (OUT / f"{level}.geojson").write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    out = {
        "sugeneruota": date.today().isoformat(),
        "busena": "DEMO",
        "rodikliai": list(CONTEXT),
        "reiksmes": values,
        "miestas": {
            "tankis": {"reiksme": round(city["gyv"] / city["km2"]), "kiekis": city["gyv"]},
            "vaiku_dalis": {"reiksme": round(100 * city["v"] / city["gyv"], 1), "kiekis": city["v"]},
            "senjoru_dalis": {"reiksme": round(100 * city["s"] / city["gyv"], 1), "kiekis": city["s"]},
        },
    }

    if STAGE2.exists():
        s2 = json.loads(STAGE2.read_text(encoding="utf-8"))
        # Stage 2 wins on duplicate ids (it has its own census shares); hidden ids are dropped.
        out["busena"] = s2.get("busena") or "TIKRINAMA"
        out["sugeneruota"] = s2.get("sugeneruota", out["sugeneruota"])
        out["slenksciai"] = s2.get("slenksciai")
        s2_ids = {r["id"] for r in s2["rodikliai"]}
        out["rodikliai"] = [r for r in s2["rodikliai"] if r["id"] not in HIDE] + \
                           [r for r in out["rodikliai"] if r["id"] not in s2_ids]
        for level in values:
            for uid, vals in s2["reiksmes"].get(level, {}).items():
                out["reiksmes"][level].setdefault(uid, {}).update(vals)
        out["miestas"].update(s2.get("miestas", {}))
        for level in out["reiksmes"].values():
            for vals in level.values():
                for h in HIDE:
                    vals.pop(h, None)
        for h in HIDE:
            out["miestas"].pop(h, None)

    (OUT / "rodikliai.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"busena={out['busena']}; indicators={[r['id'] for r in out['rodikliai']]}")
    print("city:", out["miestas"])


if __name__ == "__main__":
    main()
