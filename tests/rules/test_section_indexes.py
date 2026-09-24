"""The CR section packages index where their rules live (CR 400, 800, 903).

Those three sections have no module of their own, because their rules are
amendments to rules elsewhere: CR 400.7 is enforced wherever objects move,
CR 800.4e inside the combat damage guard, CR 903.8 inside the legality check.
Each package's docstring is therefore a table saying where each rule lives,
and the table is the navigation aid the numbering exists for - so it has to
stay true. A moved function or a corrected citation must not leave it
pointing at nothing.
"""

from __future__ import annotations

import pathlib
import re

import pytest

RULES = pathlib.Path(__file__).resolve().parents[2] / "mtgfish" / "rules"

ROW = re.compile(r"^(CR [0-9.a-z]+)\s+(.*)$")
PATH = re.compile(r"``([a-z0-9_./\s]+\.py)``")


def _rows(text: str) -> list[tuple[str, str]]:
    """The table rows, each with its continuation lines folded in."""
    rows: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            current = [match.group(1), match.group(2)]
            rows.append(current)
        elif line.startswith("==="):
            current = None
        elif current is not None and line.startswith("   "):
            current[1] += " " + line.strip()
    return [(rule, body) for rule, body in rows]


def _claims() -> list[tuple[str, str, str]]:
    """Every (package, rule, path) a section index asserts.

    A path is written either from the rules root ("kernel/game.py") or
    relative to the package the table is in ("cr613_layers.py"), whichever
    reads better in that table. Both are resolved here.
    """
    out = []
    for init in sorted(RULES.glob("*/__init__.py")):
        for rule, body in _rows(init.read_text()):
            for path in PATH.findall(body):
                out.append((init.parent.name, rule, re.sub(r"\s+", "", path)))
    return out


def _resolve(package: str, path: str) -> pathlib.Path | None:
    for candidate in (RULES / package / path, RULES / path):
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    return None


CLAIMS = _claims()


def test_the_indexes_are_not_empty():
    """A table that quietly stopped being parsed would pass everything else.

    ``kernel`` is deliberately absent: it is not a CR section, so its
    docstring lists modules rather than rule numbers and yields no claims.
    """
    packages = {package for package, _, _ in CLAIMS}
    assert {
        "cr100_game_concepts",
        "cr400_zones",
        "cr600_spells_and_abilities",
        "cr800_multiplayer",
        "cr903_commander",
    } <= packages, "a section table stopped being parsed"
    assert len(CLAIMS) > 50


@pytest.mark.parametrize(
    ("package", "rule", "path"),
    CLAIMS,
    ids=[f"{p}:{r}->{q}" for p, r, q in CLAIMS],
)
def test_each_index_entry_points_somewhere_real(package: str, rule: str, path: str):
    target = _resolve(package, path)
    assert target is not None, f"{package} sends {rule} to {path}, which does not exist"
    assert rule in target.read_text(), (
        f"{package} sends {rule} to {path}, which does not cite it"
    )
