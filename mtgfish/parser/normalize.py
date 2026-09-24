"""Cleaning oracle text before anything tries to read it.

Every transformation here is lossless in the sense that matters: it removes or
rewrites text that has no rules meaning, and never text that does. That
distinction is the whole job.

Reminder text is the big one. "Flying (This creature can't be blocked except by
creatures with flying or reach.)" carries its rules in the word *flying*; the
parenthesis is a restatement for players. Parsing it would be harmless but
wasteful - and worse, some reminder text describes the ability in words the
grammar would misread as a *second* ability.

Self-reference is the subtle one. A card that says "Ajani's Pridemate gets a
+1/+1 counter" means *this creature*, and a parser that treats the name as an
arbitrary card name will look for a card called Ajani's Pridemate on the
battlefield and find the wrong one - or several.
"""

from __future__ import annotations

import re

#: Scryfall uses typographic dashes, quotes and ellipses. They are noise for a
#: tokenizer and a constant source of near-miss string comparisons.
_REPLACEMENTS = {
    "—": " - ",  # em dash: separates a keyword from its cost or effect
    "–": "-",  # en dash
    "’": "'",  # right single quote
    "‘": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",  # non-breaking space
    "−": "-",  # minus sign, as in -1/-1
}

#: Reminder text, which is always parenthesised and never load-bearing.
#: Nested parentheses do not occur in oracle text, so one non-greedy pass is
#: enough and a stack would be pretending to a generality that does not exist.
_REMINDER = re.compile(r"\s*\([^)]*\)")

#: Ability words - "Landfall", "Metalcraft", "Delirium" - are flavour. CR
#: 207.2c: they have no rules meaning and are italicised followed by a long
#: dash. Removing them leaves the real ability behind.
_ABILITY_WORD = re.compile(r"^[A-Z][A-Za-z' ]{2,30} - (?=[A-Z{])")


def normalize(text: str, *, card_name: str = "") -> str:
    """Prepare oracle text for tokenizing.

    ``card_name`` lets self-references collapse to ``this``, which is what the
    rules mean by them (CR 201.5) and what the engine's ``source_only`` filter
    expects.
    """
    if not text:
        return ""

    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)

    text = _REMINDER.sub("", text)
    # The em-dash substitution leaves double spaces ("Landfall  -  Whenever"),
    # and every phrase match downstream assumes single ones. Collapsing runs of
    # spaces here rather than in the tokenizer keeps the ability-word regex -
    # which anchors on a literal " - " - working.
    text = re.sub(r"[ 	]+", " ", text)
    text = _resolve_self_reference(text, card_name)

    lines = [_strip_ability_word(line.strip()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _strip_ability_word(line: str) -> str:
    """Drop a leading ability word (CR 207.2c).

    Only at the start of a line, and only when followed by something that
    looks like the beginning of a real ability - otherwise "Equip - {2}" and
    every other legitimate keyword-dash-cost line would lose its keyword.
    """
    from ..rules.cr700_additional_rules.keywords import ABILITY_WORDS

    match = _ABILITY_WORD.match(line)
    if not match:
        return line
    word = line[: match.end()].split(" - ")[0].strip()
    if word.lower() not in ABILITY_WORDS:
        return line
    return line[match.end() :].strip()


def _resolve_self_reference(text: str, card_name: str) -> str:
    """Turn the card's own name into ``this`` (CR 201.5).

    The legend-name shortening matters: "Ajani, Nacatl Pariah" is referred to
    on its own card as "Ajani", so both the full name and the part before the
    first comma have to go. Longest first, or replacing the short form would
    leave the rest of the full name stranded as loose words.
    """
    if not card_name:
        return text

    forms = [card_name]
    if "," in card_name:
        forms.append(card_name.split(",", 1)[0].strip())
    # Split cards and adventures name their halves; each half's own name is a
    # self-reference on that half.
    if "//" in card_name:
        forms.extend(part.strip() for part in card_name.split("//"))

    for form in sorted({f for f in forms if f}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(form)}\b", "this", text)
    # "Ajani's Pridemate's power" becomes "this's power", which is not
    # English and not a phrase the grammar knows. The rules say "its
    # power", which ``parse_value`` already reads.
    return text.replace("this's", "its")


def strip_reminder(text: str) -> str:
    """Reminder text only, for callers that want the rest left alone."""
    return _REMINDER.sub("", text).strip()


def is_reminder_only(text: str) -> bool:
    """Whether a line is nothing but reminder text."""
    return bool(text.strip()) and not strip_reminder(text)
