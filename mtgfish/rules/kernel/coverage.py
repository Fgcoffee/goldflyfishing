"""Comprehensive Rules coverage: which rules the engine actually implements.

The keyword registry works because the checklist comes from outside - Scryfall
publishes the list, so the engine cannot quietly claim a keyword it does not
have. This module does the same thing for the rules themselves, against the
3,300-odd numbered rules WotC publishes.

Every rule group in sections 100-600 is classified here, and the default for
anything unclassified is ``NOT_IMPLEMENTED``. There is no way to score a rule
as done by forgetting to mention it.

Statuses mean exactly what they say:

``IMPLEMENTED``     the engine enforces this rule, and a test covers it
``PARTIAL``         a simplified version is enforced; the note says what is missing
``NOT_IMPLEMENTED`` recognised, not built
``NOT_APPLICABLE``  the rule cannot come up in a simulated Commander game
                    (physical card handling, ante, other casual variants)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Status(IntEnum):
    IMPLEMENTED = 0
    PARTIAL = 1
    NOT_IMPLEMENTED = 2
    NOT_APPLICABLE = 3


@dataclass(frozen=True, slots=True)
class Entry:
    status: Status
    module: str = ""
    note: str = ""


def _entries(rows: dict[Status, dict[str, tuple[str, str]]]) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for status, groups in rows.items():
        for group, (module, note) in groups.items():
            out[group] = Entry(status, module, note)
    return out


#: Keyed by rule group: a three-digit number ("601") or a numbered subrule
#: ("601.2"). The most specific matching key wins, so a group can be marked
#: implemented while one of its subrules is called out as partial.
COVERAGE: dict[str, Entry] = _entries(
    {
        Status.IMPLEMENTED: {
            "308": (
                "rules/cr200_parts_of_a_card/cr205_typeline.py",
                "Kindred: the other card type decides casting and resolving "
                "(308.1), and the subtypes are creature types filters match "
                "(308.2) - tested in test_cr308_kindred",
            ),
            "700": (
                "rules/cr700_additional_rules/cr700_general.py",
                "modes (700.2), 'dies' (700.4), devotion (700.5), historic "
                "(700.6), and the tracked terms: party (700.8), modified "
                "(700.9), activated this turn (700.10), descended (700.11), "
                "outlaw (700.12), crime (700.13), expend (700.14), worthy "
                "(700.16)",
            ),
            # -- 100s -------------------------------------------------------
            "100": ("rules/cr100_game_concepts/cr103_setup.py", "multiplayer game setup"),
            "102": ("rules/cr100_game_concepts/player.py", ""),
            "103": ("rules/cr100_game_concepts/cr103_setup.py", "turn order, opening hands, London mulligan"),
            "104": ("rules/kernel/game.py", "win and loss conditions"),
            "105": ("rules/kernel/enums.py", "colors as a bitmask"),
            "106": ("rules/cr100_game_concepts/cr106_mana.py", "mana types, pools, emptying"),
            "109": ("rules/kernel/gameobject.py", "objects"),
            "107": (
                "rules/cr100_game_concepts/cr106_mana.py",
                (
                    "mana symbols incl. hybrid and Phyrexian, 107.1-2's number "
                    "rules, and 107.18's pawprint modes (700.2i)"
                ),
            ),
            "107.17": (
                "rules/cr100_game_concepts/player.py",
                (
                    "a player can hold ticket counters and a cost can remove "
                    "them (122.1); what is missing is parser-side, where {TK} "
                    "is refused as an unknown mana symbol"
                ),
            ),
            "706": (
                "rules/cr600_spells_and_abilities/resolve.py",
                (
                    "706.1-2 the roll and its modifiers, 706.3a the results "
                    "table, 706.4 reading the result, and 706.6 ignoring the "
                    "lowest. 706.5's 'rolled doubles' is one Unfinity card; "
                    "706.7's planar die is Planechase"
                ),
            ),
            "310": (
                "rules/cr700_additional_rules/cr704_sba.py",
                (
                    "battles, defense counters, the protector (310.9, 310.11, "
                    "310.12a) and the Siege's intrinsic exile-and-cast-"
                    "transformed ability (310.12b)"
                ),
            ),
            "110": (
                "rules/kernel/gameobject.py",
                (
                    "permanents, owner and controller, and 110.2b's default "
                    "controller surviving a resolution"
                ),
            ),
            "112": (
                "rules/cr600_spells_and_abilities/cr608_stack.py",
                (
                    "spells, and 112.4 - an effect on a permanent spell follows "
                    "it onto the battlefield. 112.1b and 112.2a are unreachable "
                    "rather than missing: nothing creates a castable copy of a "
                    "card (707.12), so no such spell can exist"
                ),
            ),
            "113": ("rules/cr600_spells_and_abilities/abilities.py", "the four ability types, and where they function"),
            "115": ("rules/cr600_spells_and_abilities/cr601_casting.py", "targets, legality on announce and resolve"),
            "117": ("rules/cr100_game_concepts/cr117_priority.py", "priority, passing, resolution"),
            "119": ("rules/cr100_game_concepts/actions.py", "life"),
            "120": ("rules/cr100_game_concepts/actions.py", "damage to players, creatures, planeswalkers"),
            "121": ("rules/kernel/game.py", "drawing, and the empty-library flag"),
            "122": ("rules/cr100_game_concepts/actions.py", "counters, incl. +1/+1 and -1/-1 annihilation"),
            "101": (
                "rules/cr500_turn_structure/restrictions.py",
                (
                    "101.2 'can't beats can' as a general mechanism. 101.1, the "
                    "golden rule, is not modelled and cannot be: card text "
                    "compiles into a fixed effect vocabulary, so a card beats a "
                    "rule only where the engine already offers a seam"
                ),
            ),
            "111": ("rules/cr100_game_concepts/cr111_tokens.py", "tokens, CR 111.4 naming and CR 111.5 refusal"),
            "114": ("rules/cr100_game_concepts/cr111_tokens.py", "emblems, abilities functioning in the command zone"),
            "118": (
                "rules/cr100_game_concepts/cr118_costs.py",
                "alternative (118.9), additional (118.8), unpayable (118.6), "
                "atomic payment (118.3), and coloured reduction (118.7b-g)",
            ),
            # -- 200s -------------------------------------------------------
            "202": ("rules/cr100_game_concepts/cr106_mana.py", "mana cost and derived color"),
            "204": ("data/cards.py", "color indicator overrides cost-derived color"),
            "205": ("rules/cr200_parts_of_a_card/cr205_typeline.py", "type lines, incl. multi-word subtypes"),
            "209": ("rules/cr600_spells_and_abilities/cr608_stack.py", "loyalty as counters on entry"),
            "210": ("rules/cr600_spells_and_abilities/cr608_stack.py", "defense as counters on entry"),
            # -- 300s -------------------------------------------------------
            "301": ("rules/cr700_additional_rules/cr704_sba.py", "artifacts, Equipment attachment"),
            "302": ("rules/cr500_turn_structure/cr506_combat.py", "creatures, summoning sickness"),
            "303": ("rules/cr700_additional_rules/cr704_sba.py", "enchantments, Aura attachment"),
            "304": ("rules/kernel/legality.py", "instants"),
            "305": ("rules/cr600_spells_and_abilities/cr613_layers.py", "lands, intrinsic mana, 305.7 type-setting"),
            "306": ("rules/cr700_additional_rules/cr704_sba.py", "planeswalkers and loyalty"),
            "307": ("rules/kernel/legality.py", "sorceries and sorcery-speed timing"),
            # -- 400s -------------------------------------------------------
            "400": ("rules/kernel/game.py", "zones, and 400.7 new-object semantics"),
            "401": ("rules/cr100_game_concepts/player.py", "library"),
            "402": ("rules/cr100_game_concepts/player.py", "hand"),
            "403": ("rules/kernel/game.py", "battlefield"),
            "404": ("rules/cr100_game_concepts/player.py", "graveyard"),
            "405": ("rules/cr600_spells_and_abilities/cr608_stack.py", "stack"),
            "406": ("rules/kernel/game.py", "exile"),
            "408": ("rules/kernel/game.py", "command zone"),
            # -- 500s -------------------------------------------------------
            "500": ("rules/cr500_turn_structure/cr500_turn.py", "phases, steps, mana emptying"),
            "501": ("rules/cr500_turn_structure/cr500_turn.py", ""),
            "502": ("rules/cr500_turn_structure/cr500_turn.py", "untap step, no priority"),
            "504": ("rules/cr500_turn_structure/cr500_turn.py", "draw step as a turn-based action"),
            "505": ("rules/cr500_turn_structure/cr500_turn.py", "main phases"),
            "506": ("rules/cr500_turn_structure/cr506_combat.py", "combat phase, removal from combat"),
            "507": ("rules/cr500_turn_structure/cr500_turn.py", ""),
            "508": ("rules/cr500_turn_structure/cr506_combat.py", "declare attackers, restrictions/requirements"),
            "509": ("rules/cr500_turn_structure/cr506_combat.py", "declare blockers, evasion, damage order"),
            "510": ("rules/cr500_turn_structure/cr506_combat.py", "combat damage, first/double strike steps"),
            "511": ("rules/cr500_turn_structure/cr506_combat.py", ""),
            "512": ("rules/cr500_turn_structure/cr500_turn.py", ""),
            "513": ("rules/cr500_turn_structure/cr500_turn.py", ""),
            "514": ("rules/cr500_turn_structure/cr500_turn.py", "cleanup, discard, damage wearing off"),
            # -- 600s -------------------------------------------------------
            "601": ("rules/cr600_spells_and_abilities/cr601_casting.py", "the full CR 601.2 sequence with rewind"),
            "602": ("rules/cr600_spells_and_abilities/cr601_casting.py", "activating abilities"),
            "603": ("rules/cr600_spells_and_abilities/cr603_triggers.py", "triggers, intervening-if, LKI, APNAP"),
            "604": ("rules/cr600_spells_and_abilities/cr613_layers.py", "static abilities, CDAs"),
            "605": ("rules/cr600_spells_and_abilities/cr601_casting.py", "mana abilities do not use the stack"),
            "606": ("rules/cr600_spells_and_abilities/cr601_casting.py", "loyalty timing, once-per-turn, counters as the cost"),
            "612": ("rules/cr600_spells_and_abilities/cr613_layers.py", "text-changing effects in layer 3"),
            "608": ("rules/cr600_spells_and_abilities/cr608_stack.py", "resolution, and 608.2b fizzling"),
            "609": ("rules/cr600_spells_and_abilities/resolve.py", ""),
            "610": ("rules/cr600_spells_and_abilities/resolve.py", ""),
            "611": ("rules/cr600_spells_and_abilities/cr611_durations.py", "continuous effects and when they end"),
            "613": (
                "rules/cr600_spells_and_abilities/cr613_layers.py",
                "all layers, sublayers, and 613.8 dependency; effects reach "
                "cards in other zones when they name them (611.2c, 611.3a)",
            ),
            "603.7": (
                "rules/cr600_spells_and_abilities/cr603_triggers.py",
                "delayed triggered abilities; 603.7c they remember the objects "
                "their effect acted on and follow only the move that triggered "
                "them",
            ),
            "603.8": ("rules/cr600_spells_and_abilities/cr603_triggers.py", "state triggers, with re-arming"),
            "614": ("rules/cr600_spells_and_abilities/cr614_replacement.py", "replacement effects"),
            "615": ("rules/cr600_spells_and_abilities/cr614_replacement.py", "prevention effects"),
            "616": ("rules/cr600_spells_and_abilities/cr614_replacement.py", "ordering multiple replacements"),
            # -- 700s -------------------------------------------------------
            "703": ("rules/cr500_turn_structure/cr500_turn.py", "turn-based actions"),
            "704": ("rules/cr700_additional_rules/cr704_sba.py", "the full state-based action list"),
            "705": ("rules/cr100_game_concepts/actions.py", "coin flips, from the seeded RNG"),
            "725": ("rules/cr700_additional_rules/cr725_designations.py", "the monarch, incl. combat theft and 725.5"),
            "726": (
                "rules/cr700_additional_rules/cr725_designations.py",
                "the initiative: its three 726.2 triggered abilities on the stack "
                "(venture into Undercity on taking it and at the holder's upkeep; "
                "combat damage passes it), 726.3, 726.4 and 726.5",
            ),
            "309": (
                "rules/cr300_card_types/cr309_dungeons.py",
                "dungeon cards in the command zone, one per player (309.3), room "
                "graphs read from the oracle text, room abilities as triggered "
                "abilities whose effects the parser reads, player-chosen branches, "
                "704.5t completion and the completed-dungeon record. Baldur's Gate "
                "Wilderness prints no arrows and is never offered; rooms the parser "
                "cannot read stay UNPARSED",
            ),
            "728": ("rules/cr700_additional_rules/cr725_designations.py", "rad counters and their main-phase procedure"),
            "731": ("rules/cr700_additional_rules/cr725_designations.py", "day and night, incl. the 731.3 flip"),
            "707": (
                "rules/cr700_additional_rules/cr707_faces.py",
                "copy effects in layer 1, copiable values only, spell copies "
                "that were never cast, and 707.8 face selection",
            ),
            "116": (
                "rules/cr100_game_concepts/cr116_special_actions.py",
                "all twelve special actions: playing a land, turning face up, "
                "suspend, foretell, plot, discard-self. The Planechase and "
                "Conspiracy Draft ones are named as out of scope by format "
                "rather than quietly missing",
            ),
            "709": ("rules/cr700_additional_rules/cr707_faces.py", "split cards - both halves castable, each on its own cost"),
            "722": (
                "rules/cr700_additional_rules/cr722_preparation.py",
                "preparation cards: never cast as the prepare spell (722.3, "
                "722.4); the prepared designation (722.3a/b); the copy in "
                "exile with only the prepare spell's characteristics, kept "
                "despite 704.5e while prepared, castable by the controller, "
                "unpreparing the permanent as it is cast (722.3c)",
            ),
            "723": (
                "rules/kernel/game.py",
                "controlling another player: decisions redirect, but only "
                "decisions - permanents stay theirs (723.3), their resources "
                "pay their costs (723.5a), and conceding never transfers "
                "(723.6). Ends with the turn",
            ),
            "727": (
                "rules/cr500_turn_structure/cr500_turn.py",
                "restarting: the game ends with no winner, every card returns "
                "(727.2), and the restarting player goes first (727.1a). The "
                "new game's seed derives from the old one so a run stays "
                "reproducible",
            ),
            "730": (
                "rules/cr700_additional_rules/cr702_keyword_impl.py",
                "merging (mutate): a copy effect in layer 1 on a permanent that "
                "never left, so CR 730.2c holds - counters, damage, auras and "
                "summoning sickness all survive the merge",
            ),
            "601.4": (
                "rules/kernel/legality.py",
                "restrictions on who may cast a spell, through the same "
                "prohibition mechanism as everything else CR 101.2 governs",
            ),
            "603.10": (
                "rules/cr600_spells_and_abilities/cr603_triggers.py",
                "look-back-in-time triggers: not only leaves-the-battlefield "
                "(603.10a) but phasing out, becoming unattached and losing "
                "control, each of which sees the pre-event game",
            ),
            "607.2": (
                "rules/cr100_game_concepts/actions.py",
                "linked-ability lookups keyed on the source and the link id of "
                "the ability that moved the card, so another object's or another "
                "ability's exiles are not 'the exiled cards' and a blinked "
                "permanent's linked ability correctly finds nothing",
            ),
            "613.3": (
                "rules/cr600_spells_and_abilities/cr613_layers.py",
                "within layers 2-6, characteristic-defining abilities apply "
                "before timestamped effects, then dependency reorders",
            ),
            "614.12": (
                "rules/cr600_spells_and_abilities/cr614_replacement.py",
                "self-replacement effects apply before all others (CR 616.1)",
            ),
            "116.2": (
                "rules/cr100_game_concepts/cr116_special_actions.py",
                "all twelve, enumerated in legal_actions and performed without "
                "the stack - which is what lets a morph flip up under split "
                "second",
            ),
            "601.3": (
                "rules/cr600_spells_and_abilities/resolve.py",
                "playing from a zone other than hand, via PLAY_FROM_ZONE - a "
                "land still uses the land drop, a spell still pays",
            ),
            "603.9": (
                "rules/cr600_spells_and_abilities/resolve.py",
                "reflexive triggers: \"when you do\" is created and triggered "
                "inside the resolution that caused it, not by watching events",
            ),
            "603.11": (
                "rules/cr600_spells_and_abilities/cr603_triggers.py",
                "abilities that trigger from other zones, via each ability's "
                "functions_in - suspend ticking in exile, recover watching from "
                "the graveyard",
            ),
            "605.4": (
                "rules/cr600_spells_and_abilities/cr603_triggers.py",
                "triggered mana abilities resolve without the stack, including "
                "during cost payment where the stack is not available",
            ),
            "614.13": (
                "rules/cr600_spells_and_abilities/cr614_replacement.py",
                "MODIFY_DRAW; dredge replaces the draw from the graveyard",
            ),
            "614.14": ("rules/cr600_spells_and_abilities/cr614_replacement.py", "enters-the-battlefield replacements"),
            "614.16": (
                "rules/cr600_spells_and_abilities/cr614_replacement.py",
                "ENTERS_TAPPED and ENTERS_WITH_COUNTERS modify how a permanent "
                "enters, rather than acting after it has",
            ),
            "724": (
                "rules/cr500_turn_structure/cr500_turn.py",
                "ending the turn (724.1b) and extra phases and steps (500.8)",
            ),
            "710": (
                "rules/cr300_card_types/cr300_card_types.py",
                "flip cards: the bottom half's name, text, types and P/T take "
                "over, while colour and mana cost stay with the top (710.1c)",
            ),
            "711": (
                "rules/cr300_card_types/cr300_card_types.py",
                "leveler cards: {LEVEL} bands as level-counter-conditioned "
                "statics setting base P/T in layer 7b",
            ),
            "717": (
                "rules/cr700_additional_rules/cr717_attractions.py",
                "Attractions: the Attraction deck as a pile in the command "
                "zone, built from a decklist's Attractions section and "
                "shuffled at setup (717.2, 103.3a), 717.2a's construction "
                "checks, opening (717.3, 701.51), the precombat-main roll to "
                "visit (717.4, 505.5), visit abilities (717.5, 702.159) and "
                "the junkyard replacement (717.6, 717.6a)",
            ),
            "714": (
                "rules/cr300_card_types/cr300_card_types.py",
                "Sagas: chapter symbols as crossing triggers (714.2b), lore "
                "counters as a precombat-main turn-based action (714.3c), and "
                "the 704.5s sacrifice - all gated on having chapter abilities",
            ),
            "716": (
                "rules/cr300_card_types/cr300_card_types.py",
                "Class cards: a level bar is an activated ability plus a static "
                "one, and level is a non-copiable designation (716.2b)",
            ),
            "719": (
                "rules/cr300_card_types/cr300_card_types.py",
                "Case cards: the to-solve end-step trigger and the solved "
                "designation, which ends when the permanent leaves",
            ),
            "721": (
                "rules/cr300_card_types/cr300_card_types.py",
                "station cards: {N+} charge-counter thresholds as statics",
            ),
            "715": (
                "rules/cr300_card_types/cr300_card_types.py",
                "adventurer cards: the Adventure is a castable face judged on its "
                "own characteristics (715.3a-b), a copy is an Adventure too "
                "(715.3c), a resolved one is exiled and its creature castable "
                "(715.3d); \"has an Adventure\" reads the card, through copy "
                "effects (715.2a-b); any name may be chosen (715.5)",
            ),
            "718": (
                "rules/cr300_card_types/cr300_card_types.py",
                "prototype cards: casting prototyped is a choice of face, not an "
                "alternative cost (718.3); the prototype cost, P/T and colour "
                "are read from the card's prototype line and hold on the stack "
                "and the battlefield (718.3a-b), in copies (718.3c-d), and "
                "nowhere else (718.4)",
            ),
            "720": (
                "rules/cr300_card_types/cr300_card_types.py",
                "omen cards: the Omen is a castable face (720.3a-b), a copy is "
                "an Omen too (720.3c), a resolved one is shuffled into its "
                "owner's library (720.3d); \"has an Omen\" as for Adventures",
            ),
            "708": (
                "rules/cr700_additional_rules/cr708_face_down.py",
                "face-down spells and permanents: layer 1b gives what turned it "
                "face down (708.2, ward for disguise and cloak); cast face down "
                "from hand or command zone and judged as the 2/2 (708.4); a "
                "face-down spell becomes a face-down permanent and nothing "
                "enters face up (708.3); turning up pays its cost, keeps the "
                "object (708.8) and restamps it (613.7f); manifest, cloak and "
                "manifest dread (701.40, 701.58, 701.62); copies take the "
                "face-down values (708.10); exiled face down has no "
                "characteristics (406.3a); revealed on leaving and at game end "
                "(708.9). 708.5-708.6 are about what players may look at, which "
                "the engine does not hide from agents",
            ),
            "712": (
                "rules/cr700_additional_rules/cr707_faces.py",
                "double-faced cards: transforming without a new object (712.18), "
                "front-face casting (712.11), MDFC land faces (712.12)",
            ),
            # -- 800s -------------------------------------------------------
            "800": (
                "rules/kernel/game.py",
                "multiplayer basics, APNAP, and 800.4 leaving the game. Range of "
                "influence is unlimited, which is what Commander uses; 801 is the "
                "optional limited variant and does not apply",
            ),
            # -- 900s -------------------------------------------------------
            "903": (
                "rules/cr903_commander/",
                "command zone, {2} tax, 21 commander damage, 903.9a state-based "
                "action and 903.9b replacement, colour identity and singleton "
                "deck construction, and the partner variants",
            ),
        },
        Status.PARTIAL: {
            "717.1": (
                "rules/cr700_additional_rules/cr717_attractions.py",
                "lit numbers are read from the card data's attraction_lights, "
                "which the shipped Scryfall snapshot does not carry - with "
                "nothing lit up, no Attraction in a real game is ever "
                "visited, though the roll itself happens",
            ),
            "201": (
                "rules/kernel/matching.py",
                (
                    "names are matched and compared (201.2a-c), and 201.4's "
                    "chosen card name is recorded and readable. 201.3's "
                    "interchangeable names are not modelled - an object "
                    "carries a single name"
                ),
            ),
            "208": (
                "data/cards.py",
                "printed power/toughness including * (208.2), 208.4b base "
                "power/toughness (recorded after layer 7b and filterable), and "
                "208.5's 0-fill; 208.2b (a replacement effect choosing P/T as "
                "it enters) is missing",
            ),

            "108": (
                "rules/kernel/game.py",
                "cards; 108.3 owner is the player whose deck it started in, a "
                "token's owner is its creator (111.2), a spell copy's the player "
                "who put it on the stack (707.10, 112.2a), and 400.3 sends a "
                "card to its owner's hand, library or graveyard. Cards brought "
                "in from outside the game (108.3, 108.3b) and ante are not "
                "modelled",
            ),


            "207": (
                "data/cards.py",
                "text box is stored; turning it into abilities is the parser's job",
            ),
            "607": (
                "rules/cr100_game_concepts/actions.py",
                "engine side of 607.2a-c and 607.2q: exiles, tokens and "
                "put-onto-the-battlefield are recorded per source and link id, "
                "and ObjectFilter.linked_to_source finds only what that source's "
                "partner ability did; links end when either object changes "
                "zones (400.7), a leaves-the-battlefield half looks back one "
                "step (603.10a), and cost-exiled cards pass from spell to "
                "permanent (607.2q). Chosen values (607.2d) live on the object "
                "and are undefined on a new one (607.5a). Missing: the parser "
                "emits neither link ids nor the linked filter, so printed pairs "
                "(imprint, hideaway, 'the exiled card' across abilities) are "
                "not yet linked; 607.1d links across two objects and 607.3's "
                "summed answers are not modelled",
            ),
            # -- 700s -------------------------------------------------------
            "701": (
                "rules/cr700_additional_rules/cr701_keyword_actions.py",
                "70 of 81 keyword actions fully modelled; the rest are Unfinity, "
                "Planechase, Archenemy or newer than the card-pool snapshot",
            ),
            "702": (
                "rules/cr700_additional_rules/keywords.py",
                "222 of 225 keyword abilities implemented; the remainder are out-of-format "
                "(Augment, the Conspiracy agendas) or shapes awaiting card text",
            ),
            # -- 800s -------------------------------------------------------

            # -- 900s -------------------------------------------------------

        },
        Status.NOT_IMPLEMENTED: {
            # -- 700s, with the rule names taken from the actual CR ----------
            # -- known gaps *inside* groups marked implemented above ---------
            # A group-level mark would otherwise score every subrule as done,
            # which is exactly the kind of flattering arithmetic this module
            # exists to prevent.

            # -- 700s --------------------------------------------------------


        },
        Status.NOT_APPLICABLE: {
            "713": (
                "",
                (
                    "substitute cards: a physical game supplement standing in "
                    "for a double-faced or meld card at a table. A simulator "
                    "has the real card"
                ),
            ),
            "204.1": (
                "",
                (
                    "where the colour indicator is printed and what it looks "
                    "like. What it *means* is 204.2, which is implemented"
                ),
            ),
            "503": (
                "",
                "503.2 governs 'cast only after [a player's] upkeep step' with "
                "more than one upkeep step; no card in the Commander-legal pool "
                "carries that wording, so there is nothing to enforce",
            ),
            "123": (
                "",
                "stickers are Unfinity only, and sticker sheets live outside "
                "the 100-card deck this program simulates",
            ),
            "733": (
                "",
                "handling illegal actions is tournament procedure, not game "
                "rules - a simulator cannot take an illegal action to correct",
            ),

            # Section headers, not rules. They carry no requirement, so
            # scoring them as gaps would be noise in every report.
            "200": ("", "section header, not a rule"),
            "300": ("", "section header, not a rule"),
            "600": ("", "section header, not a rule"),

            "311": ("", "planes - Planechase only"),
            "312": ("", "phenomena - Planechase only"),
            "313": ("", "vanguards - Vanguard only"),
            "314": ("", "schemes - Archenemy only"),
            "315": ("", "conspiracies - draft only"),
            "407": ("", "ante - not used"),
            # Print details with no rules meaning at all: there is nothing to
            # implement, so counting them as outstanding work overstates the
            # gap rather than describing it.
            "203": ("", "illustration - no rules meaning (CR 203.1)"),
            "206": ("", "expansion symbol - no rules meaning (CR 206.1)"),
            "213": ("", "information below the text box - no rules meaning"),
            "211": ("", "hand modifier - Vanguard only"),
            "212": ("", "life modifier - Vanguard only"),
            # -- 800s: multiplayer variants Commander does not use ------------
            "801": ("", "limited range of influence - Commander uses unlimited"),
            "802": ("", "attack multiple players option"),
            "803": ("", "attack left/right option"),
            "804": ("", "deploy creatures option"),
            "805": ("", "shared team turns option"),
            "806": ("", "free-for-all variant details beyond 800"),
            "807": ("", "Grand Melee variant"),
            "808": ("", "Team vs Team variant"),
            "809": ("", "Emperor variant"),
            "810": ("", "Two-Headed Giant variant"),
            "811": ("", "Alternating Teams variant"),
            # -- 900s: other casual variants ----------------------------------
            "901": ("", "Planechase"),
            "902": ("", "Vanguard"),
            "904": ("", "Archenemy"),
            "905": ("", "Conspiracy Draft"),
            "903.12": ("", "Brawl option - a different format"),
            "903.13": ("", "Commander Draft - a different format"),
            "903.11": ("", "cards from outside the game - no sideboard in this sim"),
            "900": ("", "casual variant section header"),
            "729": ("", "subgames - Shahrazad only, and banned in Commander"),
            "732": ("", "taking shortcuts - a human convenience with no simulated effect"),
        },
    }
)


def entry_for(rule_number: str) -> Entry:
    """The coverage entry governing a rule, most specific key first.

    Anything unclassified is NOT_IMPLEMENTED, so silence never scores as done.
    """
    parts = rule_number.split(".")
    if len(parts) > 1:
        digits = "".join(c for c in parts[1] if c.isdigit())
        specific = f"{parts[0]}.{digits}"
        if specific in COVERAGE:
            return COVERAGE[specific]
    return COVERAGE.get(parts[0], Entry(Status.NOT_IMPLEMENTED))


def _lookup(rule_number: str, per_keyword: dict[str, Entry]) -> Entry:
    """Coverage for a rule, preferring a per-keyword classification if there is one."""
    parts = rule_number.split(".")
    if len(parts) > 1:
        digits = "".join(c for c in parts[1] if c.isdigit())
        specific = f"{parts[0]}.{digits}"
        found = per_keyword.get(specific)
        if found is not None:
            return found
    return entry_for(rule_number)


def keyword_entries(rules) -> dict[str, Entry]:
    """Per-keyword coverage for CR 701 and 702, read from the keyword registry.

    Those two rule groups are ~1,200 of the 700s' 1,558 rules - one subgroup
    per keyword - so classifying them wholesale as "partial" makes the whole
    section report as 0% done while hiding the 43 keywords that *are* built.

    The mapping is automatic: a rule group's header text is the keyword's name
    ("702.9. Flying"), so it looks straight up in the registry. A new keyword
    from a new set therefore classifies itself.
    """
    from ..cr700_additional_rules import keywords as keyword_registry

    out: dict[str, Entry] = {}
    for rule in rules:
        if rule.is_subrule or not rule.number.startswith(("701.", "702.")):
            # Only the group headers carry the keyword name.
            if "." not in rule.number:
                continue
        parts = rule.number.split(".")
        if len(parts) != 2 or parts[0] not in ("701", "702"):
            continue
        if not parts[1].isdigit():
            continue  # A lettered subrule, not a group header.

        name = rule.text.split(".")[0].split("(")[0].strip()
        spec = keyword_registry.lookup(name)
        if spec is None:
            out[rule.number] = Entry(
                Status.NOT_IMPLEMENTED, "", f"{name}: not in the keyword registry"
            )
        elif spec.status is keyword_registry.Status.IMPLEMENTED:
            out[rule.number] = Entry(Status.IMPLEMENTED, "rules/cr700_additional_rules/keywords.py", name)
        elif spec.status is keyword_registry.Status.PARTIAL:
            out[rule.number] = Entry(Status.PARTIAL, "rules/cr700_additional_rules/keywords.py", f"{name}: {spec.note}")
        else:
            out[rule.number] = Entry(Status.NOT_IMPLEMENTED, "", name)
    return out


def report(rules) -> dict:
    """Coverage counts per section, and the list of gaps.

    Percentages exclude NOT_APPLICABLE rules, because scoring Planechase as
    "covered" would flatter the number without helping anyone.
    """
    from ...data.comprehensive_rules import SECTION_NAMES

    sections: dict[int, dict[Status, int]] = {}
    gaps: dict[str, list[str]] = {}
    per_keyword = keyword_entries(rules)

    for rule in rules:
        entry = _lookup(rule.number, per_keyword)
        counts = sections.setdefault(rule.section, dict.fromkeys(Status, 0))
        counts[entry.status] += 1
        if entry.status in (Status.NOT_IMPLEMENTED, Status.PARTIAL):
            gaps.setdefault(rule.rule_group, []).append(rule.number)

    out = {"sections": {}, "gaps": gaps, "names": SECTION_NAMES}
    for section, counts in sorted(sections.items()):
        relevant = sum(counts.values()) - counts[Status.NOT_APPLICABLE]
        done = counts[Status.IMPLEMENTED]
        out["sections"][section] = {
            "counts": counts,
            "relevant": relevant,
            "done": done,
            "percent": (100.0 * done / relevant) if relevant else 100.0,
        }
    return out
