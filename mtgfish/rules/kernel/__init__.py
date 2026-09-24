"""Not a CR section: the machinery the rules are written against.

Everything here exists so that the nine CR packages beside it can be written
as the rules are written. If a module's job can be named by a rule number it
does not belong here - it belongs in that rule's package.

- ``game.py``, ``gameobject.py``, ``player.py`` sibling - the game state and
  the objects in it, including ``move_object``, where CR 400.7 is enforced
- ``ids.py``, ``enums.py``, ``events.py`` - the primitive types
- ``query.py``, ``matching.py``, ``values.py``, ``conditions.py`` - how an
  effect says what it applies to, and how that is answered
- ``legality.py`` - which actions a player may take right now
- ``citations.py`` - the CR citations in this source tree, checked against
  the shipped rules text so a rules release cannot silently invalidate them
- ``coverage.py`` - what is implemented, by rule number
- ``loops.py`` - deciding how far a repeating game state is run
- ``relaxations.py`` - rules deliberately bent, each one named
- ``log.py`` - the game log
"""
