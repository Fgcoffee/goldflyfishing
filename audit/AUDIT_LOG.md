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
| `claude/lucid-einstein-dml803` @ 043206d | parser | pending | 105 commits not in main. PR #15 is already MERGED, so this work needs a new PR. Tests and census diff running. |
| `wip-misbuilt-keywords` (local, unpushed) | parser | not received | |
| rules session branch | rules | not received | |

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
| F1 | WARN | sandbox | `Sandbox.put` places planeswalkers with no loyalty counters (CR 306.5b); casting gives 7. Tests built with `put` see a 0-loyalty walker. | Ugin via put: counters `{}` |
| F2 | WARN | rules | Enlist (an optional cost to attack, 702.154a) is paid after the attack tax rather than being part of one locked-in total cost (508.1g-h). | `cr506_combat.declare_attackers` |
| F3 | WARN | parser | "cards in that player's hand" (Sudden Impact) counts every hand, the same defect as "their hand". The fix must cover "that player's" and "its owner's" too. Pre-existing on main. | Sudden Impact: "the number of all object in hand" |

## Known issues logged (reported by the parser session, 2026-10-13)

Class A (read as understood but wrong, the worst class). All of these violate
working rule 1 and must be placeholders (unread) until they are built
faithfully:

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
