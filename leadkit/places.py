"""Offline catalog of US counties, cities and ZIP codes, with instant search.

Why: people pick areas to build a list from. Asking a paid API whether
"Weston FL" has listings is slow and sometimes wrongly says no, so the picker
searches this local file instead (built by tools/build_places.py from public
Census data). The `code` of a result is what later gets sent as the location to
Realtor.com / Zillow: "Broward County, FL", "Weston, FL" or "33327".

The file loads once, on first use, and stays in memory.
"""
from __future__ import annotations

import bisect
import gzip
import heapq
import os
import re
import threading
import unicodedata

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "places.tsv.gz")

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

_LOCK = threading.Lock()
_DB = None  # loaded on first use


def _norm(s: str) -> str:
    """Lowercase, no accents, no punctuation: "St. Pete's-Beach" -> "st petes beach"."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"\bsaint\b", "st", s)


_STATE_BY_NAME = {_norm(n): a for a, n in STATE_NAMES.items()}
_STATE_ABBRS = {a.lower(): a for a in STATE_NAMES}


class _Db:
    """Rows as parallel lists, plus the lookups a search needs."""

    def __init__(self) -> None:
        self.kind, self.code, self.state, self.pop, self.ctx = [], [], [], [], []
        self.name, self.short = [], []   # normalized name; and without "county" etc.
        self.by_code = {}
        words = []                       # (word, row) for cities and counties
        zips = []                        # (zip, row)
        with gzip.open(DATA, "rt", encoding="utf-8") as f:
            for line in f:
                kind, code, state, pop, ctx = line.rstrip("\n").split("\t")
                i = len(self.code)
                self.kind.append(kind)
                self.code.append(code)
                self.state.append(state)
                self.pop.append(int(pop))
                self.ctx.append(ctx)
                self.by_code[code] = i
                if kind == "zip":
                    zips.append((code, i))
                    self.name.append("")
                    self.short.append("")
                    continue
                nm = _norm(code.rsplit(",", 1)[0])
                self.name.append(nm)
                self.short.append(re.sub(r" (county|parish|borough|municipality|census area)$", "", nm))
                for w in set(nm.split()):
                    words.append((w, i))
        words.sort()
        self.words = [w for w, _ in words]
        self.word_rows = [i for _, i in words]
        zips.sort()
        self.zips = [z for z, _ in zips]
        self.zip_rows = [i for _, i in zips]

    def entry(self, i: int) -> dict:
        kind, pop, ctx = self.kind[i], self.pop[i], self.ctx[i]
        people = "{:,} people".format(pop) if pop else ""
        detail = " · ".join(x for x in ((ctx,) if kind != "county" else ()) + (people,) if x)
        return {"code": self.code[i], "label": self.code[i], "kind": kind,
                "state": self.state[i], "detail": detail, "pop": pop}


def _db() -> _Db:
    global _DB
    if _DB is None:
        with _LOCK:
            if _DB is None:
                _DB = _Db()
    return _DB


def _split_state(tokens: list) -> tuple:
    """("weston", "florida") -> (["weston"], "FL"). The state must be the end
    of the query and something must be left before it."""
    for n in (3, 2, 1):
        if len(tokens) > n:
            tail = " ".join(tokens[-n:])
            st = _STATE_BY_NAME.get(tail) or (_STATE_ABBRS.get(tail) if n == 1 else None)
            if st:
                return tokens[:-n], st
    return tokens, ""


def _search_zip(db: _Db, digits: str, state: str, limit: int) -> list:
    lo = bisect.bisect_left(db.zips, digits)
    hi = bisect.bisect_left(db.zips, digits + "~")
    rows = [db.zip_rows[k] for k in range(lo, hi)
            if not state or db.state[db.zip_rows[k]] == state]
    best = heapq.nsmallest(limit, rows, key=lambda i: (db.code[i] != digits, -db.pop[i], db.code[i]))
    return [db.entry(i) for i in best]


def _search_names(db: _Db, tokens: list, state: str, limit: int) -> list:
    anchor = max(tokens, key=len)
    lo = bisect.bisect_left(db.words, anchor)
    hi = bisect.bisect_left(db.words, anchor + "￿")
    cand = set(db.word_rows[lo:hi])
    q = " ".join(tokens)
    scored = []
    for i in cand:
        if state and db.state[i] != state:
            continue
        words = db.name[i].split()
        if not all(any(w.startswith(t) for w in words) for t in tokens):
            continue
        if q == db.name[i]:
            tier = 0
        elif q == db.short[i] or db.name[i].startswith(q):
            tier = 1
        else:
            tier = 2
        scored.append((tier, -db.pop[i], i))
    scored.sort()
    # With no state given, a few same-named towns ("Miami" in FL, OK, AZ...)
    # would crowd out everything else, so only the two biggest count as exact.
    out, seen = [], {}
    for tier, _, i in scored:
        if tier == 0 and not state:
            seen[db.name[i]] = seen.get(db.name[i], 0) + 1
            if seen[db.name[i]] > 2:
                tier = 1
        out.append((tier, i))
    out.sort(key=lambda t: (t[0], -db.pop[t[1]]))
    return [db.entry(i) for _, i in out[:limit]]


def search(q: str, limit: int = 8) -> list:
    """Places matching what someone has typed so far, best first."""
    db = _db()
    tokens = _norm(q or "").split()
    if not tokens or limit <= 0:
        return []
    for use_state in (True, False):
        rest, state = _split_state(tokens) if use_state else (tokens, "")
        if use_state and not state:
            continue
        digits = [t for t in rest if t.isdigit()]
        if digits:
            res = _search_zip(db, digits[0], state, limit) if len(rest) == 1 and len(digits[0]) <= 5 else []
        else:
            res = _search_names(db, rest, state, limit)
        if res:
            return res
    return []


def get(code: str):
    """The entry whose code is exactly `code`, or None."""
    db = _db()
    i = db.by_code.get(code)
    return db.entry(i) if i is not None else None
