"""
Geocode Kaunas social-services / elderly-care facility addresses via Nominatim
(OpenStreetMap's public geocoder - no key required, rate-limited to 1 req/sec).

Address list was manually collected from https://www.kaunospc.lt/kontaktai-2/
(Kauno miesto socialinių paslaugų centras contact page) on 2026-09-14 - this
municipal social services center's district offices + specialized day/crisis
centers are the only publicly listed facility-level social-services locations
found; population-level "how many people need this" data is not public (see
research notes).

Output: data/processed/social_facilities.csv
"""
import csv
import json
import time
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "kaunas-dashboard-research/1.0 (contact: inga.popovaite@gmail.com)"

FACILITIES = [
    ("Kauno m. socialinių paslaugų centras (administracija)", "Partizanų g. 38D, Kaunas", "administration"),
    ("Bendruomeninių paslaugų skyrius - maisto pagalba (Petrašiūnų-Gričiupio r.)", "Gričiupio g. 11, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Aleksoto r.)", "Veiverių g. 132, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Centro-Žaliakalnio r.)", "Šv. Gertrūdos g. 7, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Dainavos r.)", "V. Krėvės pr. 41A, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Eigulių r.)", "P. Plechavičiaus g. 9A, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Panemunės-Šančių r.)", "Vaidoto g. 115, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Šilainių r.)", "Baltų pr. 53, Kaunas", "district_office"),
    ("Bendruomeninių paslaugų skyrius (Vilijampolės r.)", "A. Stulginskio g. 61, Kaunas", "district_office"),
    ("Šeimos paslaugų skyrius (pagrindinis)", "R. Kalantos g. 57, Kaunas", "family_services"),
    ("Psichologinės pagalbos skyrius", "Nemuno g. 29, Kaunas", "family_services"),
    ("Šeimos krizių centras (visą parą)", "Birutės g. 29A, Kaunas", "crisis_center"),
    ("Laikino apnakvindinimo paslaugos", "R. Kalantos g. 55, Kaunas", "temp_housing"),
    ("Dainavos dienos globos centras", "Partizanų g. 38D, Kaunas", "senior_day_center"),
    ("Dienos centras \"Demencija\"", "Kurtinių g. 1D, Kaunas", "senior_day_center"),
]

OUT_CSV = "data/processed/social_facilities.csv"


def geocode(address):
    q = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "lt"})
    req = urllib.request.Request(f"{NOMINATIM_URL}?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if not data:
        return None, None
    return float(data[0]["lat"]), float(data[0]["lon"])


def main():
    rows = []
    for name, address, category in FACILITIES:
        lat, lon = geocode(address)
        status = "ok" if lat else "GEOCODE_FAILED"
        print(f"{status:16s} {address:40s} -> {lat}, {lon}")
        rows.append({"name": name, "address": address, "category": category, "lat": lat, "lon": lon})
        time.sleep(1.1)  # Nominatim usage policy: max 1 req/sec

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "address", "category", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved {len(rows)} facilities -> {OUT_CSV}")


if __name__ == "__main__":
    main()
