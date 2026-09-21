"""Cards that parse cleanly and then do nothing.

The parser reports whether a card was *read*. The rules tests report whether
the engine is *correct*. Between them sits the failure that has produced every
serious bug in this project: a card whose text parses, whose opcodes are right,
and which is never connected to the machinery that would execute them.

Those cards report green everywhere. "Enters tapped" produced a TAP effect that
nothing applied. Doubling Season produced a REPLACEMENT that no registry read.
Foundry Inspector produced a MODIFY_COST that no cost calculation matched. In
every case the parse was perfect and the card did nothing at all.

This finds them mechanically: build a small board the card could plausibly
affect, put the card onto it, and ask whether *anything* changed.

The board matters more than the check. An earlier version dropped the card
onto an empty battlefield and flagged Skullclamp, which is correctly inert with
nothing to equip. A tool that cries wolf gets ignored, so the probe board
carries what a permanent might act on:

* a creature to buff, equip, enchant or restrict,
* an artifact and a creature in hand, to price against cost modifiers,
* the card itself, attached if it is an Aura or Equipment.

Still a *suspicion* list, not a bug list - some cards genuinely need a game in
progress. It is ordered by how much the card is played, because a staple that
does nothing matters and an unplayed rare does not.
"""

from __future__ import annotations

import argparse

from ..data.db import CardDatabase
from ..parser import parse_card
from ..rules.cr600_spells_and_abilities.abilities import AbilityKind
from ..rules.cr600_spells_and_abilities.effects import EffectKind

#: Opcodes that only ever act through machinery outside the ability itself.
#: A card whose static text compiles to these and which then changes nothing
#: is the exact shape of every silent bug found so far.
WIRED_ELSEWHERE = frozenset(
    {
        EffectKind.REPLACEMENT,
        EffectKind.RESTRICTION,
        EffectKind.PERMISSION,
        EffectKind.MODIFY_COST,
        EffectKind.GRANT_ABILITY,
        EffectKind.MODIFY_PT,
        EffectKind.SET_PT,
        EffectKind.ADD_TYPE,
        EffectKind.SET_TYPE,
        EffectKind.REMOVE_TYPE,
        EffectKind.SET_COLOR,
        EffectKind.REMOVE_ABILITIES,
    }
)

#: What the probe board is made of. A creature to act on, and two spells to
#: price - one artifact, one creature - so a cost modifier of either flavour
#: shows up.
COMPANION = "Grizzly Bears"
PROBE_SPELLS = ("Sol Ring", "Serra Angel")


def _probe_state(sandbox) -> tuple:
    """Everything about the board a permanent could plausibly change."""
    from ..rules.cr600_spells_and_abilities.cr601_casting import cost_increases, cost_reductions

    game = sandbox.game
    game.invalidate_characteristics()

    companion = None
    prices = []
    characteristics = None
    for obj in game.objects.values():
        if obj.card is None:
            continue
        if obj.card.name == COMPANION and obj.zone.name == "BATTLEFIELD":
            companion = obj
        elif obj.card.name in PROBE_SPELLS and obj.zone.name == "HAND":
            prices.append(
                (
                    obj.card.name,
                    sum(cost_reductions(game, obj, obj.controller)),
                    sum(cost_increases(game, obj, obj.controller)),
                )
            )

    if companion is not None:
        chars = game.characteristics(companion)
        characteristics = (
            chars.power,
            chars.toughness,
            tuple(sorted(chars.keywords)),
            int(chars.types),
            int(chars.colors),
        )

    from ..rules.cr500_turn_structure.restrictions import _active, _active_permissions
    from ..rules.cr600_spells_and_abilities.cr614_replacement import static_replacements

    return (
        characteristics,
        tuple(sorted(prices)),
        len(game.continuous_effects),
        len(game.replacement_effects) + len(static_replacements(game)),
        len(_active(game)),
        len(_active_permissions(game)),
        len(game.pending_triggers),
    )


def _build_probe(sandbox) -> None:
    sandbox.reset()
    sandbox.put(COMPANION, "battlefield", 0)
    for name in PROBE_SPELLS:
        sandbox.put(name, "hand", 0)
    sandbox.give_mana(12)


def changes_anything(sandbox, card) -> bool:
    """Whether putting this card onto a plausible board changes it."""
    _build_probe(sandbox)
    before = _probe_state(sandbox)

    if sandbox.put(card.name, "battlefield", 0).get("error"):
        return True  # Could not be tested; not this tool's business.
    _attach_if_needed(sandbox, card)
    sandbox.settle()

    if _probe_state(sandbox) != before:
        return True
    # An activated ability the engine will offer is a contribution too.
    return bool([a for a in sandbox.legal() if a["kind"] != "PASS"])


def _attach_if_needed(sandbox, card) -> None:
    """Auras and Equipment do nothing until they are on something.

    Flagging Skullclamp for being inert with nothing to equip is exactly the
    false positive that makes a report worth ignoring.
    """
    face = card.faces[0]
    subtypes = set(getattr(face.type_line, "subtypes", ()) or ())
    if not subtypes & {"Aura", "Equipment", "Fortification"}:
        return

    game = sandbox.game
    host = next(
        (
            obj
            for obj in game.objects.values()
            if obj.card is not None
            and obj.card.name == COMPANION
            and obj.zone.name == "BATTLEFIELD"
        ),
        None,
    )
    attachment = next(
        (
            obj
            for obj in game.objects.values()
            if obj.card is not None and obj.card.name == card.name
        ),
        None,
    )
    if host is not None and attachment is not None:
        # The real keyword action, not a hand-written assignment: attachment
        # is two-sided - the Equipment records what it is on and the creature
        # records what is on it - and setting only one side left every
        # "equipped creature ..." filter matching nothing, which made this
        # tool report six false positives.
        from ..rules.cr100_game_concepts import actions

        actions.attach(game, attachment, host)
        game.invalidate_characteristics()


def suspicious(db: CardDatabase, limit: int) -> list[tuple[int, str, str]]:
    """Cards that parse completely, are permanents, and change nothing."""
    from ..parser.verdicts import VerdictStore
    from ..ui.sandbox import Sandbox

    sandbox = Sandbox(db=db, verdicts=VerdictStore())
    out: list[tuple[int, str, str]] = []

    for card in db.iter_by_play_rate(limit=limit):
        parsed = parse_card(card)
        if not parsed.fully_parsed:
            continue  # Already reported by the coverage report.

        abilities = [a for face in parsed.faces for a in face.abilities]
        if not abilities or not _is_permanent(card):
            continue

        statics = [
            node
            for ability in abilities
            if ability.kind is AbilityKind.STATIC
            for effect in ability.effects
            for node in effect.walk()
        ]
        wired = [node for node in statics if node.kind in WIRED_ELSEWHERE]
        if not wired:
            continue
        # A characteristic-defining ability changes only its own source, which
        # the probe does not watch.
        if all(
            ability.is_characteristic_defining
            for ability in abilities
            if ability.kind is AbilityKind.STATIC
        ):
            continue

        if changes_anything(sandbox, card):
            continue

        kinds = sorted({node.kind.name for node in wired})
        out.append((db.play_rank(card.oracle_id) or 0, card.name, ", ".join(kinds)))

    return sorted(out)


def _is_permanent(card) -> bool:
    from ..rules.kernel.enums import CardType

    type_line = getattr(card.faces[0], "type_line", None)
    if type_line is None:
        return False
    return not bool(type_line.types & (CardType.INSTANT | CardType.SORCERY))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args(argv)

    db = CardDatabase()
    db.registry()
    found = suspicious(db, args.depth)

    print(
        f"Permanents among the top {args.depth} played cards that parse "
        "completely and then change nothing on a board they should affect."
    )
    print(
        "A suspicion list, not a bug list - but every silent bug found so far "
        "would have shown up here.\n"
    )
    if not found:
        print("  (none)")
        return 0
    for rank, name, kinds in found[: args.limit]:
        print(f"  #{rank:<6} {name:38} {kinds}")
    print(f"\n{len(found)} in total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
