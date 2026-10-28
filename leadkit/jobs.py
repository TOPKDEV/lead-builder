"""A job is one list being built: its settings in jobs/<name>/job.json and
its data in jobs/<name>/leads.db."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from leadkit import settings
from leadkit.store import Store, now

ADDONS = {
    "emails": "Find each agent's email on their brokerage or personal site",
    "deep_emails": "Second email pass in a real Chrome window for sites that block the first (slow, home computer only)",
    "verify_emails": "Check every email is deliverable before you send",
    "verify_phones": "Check each phone is the agent's own, and whether it's a cell, landline or VoIP",
    "volume": "Look up listings each agent sold in the last 12 months on homes.com (Monid)",
    "fix_phones": "When a number is an office line, a landline or someone else's, find the agent's own cell and swap it in",
}
# The order steps run in. Build jobs count and fetch listings first; every job then runs the
# add-ons it asked for. Volume comes before phones so phone checks can be
# limited to producers. The Chrome pass is by far the slowest, so it goes
# last and everything else is usable while it runs; verification and phone
# steps run again after it, for only the agents it recovered.
STEP_ORDER = ["count", "listings", "agents", "emails", "verify_emails", "volume", "verify_phones",
              "fix_phones", "deep_emails", "verify_emails", "verify_phones", "fix_phones"]


@dataclass
class Job:
    name: str
    mode: str                                   # "build" or "enrich"
    markets: list[dict] = field(default_factory=list)
    min_listings: int = 1
    min_price: int | None = None
    max_price: int | None = None
    property_types: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    addons: list[str] = field(default_factory=list)
    producer_min: int = 5
    # Which agents the phone steps cover: "all", "emails" (anyone with a
    # usable email), or "producers" (sold producer_min+ listings last year).
    phones_for: str = "all"
    source_file: str | None = None
    column_map: dict = field(default_factory=dict)
    quote: dict | None = None
    approved_budget: float | None = None
    approved_at: str | None = None
    created_at: str = field(default_factory=now)

    @property
    def dir(self) -> Path:
        return settings.JOBS_DIR / self.name

    @property
    def export_dir(self) -> Path:
        return settings.EXPORTS_DIR / self.name

    def store(self) -> Store:
        return Store(self.dir / "leads.db")

    def steps(self) -> list[str]:
        base = ["count", "listings", "agents"] if self.mode == "build" else []
        steps = [s for s in STEP_ORDER if s in base or s in self.addons]
        if "deep_emails" not in steps:
            # The second verification and phone passes only cover what a
            # deep pass found.
            steps = [s for i, s in enumerate(steps) if s not in steps[:i]]
        return steps

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "job.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s:
        raise ValueError("Give the job a name with some letters or numbers in it.")
    return s


def load(name: str) -> Job:
    path = settings.JOBS_DIR / slug(name) / "job.json"
    if not path.exists():
        names = ", ".join(all_names()) or "none yet"
        raise SystemExit(f"No job called {name!r}. Jobs: {names}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # A job saved by an older version may carry settings that no longer exist.
    known = {f.name for f in fields(Job)}
    return Job(**{k: v for k, v in data.items() if k in known})


def all_names() -> list[str]:
    if not settings.JOBS_DIR.exists():
        return []
    return sorted(p.parent.name for p in settings.JOBS_DIR.glob("*/job.json"))
