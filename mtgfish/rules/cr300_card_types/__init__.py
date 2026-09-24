"""Card types (CR 300-315).

What each card type is and the rules that come with it: artifacts and their
Vehicles, creatures, enchantments and their Auras, instants, lands,
planeswalkers, sorceries, battles and the rest.

Two modules live here, holding the parts of section 3 that have nowhere else
to go:

=====================  ===============================================
CR 208.3, 301.7,       ``cr300_characteristics.py``
306.5, 310.4
CR 710-721             ``cr300_card_types.py``
=====================  ===============================================

``cr300_characteristics.py`` holds the three characteristics only one card type
has - power and toughness, loyalty, defense - and the rules that say when a
permanent actually has them. ``cr300_card_types.py`` holds the card types whose
rules are a printed glyph rather than oracle text.

Everything else in section 3 is a rule *about* a card type rather than a
mechanism of its own, so it is enforced where that mechanism lives - the
timing rules in the legality check, the resolution rules on the stack, the
state-based actions in CR 704. This is the index of where each one went.
Module paths below are written without the ``.py``, because these are not
this package's files: they are the homes the rules found elsewhere.

**What every card type shares.** The seven "you may cast one of these, and
here is what happens when it resolves" rules are one mechanism each, not
seven:

CR 301.1, CR 302.1, CR 303.1, CR 306.1, CR 310.1 - an artifact, creature,
   enchantment, planeswalker or battle is cast at sorcery speed, from hand.
   CR 304.1 says an instant is not. Both are ``kernel/legality``
   ``_spell_timing``, over the window ``has_sorcery_speed`` defines.
CR 301.2, CR 302.2, CR 303.2, CR 306.2, CR 310.2 - a permanent spell resolves
   by becoming a permanent under its controller's control, in
   ``cr600_spells_and_abilities/cr608_stack`` ``_resolve_spell``. CR 304.2 and
   CR 307.2 are the other half of the same branch: an instant or a sorcery
   does what it says and is then put into its owner's graveyard.
CR 301.3, CR 302.3, CR 303.3, CR 304.3, CR 306.3, CR 307.3, CR 310.3 -
   subtypes, one registry for all of them, in
   ``cr200_parts_of_a_card/cr205_typeline``. The rules differ only in which
   list of names is legal for the type, which is what the registry stores.
   CR 304.3 and CR 307.3 share one list, since spell types are common to
   instants and sorceries.

**Per type.**

===========  =========================================================
CR 301.4     Honoured by omission: an artifact is given nothing for being
             an artifact. Its colour comes from its mana cost (CR 202) in
             ``cr100_game_concepts/cr106_mana``, so a coloured artifact is
             coloured and a colourless nonartifact is colourless, and
             ``settle_type_characteristics`` here leaves a noncreature
             artifact with no power and no toughness
CR 302.5     ``cr500_turn_structure/cr506_combat`` ``can_attack`` and
             ``can_block_at_all`` - being a creature is the whole
             requirement, which is why an animated land or Vehicle attacks
             on exactly the same terms
CR 302.7     Marked on the creature by ``cr100_game_concepts/actions``
             ``deal_damage``; lethal marked damage destroys it as CR 704.5g
             in ``cr700_additional_rules/cr704_sba``; it is removed in the
             cleanup step (CR 514.2) in
             ``cr500_turn_structure/cr500_turn``
CR 303.7     Role is an Aura subtype and needs no code of its own; the rule
             that Roles carry is CR 303.7a
CR 303.7a    ``cr700_additional_rules/cr704_sba`` ``_check_role_rule``,
             which the rules also number CR 704.5z - one Role per permanent
             per player, the newest kept
CR 304.5     Honoured by the shape of the legality check: "any time they
             could cast an instant" is "has priority", and
             ``kernel/legality`` asks about casting a spell as its own act,
             separate from activating an ability or taking a special
             action. A player who can't cast spells keeps all three
CR 306.4     The uniqueness rule is gone, and nothing replaced it: two
             planeswalkers of the same planeswalker type coexist. The
             legend rule that superseded it is CR 704.5j in
             ``cr700_additional_rules/cr704_sba``
CR 306.6     ``cr500_turn_structure/cr506_combat`` ``_may_be_attacked``
CR 306.7     Honoured by omission: noncombat damage dealt to a player is
             dealt to that player. ``cr100_game_concepts/actions``
             ``deal_damage`` has no redirection step to remove
CR 306.8     ``cr100_game_concepts/actions`` ``deal_damage`` - damage to a
             planeswalker removes that many loyalty counters and marks
             nothing
CR 306.9     CR 704.5i in ``cr700_additional_rules/cr704_sba``
CR 307.4     ``kernel/game`` ``move_object`` refuses the move, alongside
             CR 304.4 for instants - the rule is that a sorcery never
             arrives, not that something removes it afterwards
CR 310.5     ``cr500_turn_structure/cr506_combat`` ``_may_be_attacked`` -
             but see CR 310.11 below for who gets to attack it
CR 310.6     ``cr100_game_concepts/actions`` ``deal_damage`` - damage to a
             battle removes that many defense counters
CR 310.7     CR 704.5v in ``cr700_additional_rules/cr704_sba``
CR 310.8     CR 704.5w in the same place
CR 310.10    CR 704.5p in the same place: a battle attached to anything
             becomes unattached, because no rule ever let it be attached
===========  =========================================================

**Not implemented: the protector, and Sieges.** CR 310.9 gives every battle a
player designated as its protector, and nothing in the engine models one.
Four rules fall with it:

===========  =========================================================
CR 310.11    The state-based action that gives a protectorless battle a new
             protector, or puts it into its owner's graveyard when no
             player can be one. CR 704.5x, absent from
             ``cr700_additional_rules/cr704_sba``
CR 310.12    Sieges are subject to special rules - the two below
CR 310.12a   A Siege's controller chooses its protector from among their
             opponents as it enters. Without it,
             ``cr500_turn_structure/cr506_combat`` falls back to the
             battle's *controller* as the defending player, which is
             backwards for the ordinary case: you cast a Siege, an opponent
             protects it, and you are the one who attacks it
CR 310.12b   The intrinsic "when the last defense counter is removed, exile
             it, then you may cast it transformed" - so a Siege that is
             beaten dies under CR 310.7 instead of flipping
===========  =========================================================

**Other sections' business.** Several types delegate the bulk of their rules
elsewhere, because the rule is stated in section 700: a Saga's chapters and a
Class's levels are CR 714 and CR 716, and battles take their state-based
actions from CR 704 - all in ``cr700_additional_rules``. Lands (CR 305) are
played rather than cast (CR 305.1, a special action in CR 116) and their land
types carry intrinsic mana abilities (CR 305.6); both, with the type-setting
rule CR 305.7, live in ``cr600_spells_and_abilities/cr613_layers`` and
``kernel/legality``. CR 308 (Kindred) and CR 309 (dungeons) are partly done
and are scored that way in the coverage table.

**Not applicable.** CR 311 planes, CR 312 phenomena, CR 313 vanguards,
CR 314 schemes and CR 315 conspiracies are card types that exist only in
casual variants - Planechase, Archenemy, Vanguard and draft. This engine
plays Commander (CR 903), which uses none of them, so there is nothing to
implement rather than something missing. A card of one of those types cannot
legally be in a Commander deck and never reaches a zone this engine models.
"""
