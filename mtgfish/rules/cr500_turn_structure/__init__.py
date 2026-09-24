"""Turn structure (CR 500-514).

The phases and steps, the turn-based actions each one takes, and combat.

===========  =========================================================
CR 500-514   ``cr500_turn.py`` - the phases and steps, and the turn-based
             actions: untap, upkeep, draw, cleanup
CR 506-511   ``cr506_combat.py`` - attackers, blockers, combat damage and
             the end of combat
CR 508-509   ``restrictions.py`` - the constraint problem a declaration
             has to solve: every restriction obeyed, and the greatest
             possible number of requirements satisfied
===========  =========================================================

Combat damage division (CR 510.1c-d) is a choice, so like every other choice
it is asked of the ``Agent`` in ``cr100_game_concepts/cr117_priority.py``;
what this package owns is which answers are legal.
"""
