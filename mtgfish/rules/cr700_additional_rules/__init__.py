"""Additional rules (CR 700-732).

Everything the earlier sections refer to but do not state: the state-based
actions, the keyword actions and keyword abilities, and the subsystems that
particular card types lean on.

===========  =========================================================
CR 701       ``cr701_keyword_actions.py`` - the keyword actions
CR 702       ``cr702_keyword_impl.py`` - the keyword abilities, one
             registered builder each; ``keywords.py`` is the registry
CR 704       ``cr704_sba.py`` - state-based actions, and CR 704.8 last
             known information
CR 707, 712  ``cr707_faces.py`` - face-down spells and permanents,
             double-faced cards and transforming
CR 714-732   ``cr725_designations.py`` - the monarch, the initiative and
             the other designations
CR 700.2     ``../cr600_spells_and_abilities/cr601_casting.py`` - modes,
             which are stated here but chosen during casting
===========  =========================================================
"""
