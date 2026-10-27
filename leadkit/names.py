"""Matching one agent across sources, and splitting names for mail merge.

Realtor.com, an imported CRM export and homes.com spell the same person several
ways: "Christopher Scott Maners", "Chris Maners PA", "Maners, Christopher".
The key is first and last name, lowercased, with licence and entity
suffixes dropped and common nicknames collapsed. That is as much as can be
matched safely without a licence number; a missed nickname is a missed
merge, never a wrong one.
"""
from __future__ import annotations

import re

SUFFIXES = {
    "pa", "llc", "inc", "jr", "sr", "ii", "iii", "iv", "realtor", "broker",
    "abr", "gri", "crs", "esq", "team", "group", "pllc", "lc", "corp",
}

NICKNAMES = {
    "chris": "christopher", "mike": "michael", "dave": "david", "davey": "david", "bob": "robert",
    "rob": "robert", "bobby": "robert", "jim": "james", "jimmy": "james", "tom": "thomas",
    "tommy": "thomas", "nick": "nicholas", "alex": "alexander", "dan": "daniel", "danny": "daniel",
    "joe": "joseph", "joey": "joseph", "tony": "anthony", "rick": "richard", "rich": "richard",
    "dick": "richard", "steve": "stephen", "steven": "stephen", "bill": "william", "billy": "william",
    "will": "william", "willy": "william", "ted": "theodore", "ed": "edward", "eddie": "edward",
    "fred": "frederick", "greg": "gregory", "jeff": "jeffrey", "ken": "kenneth", "larry": "lawrence",
    "matt": "matthew", "pat": "patrick", "pete": "peter", "phil": "philip", "ron": "ronald",
    "sam": "samuel", "tim": "timothy", "vince": "vincent", "kathy": "katherine", "kate": "katherine",
    "katie": "katherine", "cathy": "catherine", "liz": "elizabeth", "beth": "elizabeth",
    "betsy": "elizabeth", "sue": "susan", "suzy": "susan", "jen": "jennifer", "jenny": "jennifer",
    "becky": "rebecca", "debbie": "deborah", "deb": "deborah", "maggie": "margaret",
    "peggy": "margaret", "sandy": "sandra", "trish": "patricia", "patty": "patricia",
    "cindy": "cynthia",
}


def _dotted(raw: str | None) -> str:
    """"P.A." and "P. A." are the suffix PA, not two initials."""
    return re.sub(r"\bP\.\s?A\b\.?", "PA", raw or "", flags=re.I)


def _reorder(raw: str | None) -> str:
    """"Maners, Christopher" is last-name-first; "Sean Mooney, PA" is not."""
    text = _dotted(raw)
    if text.count(",") == 1:
        last, first = text.split(",")
        tail = {w.strip(".").lower() for w in first.split()}
        if tail and not tail & SUFFIXES:
            return f"{first.strip()} {last.strip()}"
    return text


def _words(raw: str | None) -> list[str]:
    text = re.sub(r"[.,]", " ", _reorder(raw).lower())
    text = re.sub(r"[^a-z\s'-]", " ", text)
    return [w for w in text.split() if w not in SUFFIXES]


def name_key(raw: str | None) -> str:
    words = _words(raw)
    if not words:
        return ""
    first = NICKNAMES.get(words[0], words[0])
    if len(words) == 1:
        return first
    return f"{first} {words[-1]}"


def search_name(raw: str) -> str:
    """The name as a search engine should get it: no licence suffix, no middle
    name. "Senada Adzem PA" finds nothing; "Senada Adzem" finds the profile."""
    words = [w for w in re.sub(r"[.,]", " ", _dotted(raw)).split()
             if w.lower() not in SUFFIXES and len(w) > 1]
    if len(words) <= 2:
        return " ".join(words)
    return f"{words[0]} {words[-1]}"


def split_name(raw: str | None) -> tuple[str, str]:
    """(first, last) for a mail merge, keeping the person's own capitalisation."""
    words = [w for w in _reorder(raw).replace(",", " ").split()
             if w.lower().strip(".") not in SUFFIXES]
    if not words:
        return "", ""
    return words[0], (words[-1] if len(words) > 1 else "")
