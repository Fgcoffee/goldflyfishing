"""Game concepts (CR 100-124).

The vocabulary the rest of the rules are written in: who the players are,
what an object is, what mana and costs and damage and counters are, and who
may act when.

===========  =========================================================
CR 102-104   ``player.py`` - players, and how a game is won or lost
CR 103       ``cr103_setup.py`` - starting the game, turn order, mulligans
CR 106-107   ``cr106_mana.py`` - mana, its types, pools and symbols
CR 107.1-2   ``cr107_numbers.py`` - integers only, never negative, and what
             an undeterminable number is
CR 111       ``cr111_tokens.py`` - tokens
CR 116       ``cr116_special_actions.py`` - the actions that use no stack
CR 117       ``cr117_priority.py`` - priority, and the ``Agent`` interface
             every decision is asked through
CR 118       ``cr118_costs.py`` - paying costs, and the unpayable ones
CR 119-122   ``actions.py`` - life, damage and counters
===========  =========================================================

Damage lives in ``actions.py`` rather than with combat because it is not a
combat rule: CR 120 is how *any* source damages anything, and combat is only
one caller of it.

``cr107_numbers.py`` is the odd one out: it holds no game state and knows
nothing about a Game. It exists because CR 107.1 and CR 107.2 are rules *about
every other rule* - what a number may be - and the engine had been rewriting
them by hand at each call site.

Stated here, implemented elsewhere
----------------------------------

Three of this section's concepts are too fundamental to live in a rules
package: the kernel is written in terms of them. They are indexed here because
this is the section that defines them, and because nothing else in the tree
says where to look. Module paths below are written without the ``.py``,
because these are not this package's files.

===========  =========================================================
CR 109       ``kernel/gameobject`` - objects; one class for all seven
             kinds the rule lists, discriminated by ``ObjectKind``
CR 110.1     ``kernel/gameobject`` ``GameObject.is_permanent`` - an
             object on the battlefield, and the live one: CR 400.7
             leaves the pre-move object behind still reading the zone it
             left, so the zone alone is not the question
CR 110.2     ``kernel/game`` - ``create_object`` gives a new object the
             owner of the card behind it, and ``move_object`` gives an
             object entering the battlefield the controller CR 110.2a
             asks for: the player the effect put it there for. A token
             is the exception the rule names, and takes its owner from
             its controller in ``actions`` ``create_token`` (CR 111.2)
CR 110.3     ``cr600_spells_and_abilities/cr613_layers`` - "as modified
             by any continuous effects" is the whole of the layer system
CR 110.4     ``kernel/enums`` ``PERMANENT_TYPES`` - the six of them. The
             rule's second half, that an instant or sorcery can never be
             a permanent, is enforced as CR 304.4 / 307.4 by
             ``kernel/game`` ``_may_enter_the_battlefield``, which
             refuses the move rather than removing it afterwards
CR 110.4b    ``cr600_spells_and_abilities/cr608_stack`` ``_resolve_spell``
             - a permanent spell is one whose resolution is becoming a
             permanent, which is exactly the branch that function takes
CR 110.4c    True by construction rather than by a rule of its own: a
             permanent is defined by its zone and never by its types, so
             one that loses every permanent type is still an object on
             the battlefield. Nothing in
             ``cr700_additional_rules/cr704_sba`` asks whether a
             permanent has a permanent type - every check there is
             guarded by the type it is about, so a typeless permanent
             falls through all of them
CR 112.1     ``kernel/gameobject`` ``GameObject.is_spell`` - a card on
             the stack, as opposed to an ability. It stops being one
             when it leaves the stack, which
             ``cr600_spells_and_abilities/cr608_stack`` does at the end
             of resolution
CR 112.1a    ``kernel/gameobject`` ``ObjectKind.COPY`` and
             ``cr700_additional_rules/cr707_faces`` ``copy_spell`` - a
             copy of a spell is a spell, and the only kind of object on
             the stack that is not one is an ability
CR 112.2     ``kernel/game`` ``create_object`` for the card's owner;
             ``cr700_additional_rules/cr707_faces`` ``copy_spell`` for
             the exception the rule makes of a copy, whose owner is the
             player it was put on the stack under the control of
CR 112.3     CR 110.3 again, for a spell, with the same answer: the
             layer system, which computes stack objects as well as
             permanents
===========  =========================================================

Notation (CR 107.8-107.18)
--------------------------

CR 107 ends with a run of rules about printed symbols that stand for whole
abilities: a leveler's level symbols (CR 107.8), a Saga's chapter symbols
(CR 107.15), a Class's level bars (CR 107.16). No symbol ever reaches this
engine - the parser reads oracle text - so each of these rules is honoured by
the code that builds the ability the symbol denotes, all of it in
``cr300_card_types/cr300_card_types``: CR 107.8a and CR 107.8b are the level
bands (as CR 711.2a-b), CR 107.15a the chapter triggers (as CR 714.2b),
CR 107.16a the class level bars (as CR 716.2a). CR 107.15b, several chapter
numbers on one line, is read in ``parser/split`` and expanded into one ability
each in ``parser/compile``. CR 107.13's colour indicator is applied in
``data/cards``, where a card's colours are settled.

Rules of this section the engine does not model
-----------------------------------------------

Said plainly, because a silent omission reads exactly like an implementation.

**CR 101.1, the golden rule.** There is no general mechanism for it and there
cannot be one here. A card overrides a rule in this engine only where the rules
code offers a seam for it to override: a replacement effect (CR 614), a
prohibition (CR 101.2), a continuous effect (CR 611). A card whose text
contradicts a rule in a way the effect vocabulary cannot express does not beat
the rule - it fails to compile, which the parser reports rather than hides.

**Not applicable.**

===========  =========================================================
CR 107.9     The tombstone icon. The rule ends by saying it has no
CR 107.10    effect on game play, and so does CR 107.10's type icon
CR 107.11    {PW} and {CHAOS}, faces of the planar die, which belongs to
CR 107.12    Planechase (CR 901) - a different casual variant
===========  =========================================================

**Gaps, not exclusions.** These are rules about Commander-legal cards that the
engine does not yet honour:

===========  =========================================================
CR 107.18    The pawprint symbol {P}, and with it CR 700.2i: choosing
             any set of modes whose pawprints total no more than a
             stated number. The modal machinery in
             ``cr600_spells_and_abilities/cr601_casting`` chooses a
             *count* of modes and has nowhere to put a per-mode weight
CR 107.17    The ticket symbol {TK}, and the ticket counters removed to
CR 107.17a   pay a ticket cost. Neither is modelled. Not the exclusion
             it looks like: dozens of cards in the pool carry ticket
             counters and Scryfall calls them Commander-legal. It fails
             loudly rather than quietly, at least - ``parser/costs``
             refuses {TK} as an unknown mana symbol instead of reading
             past it, so the whole cost is unreadable and the ability
             does not compile
CR 112.1b    Both depend on CR 707.12, casting a copy of an object that
CR 112.2a    is not already a spell. Nothing creates such a copy: the
             only copy the engine makes is CR 707.10's copy of a spell
             already on the stack, which is never cast. Once such a
             copy existed it would be a spell by construction, so these
             two rules are waiting on the mechanism, not on a decision
CR 110.2b    Half of it holds. A player who has gained control of a
             permanent spell does control the permanent it becomes,
             because ``_resolve_spell`` reads the spell's controller.
             The rest does not: the permanent's controller *by default*
             should stay with the player who put the spell on the
             stack, so that control goes back to them when the theft
             ends, and the resolution overwrites that default with the
             thief
CR 112.4     An effect that changed a permanent spell stops applying the
             moment it resolves. Two things are in the way, and both
             are in ``cr600_spells_and_abilities``: a settled continuous
             effect is registered against the battlefield only, so it
             cannot reach a spell at all, and the permanent is a new
             object (CR 400.7) that the settled set does not name
===========  =========================================================
"""
