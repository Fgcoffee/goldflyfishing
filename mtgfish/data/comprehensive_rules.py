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

#: Page furniture the PDF extractor interleaves with the body.
_NOISE_RE = re.compile(r"^(Magic: The Gathering Comprehensive Rules|\d{1,3}\s*$)")

_GLOSSARY_RE = re.compile(r"^Glossary\s*$")

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
    """Parse the rules into numbered rules, joining rules that wrap.

    Two things make this harder than matching a number at the start of a line.

    A rule can span several lines. WotC's own .txt puts most rules on one line
    but wraps the long ones, and text extracted from the PDF wraps everything.
    Reading only the first line silently truncated 224 rules in the shipped
    text - CR 101.2 lost its Example and 180 of its 325 characters - so
    continuation lines are joined until the next heading.

    That makes the next problem load-bearing: a continuation can *begin* with
    a rule number, because the rules cross-reference each other constantly
    ("...follows the rules for paying alternative costs in rules 601.2b and
    601.2f-h."). Headings are strictly increasing, so a candidate that goes
    backwards is a cross-reference rather than a heading.

    The file opens with a table of contents listing the same three-digit
    numbers as the body, so a bare three-digit number may go backwards once -
    that is the body restarting - and entries are deduplicated by keeping the
    last occurrence, which is the body text.
    """
    path = path or default_path()
    return _parse_text(read_text(path))


def _parse_text(text: str) -> list[Rule]:
    lines = [raw.replace("\xa0", " ").strip() for raw in text.splitlines()]
    rules: dict[str, Rule] = {}
    number: str | None = None
    buf: list[str] = []
    last: tuple | None = None

    def flush() -> None:
        if number and buf:
            rules[number] = Rule(number=number, text=" ".join(buf).strip())

    for index, line in enumerate(lines):
        if not line or _NOISE_RE.match(line):
            continue
        # "Glossary" heads the contents page as well as the section after the
        # body, so the count is what tells them apart.
        if _GLOSSARY_RE.match(line) and len(rules) > 2000:
            break

        match = _RULE_RE.match(line)
        if match and match.group(4):
            major, minor, letter, body = match.groups()
            candidate = major + (f".{minor}{letter or ''}" if minor else "")
            key = _number_key(candidate)
            if _is_heading(lines, index, candidate, minor, last, key):
                flush()
                number, buf, last = candidate, [body.strip()], key
                continue
        if number:
            buf.append(line)
    flush()

    return sorted(rules.values(), key=_sort_key)


def _is_heading(
    lines: list[str], index: int, candidate: str, minor: str | None, last, key
) -> bool:
    """Whether this line starts a rule, rather than continuing one.

    A wrapped line can begin with a rule number, because the rules
    cross-reference each other constantly - "(This is a state-based action.
    See rule 704. See also rule 903.10.)" wraps so that a line begins "704.
    See also...", which is indistinguishable from a section heading by shape
    alone.

    Two signals settle it. Subrules run in strictly increasing order, so a
    subrule number that goes backwards is a cross-reference. A three-digit
    section heading is always followed by its own first subrule, so "704." is
    a heading only when the next number in the file is "704.1" - which the
    cross-reference above is not, and which also lets the body restart after
    the table of contents has listed every section already.
    """
    if minor is not None:
        return last is None or key > last

    for following in lines[index + 1 : index + 40]:
        if not following:
            continue
        ahead = _RULE_RE.match(following)
        if ahead and ahead.group(4):
            if (ahead.group(1), ahead.group(2), ahead.group(3)) == (candidate, "1", ""):
                return True
            break

    # A section can have no subrules of its own - "600. General" heads the
    # 600s and the numbering starts at 601 - so a title-shaped body counts
    # too. A title is short and has no sentence in it, which is what separates
    # "General" from "See also rule 903.10.)".
    body = _RULE_RE.match(lines[index]).group(4).strip()
    return len(body) <= 40 and "." not in body and body[:1].isupper()


def _number_key(number: str) -> tuple:
    parts = number.split(".")
    major = int(parts[0])
    if len(parts) == 1:
        return (major, 0, "")
    tail = parts[1]
    digits = "".join(c for c in tail if c.isdigit())
    letter = "".join(c for c in tail if c.isalpha())
    return (major, int(digits or 0), letter)


def read_text(path: Path) -> str:
    """The rules as text, from either the .txt or the PDF WotC publishes.

    Both are shipped forms of the same document, and a release often arrives
    as one or the other, so the reader takes whichever is there rather than
    making the caller convert first.
    """
    if path.suffix.lower() == ".pdf":
        return _text_from_pdf(path)
    return path.read_text(encoding="utf8", errors="replace")


def _text_from_pdf(path: Path) -> str:
    import warnings

    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            f"reading {path.name} needs pypdf; pip install pypdf"
        ) from exc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reader = pypdf.PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)


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
