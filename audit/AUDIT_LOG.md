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
| `claude/lucid-einstein-dml803` @ 745e50e (PR #17) | parser | BLOCK: F6b, F10 (merge order), F15 | Tests GREEN: 2488 passed (parser+rules+tools). Census verified: 426 lost, all carrying a listed keyword; 0 gained, 0 changed. F9 fixed. Earlier @ 043206d: | 105 commits not in main, and PR #15 is already MERGED, so this work needs a new PR. Census vs main: +983 / −379 / 8953 abilities with changed IR text. The LOST and CHANGED samples are corrections (see below). Test suite rerunning. |
| `wip-misbuilt-keywords` (local, unpushed) | parser | not received | |
| `main-n8yjai` @ 599bdb1 (PR #18) | rules | BLOCK: F5 still free on this PR alone, F11 Vehicle crews itself (before wiring), F10 (merge order) | Aftermath, flashback, dash, blitz, buyback (when chosen) and sandbox loyalty verified. Jump-start and soulbond are primitives only. |

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
| F6b | **BLOCK** | rules (legality) → resolved on PR #18 | With Aftermath inert on PR #17, Dawn is still castable FROM HAND: the castability comes from split-card legality, not the keyword. PR #18 fixes it (only face 0 from hand; face 1 from the graveyard for {3}{W}{W}, then exiled). PR #17 must not merge without PR #18's fix. | Dusk // Dawn on 745e50e: CAST_SPELL faces 0 and 1 from hand |
| F10 | **BLOCK** | both | Merge order and integration. (a) PR #17 and PR #18 CONFLICT in 5 files (cr_baseline.json, resolve.py, conditions.py, gameobject.py, query.py). (b) PR #17 makes Crew, Jump-start and Aftermath inert while PR #18 fixes Aftermath's legality and adds the Crew and jump-start primitives, so whoever merges second must decide each table entry and rerun the board checks. (c) Flashback (117 cards) stays a wrong reading counted as read on PR #17 until PR #18's exile lands. (d) Dies triggers ("When this creature dies") don't fire on main or PR #18 (Requisitioner makes no Treasure); PR #17 fixes this. | combo worktree; t_req.py |
| F11 | **BLOCK** (before Crew is wired) | rules + parser | PR #18's total-power TAP_OTHER lets a Vehicle TAP ITSELF to pay its own Crew: after Cultivator's Caravan is crewed (a 5/5 creature), Crew is offered again and taps the Caravan. CR 702.122a and 702.171a say "OTHER untapped creatures". The candidates must exclude the source (or the builder must use an "another creature" filter). Otherwise correct, wired in a scratch merge: Hill Giant alone OK, Bears + Elves OK, two Elves refused, three 0-power refused, a summoning-sick creature may crew (302.6), the chooser taps the fewest. | t_crew2.py on cherry-pick e85e13d + total_power=True |
| F12 | WARN | rules | Buyback works when chosen (Capsize: {1}{U}{U}+{3} paid, returns to hand), but `legal_actions` never offers the buyback variant, so bots never pay it. | t_bb.py |
| F13 | WARN | rules | `find_payment(life_available=player.life - 1)` won't pay Phyrexian life down to 0; CR 119.4 allows paying life equal to your total. Pre-existing. | cr601_casting._pay |
| F14 | WARN | parser | The inert placeholders also drop the correct half: Modular creatures (19 Arcbound) now die on entry as 0/0; Fading creatures enter with no counters and never fade (Blastoderm stays a permanent 5/5 shroud). This is the honest-unread outcome rule 1 accepts, but it distorts the simulation, so prioritise the faithful builders. | t_inert.py |
| F15 | **BLOCK** | rules (engine) + parser (refuse until fixed) | Granted triggered abilities never trigger (the rules session confirms: "granted triggered abilities don't trigger"). 116 cards counted as FULLY READ grant one ('gains "When this creature dies..."', 'Equipped creature has "Whenever..."', Aura Flux, Agent of the Iron Throne...). | Abnormal Endurance on Grizzly Bears: the ability is listed on the Bears, Shock kills them, nothing returns (parser branch @ 745e50e) |
| F16 | WARN→verify | rules | Rules-session gaps that may be wrong readings counted as read: replicate copies can't choose new targets (702.56a lets them); warp's builder allows casting from the command zone (702.185a says "from your hand", so per 903.8 it's not castable from the command zone); blitz's draw is a delayed trigger, not a granted ability (DIVERGENCE: under ability loss, such as Humility, it would still draw). | rules session report 12:36 |
| F17 | WARN | parser | (Reported by the parser session, pre-existing.) Tannuk, Steadfast Second: "Artifact cards and red creature cards in your hand have warp" reads as "all red artifact or creature": the adjective is distributed over the whole list. Any "X cards and <adj> Y cards" may share the bug. | parser report 10-17 |
| F19 | **BLOCK** (before F15 is lifted) | rules | Granted LEAVES-THE-BATTLEFIELD keyword triggers that return the card do nothing, even with 5d767e8. The trigger goes on the stack and resolves (ABILITY_RESOLVED) but the card stays in the graveyard. Printed undying works (Young Wolf returns with +1/+1). Not yet tested: afterlife and modular granted. | Mikaeus, the Unhallowed + Grizzly Bears, Bolted: not back. Rattleblaze Scarecrow (persist while you control a black creature) + Vampire Nighthawk, Bolted: not back. Rules head 5d767e8 and scratch merge. |
| F20 | WARN | rules | 89c8f14: `copy_spell(..., may_choose_new_targets=True)` is the default and no caller passes False, so EVERY copy may retarget. CR 707.10c lets a copy choose new targets only when "some effects ... state that its controller may" do so; 707.10d-e copies have their targets fixed (Precursor Golem, Mirrorwing Dragon, Ivy). No read card plays wrong today (the 40 read copy effects without "new targets" are untargeted or copy-watchers), but the default is backwards. Make it False and pass True from storm, replicate, Twincast-style text. | cr707_faces.copy_spell |
| F21 | **BLOCK** | parser (builders) | Cost keywords whose builders the rules session reports as wrong (strict xfails in tests/rules/test_cost_keywords_board_b.py) are still counted as FULLY READ: ~91 cards (bloodthirst 18, offspring 17, squad 12, conspire 12, assist 11, freerunning 7, spectacle 6, impending 4, prowl 3, gift 1). On the board on rules 1540150, parser bdcf1fd and their merge: Stormblood Berserker gets no counters after the opponent is Shocked (1/1, should be 3/3); Overlord of the Mistmoors cast for impending gets NO time counters and IS a creature; Agate Instigator (offspring) and Powder Ganger (squad) are offered the extra cost, take the mana, and make NO token; Wort's granted conspire is never offered. Put them in MISBUILT_KEYWORDS (and stop offering the costs) until faithful. | t_batch2b.py, t_sq.py |
| F1 | WARN | sandbox → fixed on PR #18 | `Sandbox.put` places planeswalkers with no loyalty counters (CR 306.5b); casting gives 7. Tests built with `put` see a 0-loyalty walker. | Ugin via put: counters `{}` |
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

## Round 3 (2026-10-14)

Parser PR #17 @ 745e50e: 65b723b + 745e50e census vs 043206d gives 0 gained, 426 lost, 0 changed; every lost card's text contains a MISBUILT_KEYWORDS keyword. On the board: Annihilator inert (6 lands stay), Crew not offered (the Caravan's mana ability still is), Radical Idea not castable from the graveyard, Batterskull makes no Germ. Dawn is still castable from hand (F6b).

Rules PR #18 @ 599bdb1: aftermath OK (from the graveyard only, exiled); dash OK (haste, returns at end step); blitz OK (haste, sacrificed, draws); buyback OK when chosen (F12); sandbox loyalty OK. Jump-start is still free on PR #18 alone, because its builder doesn't use `pays_mana_cost` yet (parser wiring). Soulbond is a primitive only (the builder still reads "attach to all"; the paired static is unread). 461017e (sunburst colours, compleated, dethrone): code-reviewed OK, F13.

Rules session PR #18 overview (12:36): builder-side items it lists as parser-owned are Crew/Saddle total_power, Sunburst, Compleated, Dethrone, Soulbond, Jump-start/Retrace pays_mana_cost, the Aftermath marker, the Emerge sacrifice, and Miracle/Splice/Bestow/Embalm/Eternalize/Encore misbuilt (strict xfails). Gaps logged: bestow as an Aura spell; token copies of a card (TokenSpec.copy_of never read); splice naming; replicate new targets; warped designation; warp from the command zone; enlist not wired (so F2 is moot for now); queue: haunt, amplify, craft, specialize, demonstrate, myriad, double team, teamwork, piles, REVEAL/EXILE player, Status field.

## Round 4 (2026-10-17)

**F10 resolved.** The rules session merged PR #17 into main-n8yjai (d5a4a3b); the parser fast-forwarded to it. **F6b resolved** on the combined head (Dawn is only castable from the graveyard).

Parser PR #17 @ 127dad9 (combined head + 486f3b7 F15 refusals + 127dad9 replicate/warp):
- Every earlier board check rerun: taxes, Silence, ward, edict, fights, Maro, flashback exile (countered too), aftermath (from the graveyard only, exiled), dash, blitz (now with the Treasure: dies triggers fire), buyback when chosen, the inert keywords (Annihilator, Crew, jump-start, Living weapon, Modular, Fading). All as expected.
- Census vs 745e50e: 0 gained, 240 lost, 0 rendered-IR changes. Lost reasons are exactly "grants a triggered ability the engine never collects" (231 abilities) and "replicate copies of a targeted spell cannot choose new targets" (11). Warp's from_zone change doesn't render; the parser unit test covers it, and the sandbox can't designate a commander to show it on the board.
- On this head alone, ability loss isn't respected for leaves-the-battlefield triggers: a Requisitioner hit by Turn to Frog (no abilities) still makes its Treasure on death. Fixed by rules 5d767e8 (below).

Rules main-n8yjai @ 5d767e8 (be1ec5e + 5d767e8, NOT yet in the parser head):
- be1ec5e: **F11 fixed** (with Crew wired to total_power in a scratch copy: a crewed Caravan is no longer offered Crew to tap itself; Hill Giant alone, the fewest tapped, summoning-sick all still OK). **F13 fixed** (Gitaxian Probe castable at 2 life with no mana). **F12 fixed** (Capsize buyback and Burst Lightning kicker each offered both ways).
- 5d767e8: **granted triggers fire**. Abnormal Endurance: the Bears die and return tapped. Sublime Archangel + 2 Bears, one attacking alone: 5/5 (three exalted). **Ability loss respected**: Turn to Frog'd Requisitioner makes no Treasure (603.10a). Humility is honestly unread, so it can't be used for this check.
- The rules head and parser head merge **cleanly** (scratch merge fe1419f).

**New divergence (F18, process):** parser 486f3b7 refuses granted triggers (229 cards) because the engine never collected them, and rules 5d767e8 now collects them. Neither head has the other. Required: merge main-n8yjai into the parser branch, then lift the F15 refusal in its own commit with a census diff. I'll verify granted quoted triggers and the granted keywords (exalted, ward, cascade, flanking, persist, prowess, melee, undying, battle cry, afflict, afterlife) on the board before it counts as read again.

Pre-check for lifting F15 (scratch merge fe1419f with the refusal switched off): granted quoted dies trigger OK (Abnormal Endurance); granted exalted OK (Sublime Archangel 5/5); granted ward OK (Storvald: Bolt countered); granted prowess OK (Bria: Bears 3/3 after Opt); ability loss removes a granted trigger OK (Abnormal Endurance then Turn to Frog: no return). **Granted persist and undying FAIL (F19).** Maelstrom Nexus (cascade) and Cauldron of Souls (persist) are honestly unread, so not testable.

## Round 5 (2026-10-17, later)

Rules main-n8yjai @ 91196be: 89c8f14 (copies choose new targets via choose_new_targets; F20 WARN on the default) and 91196be (blitz draw as a granted ability; their Humility test is scripted because real Humility is unread). **F19 still reproduces on 91196be** (Mikaeus-granted undying: not back). F5 note: 5bf6bdb's title overstates it, since it adds only the primitive (the rules session acknowledges this; the pushed message stays as it is).

Parser @ c6f58cc (Faithful Modular and Fading, on top of 127dad9; does NOT contain main-n8yjai 91196be): **Modular OK** (Arcbound Worker with 1 counter: Ornithopter gets 1; with 3 counters: gets 3). **Fading OK** (Blastoderm: counters off on turns 2, 4 and 6, sacrificed on turn 8, per 702.32a). Vanishing unchanged (OK). Parser head 127dad9 suite: 2556 passed, 7 xfailed (my run; the worktree moved to c6f58cc near its end, and the parser's own run on 127dad9 was 2552 passed).

Rules session's open list (logged): enlist (F2) and non-mana attack costs; haunt, amplify, craft, specialize, demonstrate, piles, REVEAL/EXILE player; bestow as an Aura; token copies of cards; back-face mana value (712.8c/e: back faces get 0); the second cost-keyword batch (squad, offspring, multikicker, gift, bargain, spree, conspire, impending, prowl, freerunning, MTMtE) is being redone.

## Round 6 (2026-10-17, evening)

Heads diverged again: rules main-n8yjai @ 1540150 contains parser 127dad9 but not c6f58cc or bdcf1fd; parser @ bdcf1fd contains neither be1ec5e nor anything after 599bdb1 on the rules side. Scratch merge 0f622d7 (bdcf1fd + 1540150) is CLEAN. The earlier scratch merge suite (127dad9 + 5d767e8) had 1 failure, test_cr603_granted_triggers::test_a_granted_dies_trigger_returns_the_creature, which was expected (the parser's refusal makes Abnormal Endurance unread); the rules session has since made it a strict xfail.

Parser bdcf1fd (faithful Crew and Saddle): **Crew OK** on bdcf1fd alone and on the merge (Hill Giant alone, Bears + Elves, two Elves refused, three 0-power refused, a summoning-sick creature may crew, the fewest tapped, never itself). Explainer cosmetic: the type change renders as "is also 4". **Saddle OK** (Quilled Charger, Saddle 2: one Elf refused; Hill Giant pays at sorcery speed only, saddled_turn set, never itself). The Charger's "attacks while saddled" trigger is honestly unread.

Rules 1540150: **F19 still reproduces** (Mikaeus-granted undying: not back). Granted exalted: now refused by the merged parser 486f3b7 (Sublime Archangel's grant line is unread), so a lone attacker is 3/3, which is consistent. Batch-2 cost keywords: see F21. Gaps logged: assist (another player paying), cleave (bracket removal), back-face mana value (Goldbug cast converted has MV 0, should be 3; 712.8c/e).

Scratch merge 0f622d7 (parser bdcf1fd + rules 1540150), full suite `pytest tests`: **2893 passed, 21 xfailed, 0 failed.** The two heads are compatible; what remains is F18 (one head), F19 and F21.
