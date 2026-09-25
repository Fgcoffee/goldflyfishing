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
CR 707, 712  ``cr707_faces.py`` - copying, double-faced cards and
             transforming
CR 708       ``cr708_face_down.py`` - face-down spells and permanents,
             and manifest, cloak and manifest dread (CR 701.40, 701.58,
             701.62)
CR 714-732   ``cr725_designations.py`` - the monarch, the initiative and
             the other designations
CR 700.2     ``../cr600_spells_and_abilities/cr601_casting.py`` - modes,
             which are stated here but chosen during casting
===========  =========================================================
"""
