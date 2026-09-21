"""Game concepts (CR 100-124).

The vocabulary the rest of the rules are written in: who the players are,
what an object is, what mana and costs and damage and counters are, and who
may act when.

===========  =========================================================
CR 102-104   ``player.py`` - players, and how a game is won or lost
CR 103       ``cr103_setup.py`` - starting the game, turn order, mulligans
CR 106-107   ``cr106_mana.py`` - mana, its types, pools and symbols
CR 107.1-2   ``cr107_numbers.py`` - integers only, never negative, and what
             an undeterminable number is
CR 111       ``cr111_tokens.py`` - tokens
CR 116       ``cr116_special_actions.py`` - the actions that use no stack
CR 117       ``cr117_priority.py`` - priority, and the ``Agent`` interface
             every decision is asked through
CR 118       ``cr118_costs.py`` - paying costs, and the unpayable ones
CR 119-122   ``actions.py`` - life, damage and counters
===========  =========================================================

Damage lives in ``actions.py`` rather than with combat because it is not a
combat rule: CR 120 is how *any* source damages anything, and combat is only
one caller of it.

``cr107_numbers.py`` is the odd one out: it holds no game state and knows
nothing about a Game. It exists because CR 107.1 and CR 107.2 are rules *about
every other rule* - what a number may be - and the engine had been rewriting
them by hand at each call site.
"""
