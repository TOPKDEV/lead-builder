"""Build leadkit/data/places.tsv.gz, the offline catalog of US places.

Why: people pick areas to build a list from, and a paid lookup for every
keystroke (that sometimes says "no listings") is the wrong tool. This script
downloads free, public-domain Census data once and boils it down to one small
file the app searches locally.

Sources (no API key needed):
- Populations (2020 Census) from the Census TIGERweb service: counties,
  incorporated places, census-designated places and ZIP areas. The main
  api.census.gov endpoint now wants a key, TIGERweb carries the same POP100.
- ZIP -> place and ZIP -> county overlaps from the Census 2020 relationship
  files (used to say which city or county each ZIP mostly sits in).

Run from the repo root:
    .venv/bin/python tools/build_places.py [cache_dir]

Downloads are cached in cache_dir (default: tools/.cache, ignored by git if
you add it) so a re-run is instant. Everything is parsed as data only.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "leadkit", "data", "places.tsv.gz")

TIGER = ("https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020/"
         "tigerWMS_Census2020/MapServer/%d/query")
LAYERS = {"county": 82, "place_inc": 26, "place_cdp": 28, "zcta": 84}
REL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
REL_FILES = {"place": "tab20_zcta520_place20_natl.txt",
             "county": "tab20_zcta520_county20_natl.txt"}

# FIPS -> USPS. Only states, DC and PR; other territories aren't in the data.
STATES = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}

# A ZIP is called "in" a place when the place covers at least this share of it.
PLACE_SHARE = 0.02


def get_json(url: str, params: dict, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    return {}


def tiger_layer(name: str, fields: str, cache: str) -> list:
    """All rows of one TIGERweb layer (paged), cached as JSON."""
    path = os.path.join(cache, "tiger_%s.json" % name)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    rows = []
    page = 2000
    while True:
        data = get_json(TIGER % LAYERS[name], {
            "where": "1=1", "outFields": fields, "returnGeometry": "false",
            "orderByFields": "OBJECTID", "resultOffset": len(rows),
            "resultRecordCount": page, "f": "json"})
        got = [f["attributes"] for f in data.get("features", [])]
        rows += got
        print("  %s: %d rows" % (name, len(rows)), file=sys.stderr)
        if not got or not data.get("exceededTransferLimit"):
            break
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    return rows


def download(fname: str, cache: str) -> str:
    path = os.path.join(cache, fname)
    if not os.path.exists(path):
        print("  downloading %s" % fname, file=sys.stderr)
        r = requests.get(REL + fname, timeout=300)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
    return path


def read_rel(path: str):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="|"):
            yield row


# ---- names ---------------------------------------------------------------

_GOV = re.compile(r"\s+(metropolitan|metro|consolidated|unified|urban)\s+(county\s+)?government\b.*$", re.I)


def clean_city(name: str) -> str:
    """BASENAME already drops "city", "town", "CDP" and friends; this handles
    the leftovers: consolidated governments, "(balance)" and alternate names."""
    n = re.sub(r"\s*\(balance\)\s*$", "", name).strip()
    alt = re.search(r"\(([^)]+)\)\s*$", n)  # "San Buenaventura (Ventura)"
    if alt:
        n = alt.group(1)
    if _GOV.search(n):
        # "Nashville-Davidson metropolitan government", "Louisville/Jefferson County ..."
        n = _GOV.sub("", n)
        n = re.split(r"[/-]", n)[0]
    n = n.split("/")[0]  # "Hartsville/Trousdale County"
    n = re.sub(r"^Urban\s+", "", n)
    return n.strip()


# ---- build ---------------------------------------------------------------

def build(cache: str) -> list:
    os.makedirs(cache, exist_ok=True)
    print("Populations from TIGERweb...", file=sys.stderr)
    counties = tiger_layer("county", "GEOID,NAME,STATE,POP100", cache)
    places = (tiger_layer("place_inc", "GEOID,NAME,BASENAME,STATE,POP100", cache)
              + tiger_layer("place_cdp", "GEOID,NAME,BASENAME,STATE,POP100", cache))
    zctas = tiger_layer("zcta", "GEOID,ZCTA5,POP100,AREALAND", cache)

    out = []  # (kind, code, state, pop, ctx)

    # counties (independent cities are covered by the city entry)
    county_label = {}
    for c in counties:
        st = STATES.get(c["STATE"])
        if not st or c["NAME"].endswith(" city"):
            if st:
                county_label[c["GEOID"]] = None
            continue
        label = "%s, %s" % (c["NAME"], st)
        county_label[c["GEOID"]] = label
        out.append(["county", label, st, int(c["POP100"] or 0), ""])

    # ZIP overlaps: land area of each place / county inside each ZCTA
    print("ZIP relationship files...", file=sys.stderr)
    zip_land = {}
    zip_place = defaultdict(lambda: defaultdict(int))
    for r in read_rel(download(REL_FILES["place"], cache)):
        z, p = r["GEOID_ZCTA5_20"], r["GEOID_PLACE_20"]
        if z and p:
            zip_place[z][p] += int(r["AREALAND_PART"] or 0)
            zip_land[z] = int(r["AREALAND_ZCTA5_20"] or 0)
    zip_county = defaultdict(lambda: defaultdict(int))
    for r in read_rel(download(REL_FILES["county"], cache)):
        z, c = r["GEOID_ZCTA5_20"], r["GEOID_COUNTY_20"]
        if z and c:
            zip_county[z][c] += int(r["AREALAND_PART"] or 0)
            zip_land[z] = int(r["AREALAND_ZCTA5_20"] or 0)

    # cities: keep the larger when the same name shows up twice in a state
    best = {}
    geoid_label = {}
    for p in places:
        st = STATES.get(p["STATE"])
        pop = int(p["POP100"] or 0)
        if not st or pop <= 0:
            continue
        name = clean_city(p["NAME"] if p["NAME"].endswith("(balance)") else p["BASENAME"] or p["NAME"])
        if not name:
            continue
        label = "%s, %s" % (name, st)
        geoid_label[p["GEOID"]] = label
        if label not in best or pop > best[label][3]:
            best[label] = ["city", label, st, pop, "", p["GEOID"]]

    # each city's county: where most of its ZIP land lies
    votes = defaultdict(lambda: defaultdict(int))
    for z, parts in zip_place.items():
        cs = zip_county.get(z)
        if not cs:
            continue
        top = max(cs, key=cs.get)
        for p, area in parts.items():
            votes[p][top] += area
    for label, row in best.items():
        v = votes.get(row[5])
        if v:
            cl = county_label.get(max(v, key=v.get))
            if cl:
                row[4] = cl.rsplit(",", 1)[0]
        out.append(row[:5])

    # ZIPs: the place covering most of it, else the county
    for z in zctas:
        code = z["GEOID"]
        pop = int(z["POP100"] or 0)
        cs = zip_county.get(code)
        if not cs:
            continue
        top = max(cs, key=cs.get)
        st = STATES.get(top[:2])
        if not st:
            continue
        ctx = ""
        parts = zip_place.get(code)
        if parts:
            pid = max(parts, key=parts.get)
            if parts[pid] >= PLACE_SHARE * (zip_land.get(code) or 1):
                ctx = geoid_label.get(pid, "")
        if not ctx:
            ctx = county_label.get(top) or ""
        out.append(["zip", code, st, pop, ctx])
    return out


def write(rows: list) -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows.sort(key=lambda r: (r[0], -r[3], r[1]))
    # mtime=0 keeps the file byte-identical between runs
    with open(OUT, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            for r in rows:
                line = "\t".join([r[0], r[1], r[2], str(r[3]), r[4]]) + "\n"
                gz.write(line.encode("utf-8"))


def main() -> None:
    cache = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, ".cache")
    rows = build(cache)
    write(rows)
    counts = defaultdict(int)
    for r in rows:
        counts[r[0]] += 1
    print("wrote %s (%d bytes): %s" % (OUT, os.path.getsize(OUT), dict(counts)))


if __name__ == "__main__":
    main()
