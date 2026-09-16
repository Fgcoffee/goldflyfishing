"""The Comprehensive Rules, as data.

WotC publishes the whole rulebook as plain text. Parsing it gives an
authoritative list of every rule that exists - roughly 3,500 of them - which is
what turns "how much of the rules are implemented?" from an opinion into a
number.

This is the same trick as the keyword registry, applied to the rules
themselves. The engine cannot claim coverage it does not have, because the
checklist is not written by the engine's author.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_root, ensure

RULES_INDEX_URL = "https://magic.wizards.com/en/rules"

#: A rule number: "100", "100.1", "100.1a".
_RULE_RE = re.compile(r"^(\d{3})(?:\.(\d+)([a-z]?))?\.?\s+(.*)$")

#: The glossary and credits follow the numbered rules, but their headings also
#: appear in the table of contents near the top of the file - so stopping at the
#: first occurrence stops after the contents page and finds 147 rules instead of
#: 3,500. Nothing in the glossary starts with a three-digit rule number, so the
#: number pattern alone is a sufficient filter and no end marker is needed.


@dataclass(frozen=True, slots=True)
class Rule:
    """One numbered rule."""

    number: str
    text: str

    @property
    def section(self) -> int:
        """The hundreds block: 1 for the 100s, 6 for the 600s."""
        return int(self.number[0])

    @property
    def rule_group(self) -> str:
        """The three-digit rule this belongs to, e.g. "601" for "601.2b"."""
        return self.number.split(".")[0]

    @property
    def is_subrule(self) -> bool:
        return "." in self.number

    @property
    def is_lettered(self) -> bool:
        """Lettered subrules carry the actual mechanics; numbered ones head them."""
        return bool(self.number) and self.number[-1].isalpha()

    def __str__(self) -> str:
        return f"{self.number} {self.text[:70]}"


def default_path() -> Path:
    return data_root() / "comprehensive_rules.txt"


def download(path: Path | None = None) -> Path:
    """Fetch the current Comprehensive Rules text.

    The download URL carries the release date and changes with every rules
    update, so it is discovered from the rules page rather than hard-coded.
    """
    path = path or default_path()
    ensure(path.parent)

    headers = {"User-Agent": "mtgfish/0.1 (Commander simulator; local research tool)"}
    request = urllib.request.Request(RULES_INDEX_URL, headers=headers)
    html = urllib.request.urlopen(request, timeout=60).read().decode("utf8", "ignore")

    links = sorted(
        set(re.findall(r"https://media\.wizards\.com/\d+/downloads/MagicCompRules[^\"]*", html))
    )
    text_links = [link for link in links if link.endswith(".txt")]
    if not text_links:
        raise RuntimeError(f"no Comprehensive Rules .txt link found at {RULES_INDEX_URL}")

    url = text_links[-1].replace(" ", "%20")
    data = urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=120
    ).read()
    path.write_bytes(data)
    return path


def parse(path: Path | None = None) -> list[Rule]:
    """Parse the rules text into numbered rules.

    The file opens with a table of contents listing the same three-digit
    numbers as the body, so entries are deduplicated by keeping the last
    occurrence - the body text, which is the one with the actual rule in it.
    """
    path = path or default_path()
    text = path.read_text(encoding="utf8", errors="replace")

    rules: dict[str, Rule] = {}
    for raw in text.splitlines():
        line = raw.replace("\xa0", " ").strip()
        if not line:
            continue

        match = _RULE_RE.match(line)
        if not match:
            continue
        major, minor, letter, body = match.groups()
        if not body:
            continue

        number = major
        if minor:
            number += f".{minor}{letter or ''}"
        rules[number] = Rule(number=number, text=body.strip())

    return sorted(rules.values(), key=_sort_key)


def _sort_key(rule: Rule) -> tuple:
    parts = rule.number.split(".")
    major = int(parts[0])
    if len(parts) == 1:
        return (major, 0, "")
    tail = parts[1]
    digits = "".join(c for c in tail if c.isdigit())
    letter = "".join(c for c in tail if c.isalpha())
    return (major, int(digits or 0), letter)


def by_section(rules: list[Rule]) -> dict[int, list[Rule]]:
    out: dict[int, list[Rule]] = {}
    for rule in rules:
        out.setdefault(rule.section, []).append(rule)
    return out


#: What each hundreds block covers, for reporting.
SECTION_NAMES = {
    1: "Game Concepts",
    2: "Parts of a Card",
    3: "Card Types",
    4: "Zones",
    5: "Turn Structure",
    6: "Spells, Abilities, and Effects",
    7: "Additional Rules",
    8: "Multiplayer Rules",
    9: "Casual Variants",
}
