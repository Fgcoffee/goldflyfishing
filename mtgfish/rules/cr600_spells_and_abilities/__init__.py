"""Spells, abilities and effects (CR 600-616).

The largest section, and the one the engine is shaped around: how something
gets cast or activated, what happens while it is on the stack, and how
effects that last change what everything else sees.

===========  =========================================================
CR 601-602   ``cr601_casting.py`` - casting a spell, activating an ability,
             choosing targets and modes (CR 700.2), paying the total cost
CR 603       ``cr603_triggers.py`` - triggered abilities: what triggers,
             when it is put on the stack, and CR 603.10 look-back
CR 605-608   ``cr608_stack.py`` - the stack, and resolution
CR 611       ``cr611_durations.py`` - when a continuous effect ends
CR 613       ``cr613_layers.py`` - the layer system, dependency and
             timestamp order. The heart of the engine
CR 614-616   ``cr614_replacement.py`` - replacement and prevention effects
CR 113       ``abilities.py`` - the four kinds of ability
CR 609-610   ``effects.py``, ``resolve.py`` - effects as data, and the
             interpreter that carries them out
===========  =========================================================

``effects.py`` holds the rule the whole package obeys: an effect is an opcode
plus typed parameters, never a closure, so it can be inspected, serialized
and tested without a game.
"""
