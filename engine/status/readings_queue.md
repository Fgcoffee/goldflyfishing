# Readings queue: cards read wrong

Input for the parser agent. Each entry is a card the parser marks **fully read**
whose reading doesn't match its Oracle text. Coverage numbers can't see these.

Found by checking the 300 most-played cards (EDHREC rank) by hand: each
ability's Oracle line against `explain_card`, which renders from the opcodes,
and the IR where the English is ambiguous. The cards that read right are pinned
in `tests/ir/golden/readings.json` (`tests/ir/test_golden_readings.py`).

Format: **card** (play-rate rank). Oracle line, then what the IR says and what
it should say. Grouped by cause, most-played first within each group. Tick an
entry with your commit hash when it's fixed. I re-check it and move the card
into the golden set.

Not misreads, just how the read-back words things. No action needed:
- A nested player prints as "you": "its controller may *you* searches" (Path
  to Exile, Assassin's Trophy, Smothering Tithe). The engine has the right
  player choose and act (`_another_player_may`).
- "enters tapped" prints as "Continuously: tap object". Read correctly as a
  replacement.

## Spot check: parser batch b2ce87b ("as long as this is equipped / enchanted")

Ran `python -m tests.ir.readings_spot_check b2ce87b~1`. 17 cards became fully
read, and all 17 were checked. The condition reads right; the subject doesn't.

- [x] **"It" in a static ability is `remembered`, so the bonus goes to every
  permanent.** (0100e79, parser: "it" binds to the condition's object)
  - In "As long as this creature is equipped, it has double strike", "it"
    becomes `targets: {remembered: true}`.
  - A static ability has no resolution and so no remembered object.
    `matches` ignores the flag, so the filter has no constraint at all.
  - **Confirmed on the TS engine:** with Kor Duelist equipped (Sword of Fire
    and Ice), a Grizzly Bears elsewhere on the board also has double strike.
    (Scratch vitest on `RealBoard`, not committed.) The parser's board test
    only checked the Duelist itself.
  - "It" here should be `source_only` (the creature itself).
  - Affected (12): Kor Duelist, Armed Assailant, Kitesail Apprentice,
    Fireblade Charger, Sunspear Shikari, Leonin Lightbringer, Dwarfhold
    Champion, Armory Veteran, Leonin Den-Guard, Thran Golem, Skyhunter Cub,
    Auriok Glaivemaster.
  - The other five in the batch read right: Patriot, Young Avenger; Raksha
    Golden Cub; Gate Hound; Dreampod Druid; Fireblade Charger's death
    trigger.
  - Worth a guard: a static (or continuous) effect whose targets are
    `remembered` is always a misread.
- [x] **Avacynian Missionaries** (same batch). (0100e79: "transform it" is the source) "At the beginning of your end
  step, if this creature is equipped, transform it." IR: `transform object
  (whatever was just referred to)`. In a trigger with nothing acted on yet,
  "it" should be the source. Same cause, unconfirmed on a board.

## Mana abilities lose their colour restriction

All of these read as "add one mana of any colour", so in a game the source
makes any colour, more than it should.

- [ ] **Exotic Orchard** (4), **Fellwar Stone** (12). "{T}: Add one mana of any
  color that a land an opponent controls could produce." IR: `adds 1 mana of
  any color`. Should be limited to the colours opponents' lands could produce.
- [ ] **Chrome Mox** (146). "{T}: Add one mana of any of the exiled card's
  colors." IR: any colour. Should be limited to the exiled card's colours, and
  produce nothing if nothing was exiled.
- [ ] **Reflecting Pool** (165). "{T}: Add one mana of any type that a land you
  control could produce." IR: any colour. Should be limited to the types your
  lands could produce, colourless included. As it stands it's a free
  five-colour land.
- [ ] **Mox Amber** (199). "{T}: Add one mana of any color among legendary
  creatures and planeswalkers you control." IR: any colour. Should be limited
  to those colours, and produce nothing when you control none.
- [ ] **Bloom Tender** (253). "{T}: For each color among permanents you control,
  add one mana of that color." IR: `adds the number of colors among all object
  you control mana of any color`. The count is right but the colours are free.
  It should add one of each of those colours.

## "This ability costs {1} less to activate" read as an effect on resolution

The channel lands' cost reduction ends up as the last effect of the ability,
`then object (this permanent itself) costs -1 times the number of all legendary
creature you control to cast`. So it does nothing to the activation cost, and
at resolution it reduces the cost of *casting* the land.

- [ ] **Boseiju, Who Endures** (71). Should reduce the activation cost by {1}
  per legendary creature you control (CR 601.2f, via 602.2b).
- [ ] **Otawara, Soaring City** (83). Same.
- [ ] **Takenuma, Abandoned Mire** (239). Same.

## Other misreads

- [ ] **Mystic Sanctuary** (169). "When this land enters untapped, you may put
  target instant or sorcery card from your graveyard on top of your library."
  IR: `Whenever this permanent enters battlefield` with no untapped condition,
  so it also triggers when it entered tapped. Should be an intervening "if it
  entered untapped" check.
- [ ] **The Great Henge** (222). "This spell costs {X} less to cast, where X is
  the greatest power among creatures you control." IR: `costs +0 to cast`. The
  reduction is lost, so it always costs {7}{G}{G}. Should reduce by the
  greatest power among your creatures (generic part only).
- [ ] **Dovin's Veto** (233). "This spell can't be countered." Read as a spell
  effect, `Object (this permanent itself) can't be_countered`, which runs on
  resolution, too late to matter. Should be a static ability that works on the
  stack, as Hullbreaker Horror's same line already is ("Continuously: ... can't
  be_countered").
- [ ] **Rishkar's Expertise** (238). "You may cast a spell with mana value 5 or
  less from your hand without paying its mana cost." IR: `[cast_without_paying]`
  with no optional wrapper, so it must cast. Should be "you may". Minor.
- [ ] **Panharmonicon** (262). "If an artifact or creature entering causes a
  triggered ability of a permanent you control to trigger, that ability triggers
  an additional time." IR: `[extra_trigger] 1 object you control`, without the
  "artifact or creature entering" condition, so every trigger you control would
  double. Should be limited to triggers caused by an artifact or creature
  entering the battlefield.
- [ ] **Ragavan, Nimble Pilferer** (275). "... exile the top card of that
  player's library. Until end of turn, you may cast that card." IR: `you may
  [cast_without_paying] object`. Ragavan lets you cast it **paying its cost**,
  not for free. The exile also doesn't name the damaged player's library.
  Should be: exile the top card of the damaged player's library, then you may
  cast it this turn, paying its costs. Mana of any type may not be spent; that
  is Ragavan's own clause, which this text doesn't have.

- [ ] **Dryad of the Ilysian Grove** (300). "Lands you control are every basic
  land type in addition to their other types." IR: the add-types effect (146)
  has no `keywords`, so no types are added. Urborg's and Yavimaya's carry
  `["Swamp"]` / `["Forest"]`. Should carry all five basic land types.

Note on Dovin's Veto: no part of the engine consumes `Act.BE_COUNTERED` yet
(`mtgfish/rules`), so Hullbreaker Horror's static isn't enforced either. That
gap is the rules agent's; this entry is only about the parse.

## Checked against the IR: read correctly, the read-back is lossy

These went into the golden set. The `explain` wording is the thing to fix, if
anyone does, and refreshing their golden entries is then expected.
- Urborg, Tomb of Yawgmoth (68), Yavimaya, Cradle of Growth (72), Roaming
  Throne (127). The added type prints as `is also 0`, but the IR has
  `keywords: ["Swamp"]`, `["Forest"]` and `of_chosen_type`. The explainer
  reads the wrong field.
- Bojuka Bog (19). Prints `exile all object in graveyard`, but the IR is
  targeted with a target-player scope.
- Chaos Warp (24). The reveal reads from the owner's library (`owner: 4`).
- Ash Barrens (201). The landcycling search has no zone, which the engine
  sends to the hand (`_do_search_library`).
