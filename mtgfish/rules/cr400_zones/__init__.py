"""Zones (CR 400-410).

A zone is where an object is, and CR 400.7 is the rule the rest of the engine
is built around: an object that changes zones becomes a *new* object, with no
memory of the old one. Nearly every surprising interaction in Magic traces
back to it.

There is no single module here because there is no single place where zones
are implemented - the rule is enforced wherever objects move, which is the
only place it can be. Where each part lives:

===========  =========================================================
CR 400.1     ``kernel/enums.py`` - the seven zones as ``Zone``
CR 400.3     ``kernel/enums.py`` - which zones are hidden
CR 400.7     ``kernel/game.py`` ``move_object`` - a new object each time,
             and ``kernel/gameobject.py`` for what does and does not carry
             over. ``kernel/ids.py``, ``kernel/events.py`` and
             ``kernel/matching.py`` all key off it
CR 704.8     ``cr700_additional_rules/cr704_sba.py`` - the consequence of
             CR 400.7: the old object is what last known information reads
CR 401.2     ``kernel/query.py``, ``kernel/matching.py`` - library order
CR 402.2     ``cr500_turn_structure/restrictions.py`` - hand size
CR 403.1     ``kernel/enums.py``, ``cr100_game_concepts/player.py`` - graveyards
CR 404.3     ``cr100_game_concepts/player.py`` - graveyard order
CR 405       ``cr600_spells_and_abilities/cr608_stack.py`` - the stack
===========  =========================================================
"""
