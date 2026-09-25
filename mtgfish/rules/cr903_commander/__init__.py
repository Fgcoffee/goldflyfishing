"""The Commander format (CR 903).

The format this engine plays. CR 903 is not a self-contained subsystem: it is
a set of amendments to rules elsewhere - a zone you may cast from, a cost
increase, an extra state-based action, an extra replacement effect - so each
part lives with the rule it amends:

===========  =========================================================
CR 903.2     ``cr100_game_concepts/cr103_setup.py`` - Free-for-All, attack
             multiple players, no limited range of influence
CR 903.3     ``kernel/gameobject.py`` ``is_commander`` - a fact about the
             card, carried across every zone change by CR 400.7
CR 903.3b    ``cr700_additional_rules/cr702_keyword_impl.py`` - melded
CR 903.4     ``kernel/values.py``, ``kernel/query.py`` - colour identity
CR 903.6     ``cr100_game_concepts/cr103_setup.py`` - starting in the
             command zone
CR 903.7     ``cr100_game_concepts/cr103_setup.py`` - 40 life
CR 903.8     ``kernel/legality.py``, ``cr600_spells_and_abilities/
             cr601_casting.py``, ``cr100_game_concepts/player.py`` - casting
             a commander from the command zone, and the {2} tax
CR 903.9a    ``cr700_additional_rules/cr704_sba.py`` - the state-based
             action for a commander in a graveyard or exile
CR 903.9b    ``cr600_spells_and_abilities/cr614_replacement.py`` - the
             replacement effect for one going to hand or library
CR 903.10a   ``cr100_game_concepts/player.py`` - 21 combat damage from a
             single commander, per commander and never pooled; the
             state-based action reading it is CR 704.6c in
             ``cr700_additional_rules/cr704_sba.py``
CR 903.11    ``cr400_zones/cr400_outside_game.py`` - what may come in from
             outside the game, and 903.11a's name and identity limits
===========  =========================================================

Deck construction (CR 903.5) is not a rules-engine concern - it is checked
before a game starts, in mtgfish/data/decks/model.py.
"""
