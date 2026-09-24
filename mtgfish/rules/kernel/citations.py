"""Addressing the Comprehensive Rules by number, and noticing when they move.

The engine cites the rules constantly - some 1,300 comments carry a "CR 601.2b"
or similar. Every one of those was free text. Nothing checked that the rule
existed, nothing checked that it still said what the comment assumed, and a
renumbering in a new rules release invalidated them silently.

That failure is not hypothetical. Speed was CR 702.183 when this engine was
written; 702.183 is now "Tiered", and the eight comments explaining the speed
implementation point at an unrelated keyword. A reader trusting them is sent to
the wrong rule, and there was no way to find out except by looking each one up
by hand.

So citations are made addressable here:

``rule("601.2b")``        the rule, with WotC's exact wording
``cited_in_source()``     every citation the source makes, with its location
``check()``               citations that dangle, and rules whose text moved

The baseline in ``data/cr_baseline.json`` records the exact text of every rule
the engine cites, as of the rules release it was reviewed against. When a new
release lands, ``check`` diffs the shipped rules against that baseline and says
precisely which cited rules changed and how - which is the comparison that
tells you what to re-read, instead of re-reading everything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ...data.comprehensive_rules import Rule, default_path, parse

#: A citation as the source writes it: "CR 601.2b", "CR 205". The "CR " prefix
#: is required deliberately - a bare "601.2b" in prose is not a citation, and
#: matching bare numbers turned version strings and card text into rules.
CITATION_RE = re.compile(r"\bCR\s+(\d{3}(?:\.\d+[a-z]{0,2})?)")

#: Where the reviewed wording lives. Checked in, because the point is to diff
#: against it after a rules release replaces the text file.
#: This module sits at mtgfish/rules/kernel/, so the package root is three
#: levels up, not two.
MTGFISH_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = MTGFISH_ROOT / "data" / "cr_baseline.json"


def normalise(text: str) -> str:
    """Rule text with typesetting differences flattened out.

    Text extracted from the PDF is not character-identical to WotC's .txt
    even when the rule is word for word the same: the extractor pads em
    dashes and leaves a space where a hyphenated word wrapped, so
    "state-based" comes back as "state- based". Comparing raw strings made
    the August text and the September PDF differ in 2,491 rules when only
    four had actually changed - a report that noisy is one nobody reads.
    """
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s*([\u2014\u2013])\s*", r"\1", text)
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _rules() -> dict[str, Rule]:
    path = default_path()
    if not path.exists():
        return {}
    return {rule.number: rule for rule in parse(path)}


def available() -> bool:
    """Whether the rules text is on disk at all.

    Everything here degrades to "no opinion" rather than raising when it is
    missing, so a checkout without the rules text still runs.
    """
    return bool(_rules())


def rule(number: str) -> Rule | None:
    """The rule with this number, or None if no such rule exists."""
    return _rules().get(number)


def text(number: str) -> str:
    """WotC's exact wording for a rule, or "" if it does not exist."""
    found = rule(number)
    return found.text if found else ""


def exists(number: str) -> bool:
    return number in _rules()


def parent(number: str) -> str:
    """The rule a subrule hangs off: "601.2b" -> "601.2", "601.2" -> "601"."""
    if "." not in number:
        return ""
    head, tail = number.split(".", 1)
    digits = "".join(c for c in tail if c.isdigit())
    if tail != digits:
        return f"{head}.{digits}"
    return head


def resolve(number: str) -> Rule | None:
    """The rule, or the nearest ancestor that does exist.

    A citation to a subrule that has been absorbed into its parent still points
    somewhere useful, and saying "601.2 covers this now" beats saying nothing.
    """
    seen = number
    while seen:
        found = rule(seen)
        if found is not None:
            return found
        seen = parent(seen)
    return None


# ---------------------------------------------------------------------------
# Citations the source makes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Citation:
    """One "CR ..." reference, and where it was written."""

    number: str
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: CR {self.number}"


def cited_in_source(root: Path | None = None) -> list[Citation]:
    """Every CR citation in the Python source, in file order.

    Read out of the source text rather than declared in a registry, because a
    registry only knows about the citations somebody remembered to register,
    and the comments are where the citations actually are.
    """
    root = root or MTGFISH_ROOT
    out: list[Citation] = []
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        for number, line in _citations_in(source):
            out.append(Citation(number=number, path=str(path), line=line))
    return out


def _citations_in(source: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for index, line in enumerate(source.splitlines(), 1):
        for match in CITATION_RE.finditer(line):
            found.append((match.group(1), index))
    return found


# ---------------------------------------------------------------------------
# The baseline, and what has moved since
# ---------------------------------------------------------------------------


def load_baseline() -> dict[str, str]:
    """Rule number -> the wording this engine was written against."""
    if not BASELINE_PATH.exists():
        return {}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf8"))
    return data.get("rules", {})


def baseline_release() -> str:
    if not BASELINE_PATH.exists():
        return ""
    return json.loads(BASELINE_PATH.read_text(encoding="utf8")).get("release", "")


def build_baseline(citations: list[Citation] | None = None) -> dict:
    """The baseline document for the rules the source currently cites."""
    citations = citations if citations is not None else cited_in_source()
    numbers = sorted({c.number for c in citations})
    return {
        "release": release_line(),
        "note": (
            "Exact CR wording for every rule the engine cites, as reviewed. "
            "Regenerate with: python -m mtgfish.tools.rules baseline --write"
        ),
        "rules": {number: text(number) for number in numbers if exists(number)},
    }


def release_line() -> str:
    """The "effective as of" line from the rules text, as a version marker."""
    path = default_path()
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf8", errors="replace").splitlines()[:20]:
        cleaned = line.replace("\xa0", " ").strip()
        if cleaned.lower().startswith("these rules are effective"):
            return cleaned
    return ""


@dataclass(frozen=True, slots=True)
class Drift:
    """A cited rule whose text no longer matches the baseline."""

    number: str
    was: str
    now: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class Dangling:
    """A citation to a rule number that the shipped rules do not define."""

    number: str
    nearest: str
    citations: tuple[Citation, ...]


def check(citations: list[Citation] | None = None) -> dict:
    """Citations that dangle, and cited rules whose wording has changed.

    Returns a plain dict so the CLI, the tests and any report can share one
    computation rather than each re-deriving it slightly differently.
    """
    citations = citations if citations is not None else cited_in_source()
    baseline = load_baseline()

    by_number: dict[str, list[Citation]] = {}
    for citation in citations:
        by_number.setdefault(citation.number, []).append(citation)

    dangling: list[Dangling] = []
    drifted: list[Drift] = []
    for number, cites in sorted(by_number.items()):
        if not exists(number):
            nearest = resolve(number)
            dangling.append(
                Dangling(
                    number=number,
                    nearest=nearest.number if nearest else "",
                    citations=tuple(cites),
                )
            )
            continue
        was = baseline.get(number)
        now = text(number)
        if was is not None and normalise(was) != normalise(now):
            drifted.append(
                Drift(number=number, was=was, now=now, citations=tuple(cites))
            )

    return {
        "citations": citations,
        "cited_rules": sorted(by_number),
        "dangling": dangling,
        "drifted": drifted,
        "release": release_line(),
        "baseline_release": baseline_release(),
        "uncited_in_baseline": sorted(set(baseline) - set(by_number)),
    }
