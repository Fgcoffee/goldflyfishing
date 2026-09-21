"""Parts of a card (CR 200-213).

The characteristics an object has, and where they come from.

===========  =========================================================
CR 202-213   ``characteristics.py`` - the computed characteristics of an
             object, which is what every other rule reads rather than
             the printed card
CR 205       ``cr205_typeline.py`` - the type line: card types, supertypes
             and subtypes, and parsing one
===========  =========================================================

Nothing here writes a characteristic. A permanent's power is derived by
running the layer system (CR 613, in ``cr600_spells_and_abilities``) over its
printed values plus the active continuous effects.
"""
