"""People to leave out of every list: current clients, a list already being
worked, do-not-call numbers. One list per install, kept with the jobs so
updating the app doesn't touch it, and matched on name, email or phone.

A match leaves the job's agents table before any paid step reads it, so it
costs nothing and reaches no spreadsheet. The row is kept aside, not deleted:
take someone off the list and they come back with everything already paid for.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from leadkit import importer, settings
from leadkit.names import name_key
from leadkit.phones import normalize, pretty

TYPED = "Typed in"
_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def _path() -> Path:
    return settings.JOBS_DIR / "_skip" / "list.json"


def load() -> list[dict]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(entries: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=0), encoding="utf-8")


def _entry(source: str, name: str = "", emails=(), phones=()) -> dict | None:
    key = name_key(name) if name else ""
    emails = sorted({e.lower() for e in emails})
    phones = sorted({p for p in (normalize(x) for x in phones) if p})
    if not (key or emails or phones):
        return None
    token = hashlib.sha1(json.dumps([key, emails, phones]).encode()).hexdigest()[:12]
    return {"id": token, "source": source, "name": name.strip(), "key": key, "emails": emails, "phones": phones}


def _add(fresh: list[dict]) -> int:
    entries = load()
    seen = {e["id"] for e in entries}
    new = [e for e in fresh if e and e["id"] not in seen and not seen.add(e["id"])]
    if new:
        _save(entries + new)
    return len(new)


def parse_line(line: str, source: str = TYPED) -> dict | None:
    """"Jane Doe", "954-555-0100", "jane@x.com", or all three on one line."""
    emails = _EMAIL.findall(line)
    rest = _EMAIL.sub(" ", line)
    phones = _PHONE.findall(rest)
    name = _PHONE.sub(" ", rest)
    name = re.sub(r"\s+", " ", re.sub(r"[,;|\t]+", " ", name)).strip()
    return _entry(source, name, emails, phones)


def add_text(text: str) -> int:
    return _add([parse_line(line) for line in text.splitlines() if line.strip()])


def add_file(path: Path, source: str | None = None, mapping: dict | None = None) -> dict:
    """Every row of a spreadsheet. The name comes from its column; emails and
    phones are read from every cell, so a second phone column counts too."""
    header, rows = importer.read_rows(path)
    mapping = mapping or importer.detect_columns(header)
    source = source or path.name
    fresh = []
    for row in rows:
        get = lambda f: (row.get(mapping[f]) or "").strip() if f in mapping else ""  # noqa: E731
        name = get("name") or f"{get('first_name')} {get('last_name')}".strip()
        cells = " | ".join(str(v) for v in row.values() if v)
        emails = _EMAIL.findall(cells)
        phones = _PHONE.findall(_EMAIL.sub(" ", cells))
        if get("phone"):
            phones.append(get("phone"))
        fresh.append(_entry(source, name, emails, phones))
    found = [e for e in fresh if e]
    if rows and not found:
        raise ValueError("Couldn't find a name, email or phone in that file. Check it has a header row.")
    return {"rows": len(rows), "added": _add(found), "mapping": mapping, "source": source}


def remove(entry_id: str | None = None, source: str | None = None) -> None:
    _save([e for e in load() if e["id"] != entry_id and e["source"] != source])


def clear() -> None:
    _save([])


def view() -> dict:
    entries = load()
    sources: dict[str, int] = {}
    for e in entries:
        sources[e["source"]] = sources.get(e["source"], 0) + 1
    typed = [{"id": e["id"], "label": " · ".join([e["name"], *e["emails"], *map(pretty, e["phones"])][:3] if e["name"] else
                                                 [*e["emails"], *map(pretty, e["phones"])][:3])}
             for e in entries if e["source"] == TYPED]
    return {"count": len(entries), "files": [{"source": s, "count": n} for s, n in sources.items() if s != TYPED],
            "typed": typed[::-1]}


def _numbers(agent: dict) -> set[str]:
    raw = [agent.get("phone"), agent.get("phone_e164"), agent.get("phone_original"), agent.get("homes_phone"),
           *(agent.get("other_phones") or "").split(";")]
    return {n for n in (normalize(x.strip()) for x in raw if x and x.strip()) if n}


def reason_for(agent: dict, names: set, emails: set, phones: set) -> str | None:
    if agent.get("email") and agent["email"].lower() in emails:
        return "email on your skip list"
    if _numbers(agent) & phones:
        return "phone on your skip list"
    if name_key(agent.get("name")) in names:
        return "name on your skip list"
    return None


def apply(store) -> int:
    """Move anyone on the list out of the job, and anyone taken off it back.
    Returns how many are left out now."""
    entries = load()
    if not entries and not store.one("SELECT COUNT(*) FROM skipped_agents"):
        return 0
    names = {e["key"] for e in entries if e["key"]}
    emails = {x for e in entries for x in e["emails"]}
    phones = {x for e in entries for x in e["phones"]}
    for row in store.q("SELECT key, data FROM skipped_agents"):
        agent = json.loads(row["data"])
        if not reason_for(agent, names, emails, phones):
            cols = list(agent)
            store.write(f"INSERT OR IGNORE INTO agents ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                        [agent[c] for c in cols])
            store.write("DELETE FROM skipped_agents WHERE key = ?", (row["key"],))
    for agent in store.agents():
        why = reason_for(agent, names, emails, phones)
        if why:
            store.write("INSERT OR IGNORE INTO skipped_agents (key, reason, data) VALUES (?,?,?)",
                        (agent["key"], why, json.dumps(agent)))
            store.write("DELETE FROM agents WHERE key = ?", (agent["key"],))
    return store.one("SELECT COUNT(*) FROM skipped_agents") or 0


def left_out(store) -> list[dict]:
    return [{**json.loads(r["data"]), "phone_note": r["reason"], "phone_fix": None}
            for r in store.q("SELECT data, reason FROM skipped_agents")]
