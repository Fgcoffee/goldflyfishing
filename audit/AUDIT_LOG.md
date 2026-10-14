# Audit log

The auditor's record of rule conformance for the parser and rules sessions.
Standard: the Comprehensive Rules (`MagicCompRules 20260925.pdf`) and
`docs/parser-working-rules.md`. When it is unclear whether something is right,
it is blocked.

Status: **BLOCK** (must not merge), **WARN** (fix soon, does not block),
**OK** (verified), **LOGGED** (known issue the owner reported).

## Branch status

| Branch | Owner | Verdict | Notes |
|---|---|---|---|
| `claude/lucid-einstein-dml803` @ 043206d | parser | BLOCK (process, F9 red test) | 105 commits not in main, and PR #15 is already MERGED, so this work needs a new PR. Census vs main: +983 / −379 / 8953 abilities with changed IR text. The LOST and CHANGED samples are corrections (see below). Test suite rerunning. |
| `wip-misbuilt-keywords` (local, unpushed) | parser | not received | |
| `main-n8yjai` @ a0c91e7 | rules | OK for this commit; BLOCK (process) | Flashback exile verified on the board. PR #10 is already MERGED, so this needs a new PR. |

## Verified on the sandbox (parser branch @ 043206d)

| Interaction | Expected (CR) | Result |
|---|---|---|
| Ghostly Prison, 2 attackers, 3 lands | one pays {2}, other doesn't attack, stays untapped (508.1h-j) | OK |
| 2x Propaganda | {4} per attacker; 3 lands is not enough | OK |
| Propaganda, the attacking Llanowar Elves can't pay for itself | tapped before cost (508.1f) | OK |
| Archangel of Tithes untapped vs tapped | tax only while untapped | OK |
| Silence | only opponents can't cast (101.2) | OK |
| Ward {4} (Kappa Cannoneer) vs Bolt, no mana | countered (702.21a) | OK |
| Diabolic Edict on opponent | only target player sacrifices | OK |
| Prey Upon 2/2 vs 3/3 | both deal damage, bears die (701.14) | OK |
| Rabid Bite | one-way damage (701.14) | OK |
| Maro | counts only your hand | OK |
| The Eldest Reborn placed | lore 1, chapter I triggers (714.3a) | OK |
| Ugin -X | left unread (honest), "that's one or more colors" | OK (honest gap) |

## Findings

| # | Sev | Owner | Finding | Evidence |
|---|---|---|---|---|
| F4 | **BLOCK** | parser (keyword builder) | Crew N taps N creatures instead of any number with total power N or more (CR 702.122a). `_crew` in `cr702_keyword_impl.py` builds `TAP_OTHER amount=N` but its text says "total power". Wrong reading counted as understood. Pre-existing on main. | Cultivator's Caravan (Crew 3): 3x Llanowar Elves can crew; one Hill Giant can't; Bears + Elves can't |
| F5 | **BLOCK** | parser (builder) + rules (legality) | Jump-start is cast from the graveyard for FREE with no discard (702.133a: pay the mana cost and discard a card as an additional cost). It's built as a graveyard *alternative* cost with no cost. On main it then goes back to the graveyard: a free spell, recast forever. On a0c91e7 it's exiled, but still free. | Radical Idea: offered with no mana and an empty hand; pool unchanged after the cast |
| F6 | **BLOCK** | parser (builder) + rules (legality) | Aftermath is inverted (702.127a): the aftermath half (Dawn) is cast FROM HAND for its mana cost, and can't be cast from the graveyard even with mana. | Dusk // Dawn: face_index 1 offered from hand and resolved; nothing offered from the graveyard with 60 mana |
| F7 | WARN | rules | `_EXILED_WHEN_IT_LEAVES_THE_STACK` keys aftermath on an alternative cost, but 702.127a exiles it whenever it "was cast from a graveyard", whatever cost was paid. Harmonize's entry is dead, because Harmonize is built as a +1/+1-counter ability (class A6). | a0c91e7 game.py |
| F8 | **BLOCK** | parser (builder) / rules (LKI) | Modular moves NO counters. The dies trigger targets an artifact creature and resolves, but the target gets 0 counters: the count is read from the card in the graveyard, not from last-known information (CR 702.43a, 608.2h). Main and branch. This contradicts the parser's 13:15 correction. | Arcbound Worker (1 counter) Shocked, trigger targets Ornithopter, Ornithopter `{}` |
| F9 | **BLOCK** | parser | The branch's own suite is red: `tests/rules/test_citations.py::test_the_baseline_covers_every_cited_rule`, "702.34a" cited (tools/engine_gaps.py) but absent from the baseline. 1 failed, 2457 passed. | `pytest tests/parser tests/rules` @ 043206d |
| F1 | WARN | sandbox | `Sandbox.put` places planeswalkers with no loyalty counters (CR 306.5b); casting gives 7. Tests built with `put` see a 0-loyalty walker. | Ugin via put: counters `{}` |
| F2 | WARN | rules | Enlist (an optional cost to attack, 702.154a) is paid after the attack tax rather than being part of one locked-in total cost (508.1g-h). | `cr506_combat.declare_attackers` |
| F3 | WARN | parser | "cards in that player's hand" (Sudden Impact) counts every hand, the same defect as "their hand". The fix must cover "that player's" and "its owner's" too. Pre-existing on main. | Sudden Impact: "the number of all object in hand" |

## Known issues logged (reported by the parser session, 2026-10-13)

Class A (read as understood but wrong, the worst class). All of these violate
working rule 1 and must be placeholders (unread) until they are built
faithfully:

Parser correction (13:15), board-checked: **Vanishing OK** (Keldon Marauders sacrificed as its last counter comes off, 702.63a). **Unleash OK** (a Cackler with a counter can't block; Hill Giant control blocks). **Fading off by one, confirmed**: Blastoderm sacrificed on the upkeep that removes its last counter (turn 6), should be the following one (turn 8, 702.32a). **Modular NOT correct**: see F8.

1. Annihilator sacrifices every permanent (parser). **Reproduced**: Ulamog's Crusher, 6 Forests all gone (main too).
2. Flashback card goes back to the graveyard, not exile, 702.34a (rules). **Reproduced**: Think Twice back in graveyard (main too).
3. Exploit sacrifices all your creatures (parser).
4. Champion exiles all your creatures (parser).
5. Living weapon and For Mirrodin! make 0 tokens (parser).
6. Built as +1/+1 counters: Saddle, Craft, Harmonize, Job select, Specialize, Boast.
7. Flat counter counts: Amplify, Devour, Backup, Fabricate, Graft, Modular, Sunburst.
8. Missing halves: Fading, Vanishing, Haunt, Recover, Hideaway, Compleated.
9. Wrong copies: Storm, Gravestorm (once), Demonstrate (wrong player), Ripple (whole library).
10. Dropped conditions: Dethrone, Evolve, Training, Unleash.
11. Soulbond pairs with every creature.

Class B (paid but never read afterwards, suspected): Buyback, Dash, Blitz,
Bestow, Warp, Entwine, Splice, Aftermath, Encore, Miracle, Retrace, Embalm,
Eternalize, Emerge, Escalate, Replicate, Squad, Offspring, Spectacle, Prowl,
Jump-start, Gift, Bargain, Spree, Multikicker, Cleave, Assist, Conspire,
Freerunning, Impending, More Than Meets the Eye.

Class C:
- keywords.py Status field stale (DECLARED for working Scry/Equip/Ward).
- "cards in their hand"/"its" counts every hand (see F3: also "that player's").
- Bot always pays attack taxes (strategy, not rules).
- Unread so far: War Tax-style one-turn taxes, Station N+, "up to two target artifacts".

## Census diff: parser branch @ 043206d vs main @ ab752fa

- Fully read: 17578 of 32402 (54.25%). Gained 983, lost 379.
- **LOST 379**: every cluster sampled was a wrong reading on main that is now refused, as rule 1 requires. Main read "gains protection from the color of your choice" as bare Protection, "discard X" as discard 1, "incubate X" as 0 tokens, Double team as a +1/+1 counter, "one that hasn't been chosen" as a plain choose one, "two or more creatures attack" as exactly 2, and "its controller"/"that player" with no antecedent. Accepted as corrections. The PR must still list them.
- **CHANGED**: every ability hash changed (the IR dataclass changed shape), so I diffed the rendered IR text instead: 8953 abilities. The top clusters are fuller renderings (zones, "(once)", "you own", payment counts) or explained corrections ("another X" trigger is one, not all; scry → look at the top; "you" → "the player targeted earlier"). One cluster exposed F4 (Crew): the explainer now honestly renders the engine's N-creatures reading.

## Rules session: fixes verified (main-n8yjai @ a0c91e7)

- Flashback (702.34a), Think Twice: from the graveyard it is exiled on resolution; countered by Counterspell, it is still exiled; cast from hand, it goes to the graveyard (control). Force of Will's pitch cost from hand still goes to the graveyard (other alternative costs are unaffected). **OK.**
- Library redirect (`or` on Zone.LIBRARY = 0): the code is correct. No parsed card in the pool has a "would be put into a graveyard ... library instead" replacement (Legacy Weapon's is honestly unread), so only the unit test covers it. **OK.**

## Known issues reported by the rules session (2026-10-13)

A. No engine state: Saddle, Soulbond, Haunt, Sunburst, Amplify, Compleated, Craft, Specialize, Harmonize's power reduction, Demonstrate, Dethrone, Myriad, Double team, Teamwork, piles (~45 cards); reveal/exile can't name a player; attack costs are mana only.
B. ~30 cost keywords paid but maybe never read (same list as the parser's B). Note F5/F6: jump-start and aftermath are worse than "never read".
C. CR 208.2b P/T choices are continuous effects, not copiable values.
D. Bots see hidden information (708.5, hands, libraries).
E. Attractions never visited (no attraction_lights data); prize and Visit modes not done.
F. Companion conditions not checked.
G. 201.3 interchangeable names: no data.
H. Dungeons: Baldur's Gate Wilderness never offered; Hama Pashar not done.
I. Linked abilities (607): no link ids from the parser; 607.1d not modelled.
J. "Same name as" filters missing (Guardian Project, Maelstrom Pulse).
K. The bots' mana-plan lookahead hasn't been audited.
L. keywords.py Status field stale.
