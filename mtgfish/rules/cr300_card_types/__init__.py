"""Card types (CR 300-315).

What each card type is and the rules that come with it: artifacts and their
Vehicles, creatures, enchantments and their Auras, instants, lands,
planeswalkers, sorceries, battles and the rest.

=====================  ===============================================
CR 208.3, 301.7,       ``cr300_characteristics.py``
306.5, 310.4
CR 710-721             ``cr300_card_types.py``
=====================  ===============================================

``cr300_characteristics.py`` holds the three characteristics only one card type
has - power and toughness, loyalty, defense - and the rules that say when a
permanent actually has them. ``cr300_card_types.py`` holds the card types whose
rules are a printed glyph rather than oracle text.

Several types delegate the bulk of their rules elsewhere, because the rule is
stated in section 700: a Saga's chapters and a Class's levels are CR 714 and
CR 716, and battles take their state-based actions from CR 704 - all in
``cr700_additional_rules``.
"""
