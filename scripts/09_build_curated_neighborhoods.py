"""
Curate the 58 raw OSM neighborhood points down to ~24 named areas matching the
original document's table (copilot files/dashboard_idea.odt), per user decision
on 2026-09-14 ("curated ~24 named areas, recommended" over showing all 58 raw
points or rolling up to the 9 official seniūnijos).

Method: for each of the original 24 row labels, pick the matching OSM point(s)
and average their coordinates where the odt row itself names multiple places
(e.g. "Sargėnai / Vytėnai" -> average of the two OSM points for Sargėnai and
Vytėnai). All other raw OSM points (smaller sub-hamlets not named in the odt
table, e.g. Kazliškiai, Marvelė, Julijanava...) are intentionally dropped from
this curated list - they will fall to whichever curated point is nearest when
05_assign_to_neighborhoods.py is rerun against this file, which is the desired
behavior (it's exactly how the odt's own informal groupings work).

Two rows from the original 24 have no OSM anchor and needed a manual fix:
  - "Centras / Laisvės al." - Kaunas doesn't tag its functional center as a
    place=suburb node in OSM. Used the city-center coordinate from a Nominatim
    lookup of "Kaunas" instead (54.8982, 23.9045).
  - "Šiaurinis Žaliakalnis" - no distinct OSM anchor exists close to how the
    odt describes it; merged into "Žaliakalnis" rather than inventing a
    coordinate. Curated list therefore has 23 areas, not 24.

Output: data/processed/curated_neighborhoods.csv (name, lat, lon, source_note)
"""
import csv

RAW_CSV = "data/raw/osm_kaunas_neighborhoods.csv"
OUT_CSV = "data/processed/curated_neighborhoods.csv"

# curated_name -> list of raw OSM point names to average, OR a fixed (lat, lon) tuple
CURATED = {
    "Aleksotas": ["Aleksotas"],
    "Freda": ["Aukštoji Freda", "Žemoji Freda"],
    "Tirkiliškiai / Birutė": ["Tirkiliškiai", "Birutė I", "Birutė II"],
    "Naujamiestis": ["Naujamiestis"],
    "Senamiestis": ["Senamiestis"],
    "Centras / Laisvės al.": (54.8982139, 23.9044817),  # manual: Nominatim city-center lookup
    "Dainava": ["Dainava"],
    "Eiguliai": ["Eiguliai"],
    "Gričiupis": ["Gričiupis"],
    "Panemunė": ["Panemunė"],
    "Vaišvydava": ["Vaišvydava"],
    "Petrašiūnai": ["Petrašiūnai"],
    "Palemonas / Amaliai": ["Palemonas", "Amaliai"],
    "Aukštieji / Žemieji Šančiai": ["Aukštieji Šančiai", "Žemieji Šančiai"],
    "Romainiai": ["Romainiai"],
    "Sargėnai / Vytėnai": ["Sargėnai", "Vytėnai"],
    "Milikoniai": ["Milikoniai"],
    "Linkuva": ["Linkuva"],
    "Šilainių branduolys": ["Šilainiai"],
    "Vilijampolė": ["Vilijampolė"],
    "Lampėdžiai": ["Lampėdžiai"],
    "Kalniečiai": ["Kalniečiai"],
    "Žaliakalnis": ["Žaliakalnis"],  # includes former "Šiaurinis Žaliakalnis" - no separate anchor found
}


def main():
    raw = {}
    with open(RAW_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw.setdefault(row["name"], []).append((float(row["lat"]), float(row["lon"])))

    rows = []
    for name, spec in CURATED.items():
        if isinstance(spec, tuple):
            lat, lon = spec
            note = "manual coordinate"
        else:
            pts = []
            missing = []
            for raw_name in spec:
                if raw_name in raw:
                    pts.extend(raw[raw_name])
                else:
                    missing.append(raw_name)
            if missing:
                raise SystemExit(f"Missing raw OSM point(s) for '{name}': {missing}")
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            note = "avg of: " + ", ".join(spec) if len(spec) > 1 else "from OSM: " + spec[0]
        rows.append({"name": name, "lat": lat, "lon": lon, "source_note": note})

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "lat", "lon", "source_note"])
        w.writeheader()
        w.writerows(rows)

    print(f"Saved {len(rows)} curated areas -> {OUT_CSV}")


if __name__ == "__main__":
    main()
