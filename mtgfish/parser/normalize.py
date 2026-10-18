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


def normalize(
    text: str,
    *,
    card_name: str = "",
    legendary: bool = False,
    planeswalker_types: tuple[str, ...] = (),
) -> str:
    """Prepare oracle text for tokenizing.

    ``card_name`` lets self-references collapse to ``this``, which is what the
    rules mean by them (CR 201.5) and what the engine's ``source_only`` filter
    expects. ``legendary`` and ``planeswalker_types`` describe the face the
    text is on: a legendary card may call itself by a shortened name (CR
    201.5c), and a planeswalker's shortened name is also one of its own
    planeswalker types ("Karn" on Karn Liberated).
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
    # Flavor words go before self-reference: they often contain the card's
    # own name ("Blade of Magnus - ..."), and resolving that first would leave
    # "Blade of this" behind, which no longer looks like a flavor word.
    text = "\n".join(
        _strip_flavor_word(line.strip(), text) for line in text.split("\n")
    )
    text = _resolve_self_reference(
        text, card_name, legendary=legendary, planeswalker_types=planeswalker_types
    )

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


#: A flavor word and its dash (CR 207.2d): "Keen Senses - When this creature
#: enters, draw a card." The label cannot hold a cost, a results-table bar, a
#: colon or a quotation, all of which mean the text before the dash is part of
#: an ability rather than a name for one.
_FLAVOR_WORD = re.compile(r"^(?P<head>[^\s{}|:\"•+\d-][^{}|:\"•]{0,60}?) - (?P<rest>\S.*)$")

#: A Saga chapter's symbols, which may carry a flavor word of their own:
#: "I - Aerospark - Exile target creature ...".
_CHAPTER_PREFIX = re.compile(r"^(?P<chapters>[IVX]+(?:, [IVX]+)*) - (?P<rest>.*)$")

#: Words a title may leave in lower case. Anything else in lower case makes
#: the text before the dash a sentence, not a name: "Each opponent faces a
#: villainous choice - ..." is an effect, and "To solve - ..." a Case's rule.
_TITLE_SMALL_WORDS = frozenset(
    {"a", "an", "the", "of", "for", "to", "in", "on", "from", "with", "at",
     "by", "and", "or", "as", "into", "is", "my", "your", "our"}
)

#: Verbs that open a cost (CR 118). "Ward - Discard a card." is a keyword and
#: its cost; the keyword registry recognises every keyword it knows, and this
#: is the second line of defence for one it does not - a cost left behind as
#: a sentence would read as an effect.
_COST_OPENERS = re.compile(
    r"^(?:Pay|Discard|Sacrifice|Exile|Tap|Untap|Return|Remove|Reveal|Put|Mill|"
    r"Collect|Forage)\b"
)


def _strip_flavor_word(line: str, whole_text: str = "") -> str:
    """Drop a leading flavor word (CR 207.2d).

    Flavor words are the ability words' cousins - "Keen Senses", "Blade of
    Magnus", "Do You Like Squirrels?" - italicised and followed by a long dash,
    with no rules meaning at all. Unlike ability words there is no list of
    them: each is written for one card. What they share is their *shape*,
    which is a title: capitalised words, no cost, no numbers, and nothing the
    keyword registry knows. Anything that is not that shape is left alone,
    so the worst this can do is leave a line unread.

    A label the rest of the card refers to is not flavor: an Attraction's
    "Prize - ..." is what "claim the prize" means, and without its label the
    ability would read as something the card does on its own.

    A Saga chapter can carry one after its symbols, and it is stripped there
    too.
    """
    chapter = _CHAPTER_PREFIX.match(line)
    if chapter:
        rest = _strip_flavor_label(chapter.group("rest"), whole_text, in_chapter=True)
        return f"{chapter.group('chapters')} - {rest}"
    return _strip_flavor_label(line, whole_text, in_chapter=False)


def _strip_flavor_label(line: str, whole_text: str, *, in_chapter: bool) -> str:
    from ..rules.cr700_additional_rules.keywords import is_known

    match = _FLAVOR_WORD.match(line)
    if not match:
        return line
    head = match.group("head").strip()
    rest = match.group("rest")
    words = head.split()
    if not words or len(words) > 8:
        return line
    if whole_text.lower().count(head.lower()) > 1:
        return line
    # A keyword with a dash cost ("Ward - Pay 2 life"), "Max speed", "Visit",
    # an ability word - anything the registry knows keeps its dash.
    if any(is_known(" ".join(words[:n])) for n in range(len(words), 0, -1)):
        return line
    if all(re.fullmatch(r"[IVX]+,?", word) for word in words):
        return line
    if not _is_title(words):
        return line
    if not in_chapter and _COST_OPENERS.match(rest) and ":" not in rest:
        return line
    return rest.strip()


def _is_title(words: list[str]) -> bool:
    """Whether a run of words is shaped like a name rather than a sentence."""
    first = words[0].strip(".!?'\"")
    if first and not first[0].isupper():
        return False
    for word in words:
        core = word.strip(".,!?'\"")
        if not core:
            continue
        if core[0].isupper() or core.lower() in _TITLE_SMALL_WORDS:
            continue
        return False
    return True


#: An Alchemy rebalance is named "A-" plus the original's name in the card
#: data, and its oracle text calls it by the original name: "When Elderleaf
#: Mentor enters" on A-Elderleaf Mentor. On Arena the rebalanced card *has*
#: that name; the prefix is a catalogue marker, not part of it.
_REBALANCED = "A-"

#: Words that make a following name a noun phrase about some *other* object:
#: "a Gideon planeswalker", "each Garruk you control", "cards named Koma's
#: Coil". A card's reference to itself is a bare proper name and never takes
#: one of these.
_NOT_SELF_BEFORE = frozenset(
    {"a", "an", "the", "another", "other", "each", "every", "all", "any", "no",
     "named", "those", "these", "your", "their", "his", "her", "its"}
)

#: Nouns that turn a name into a type word when they follow it: "Sliver
#: creature", "a Lukka planeswalker".
_NOT_SELF_NOUNS = frozenset(
    {"creature", "creatures", "card", "cards", "spell", "spells", "token",
     "tokens", "permanent", "permanents", "planeswalker", "planeswalkers",
     "artifact", "artifacts", "enchantment", "enchantments", "land", "lands",
     "emblem", "emblems"}
)


def _resolve_self_reference(
    text: str,
    card_name: str,
    *,
    legendary: bool = False,
    planeswalker_types: tuple[str, ...] = (),
) -> str:
    """Turn the card's own name into ``this`` (CR 201.5).

    Three kinds of name, from safest to least safe:

    * the full name - and, on an Alchemy rebalance, the name without its
      "A-" prefix - which on its own card can only mean that card;
    * a legend's name up to the first comma ("Ajani" on "Ajani, Nacatl
      Pariah");
    * CR 201.5c's other shortened names, which only legendary cards use:
      "Loran" on Loran of the Third Path, "Rosie Cotton" on Rosie Cotton of
      South Lane.

    The last two are words that also name other things - a different legend
    ("Tuktuk the Returned", "Nissa's Chosen"), a type ("each Garruk you
    control", "a Lukka planeswalker") - so they are replaced only where they
    stand as a bare proper name (``_is_self_mention``). Longest first, or
    replacing the short form would leave the rest of the full name stranded as
    loose words.
    """
    if not card_name:
        return text

    exact = [card_name]
    if card_name.startswith(_REBALANCED) and len(card_name) > len(_REBALANCED):
        exact.append(card_name[len(_REBALANCED) :])
    # Split cards and adventures name their halves; each half's own name is a
    # self-reference on that half. The separator has spaces round it: a name
    # can contain the slashes themselves ("SP//dr, Piloted by Peni"), and
    # splitting that made "SP" and "dr" self-references.
    if " // " in card_name:
        exact.extend(part.strip() for part in card_name.split(" // "))

    guarded: list[str] = []
    for name in exact:
        if "," in name:
            guarded.append(name.split(",", 1)[0].strip())
    if legendary:
        for name in exact:
            guarded.extend(_shortened_names(name, planeswalker_types))

    for form in sorted({f for f in exact if f}, key=len, reverse=True):
        text = re.sub(_name_pattern(form), "this", text)
    for form in sorted({f for f in guarded if f} - set(exact), key=len, reverse=True):
        text = re.sub(
            _name_pattern(form),
            lambda m: "this" if _is_self_mention(m.string, m.start(), m.end()) else m.group(0),
            text,
        )
    # "Ajani's Pridemate's power" becomes "this's power", which is not
    # English and not a phrase the grammar knows. The rules say "its
    # power", which ``parse_value`` already reads.
    return text.replace("this's", "its")


def _name_pattern(name: str) -> str:
    """A name as a whole run of words.

    Not ``\\b``: a name can end in punctuation ("Blood for the Blood God!"),
    and a word boundary after "!" needs a word character next, which a space
    is not - so the full name was never found. A hyphen is part of a word
    here, so "Niv" does not match inside "Niv-Mizzet".
    """
    return rf"(?<![\w'-]){re.escape(name)}(?![\w-])"


def _shortened_names(name: str, planeswalker_types: tuple[str, ...]) -> list[str]:
    """The shortened names a legendary card may call itself (CR 201.5c).

    Oracle text shortens a legend's name from the front: "Loran" for Loran of
    the Third Path, "Rosie Cotton" for Rosie Cotton of South Lane, "Karn" for
    Karn Liberated. So the candidates are the leading runs of words, and a run
    is a candidate only if it could be a proper name at all:

    * it ends on a capitalised word - "Tuktuk the" or "Krydle of" is not a
      name, only the start of one;
    * it is not "The ..." - "The One" is not what The One Ring calls itself;
    * it is not possessive - "Gaea's" names nothing;
    * it is not a keyword ("Flash" on Flash Thompson);
    * it is not a subtype ("Sliver" on Sliver Queen, "Rat" on Rat King), with
      one exception: a planeswalker's own planeswalker type *is* its short
      name ("Karn" on Karn Liberated, whose type line says Karn). A mention
      that uses it as a type word is still caught by ``_is_self_mention``.
    """
    from ..rules.cr200_parts_of_a_card.cr205_typeline import active_registry
    from ..rules.cr700_additional_rules.keywords import is_known

    registry = active_registry()
    base = name.split(",", 1)[0].strip()
    words = base.split()
    out: list[str] = []
    for count in range(len(words) - 1, 0, -1):
        form = " ".join(words[:count])
        last = words[count - 1]
        if words[0] in ("The", "A", "An"):
            break
        if not last[:1].isupper() or last.endswith("'s"):
            continue
        if is_known(form):
            continue
        if registry.known(form) and form not in planeswalker_types:
            continue
        out.append(form)
    return out


def _is_self_mention(text: str, start: int, end: int) -> bool:
    """Whether a short name at ``text[start:end]`` stands for this card.

    It does unless its neighbours make it part of something else:

    * a determiner or "named" before it ("a Lukka planeswalker", "each Garruk
      you control", "tokens named Koma's Coil");
    * a capitalised word after it, directly or after "'s", "the", "of" or a
      comma - it is the start of a longer name ("Stangg Twin", "Nissa's
      Chosen", "Tuktuk the Returned", "Mishra, Lost to Phyrexia");
    * a type noun after it ("Sliver creature", "Gideon planeswalker");
    * a keyword made with the next word, or a line that is nothing but it
      and one more word - "Space sculptor" on Space Beleren is a keyword,
      not a sentence about the card.
    """
    from ..rules.cr700_additional_rules.keywords import is_known

    before = text[:start]
    after = text[end:]
    line_start = before.rfind("\n") + 1
    if not before[line_start:].strip() and re.match(r" [a-z]+ *(?:\n|$)", after):
        return False
    previous = re.search(r"([\w'-]+) $", before)
    if previous and previous.group(1).lower() in _NOT_SELF_BEFORE:
        return False
    if re.match(r" +[A-Z]", after) or re.match(r"'s [A-Z]", after):
        return False
    if re.match(r" (?:the|of) [A-Z]", after) or re.match(r", (?:the )?[A-Z]", after):
        return False
    following = re.match(r" ([\w'-]+)", after)
    if following:
        word = following.group(1)
        if word.lower() in _NOT_SELF_NOUNS:
            return False
        if is_known(text[start:end] + " " + word):
            return False
    return True


def strip_reminder(text: str) -> str:
    """Reminder text only, for callers that want the rest left alone."""
    return _REMINDER.sub("", text).strip()


def is_reminder_only(text: str) -> bool:
    """Whether a line is nothing but reminder text."""
    return bool(text.strip()) and not strip_reminder(text)
