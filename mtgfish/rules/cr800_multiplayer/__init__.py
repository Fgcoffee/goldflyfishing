"""Multiplayer rules (CR 800-811).

What changes with more than two players. A Commander game is a Free-for-All
(CR 806) with the attack multiple players option and without the limited
range of influence option (CR 903.2), so most of section 800 is honoured by
*omission*: every other player is an opponent, and nothing narrows what may
be targeted.

What is left is CR 800.4, what happens when a player leaves the game, and it
is enforced at each point where a departed player would otherwise still be
acted upon - so it lives with those rules rather than here:

===========  =========================================================
CR 800.4     ``kernel/game.py`` - a player leaving, and turn order
             closing over the gap
CR 800.4a    ``kernel/game.py`` - their objects leave with them, and
             ``cr600_spells_and_abilities/cr603_triggers.py`` - their
             abilities on the stack cease to exist
CR 800.4b    ``cr100_game_concepts/cr111_tokens.py`` - their tokens cease
CR 800.4e    ``cr100_game_concepts/actions.py`` - combat damage is not
             assigned to a player who has left
CR 800.4k    ``cr600_spells_and_abilities/cr611_durations.py`` - a turn that
             would begin for a departed player does not begin
CR 800.4m    ``cr600_spells_and_abilities/cr611_durations.py`` - an effect
             lasting until their turn never ends
CR 802.1     ``cr500_turn_structure/cr506_combat.py`` - attacking any opponent
CR 806.3     ``cr100_game_concepts/cr103_setup.py`` - random seating
===========  =========================================================
"""
