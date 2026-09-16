"""Turning an ability's text into a token stream.

A hand-written tokenizer rather than a regex split, for one reason: mana
symbols. ``{T}``, ``{2}``, ``{G/U}``, ``{X}`` and ``{2/W}`` are single tokens
that happen to contain characters a naive splitter would break on, and getting
them wrong poisons every cost the parser ever reads.

Numbers are kept as their own kind because the grammar constantly needs to ask
"is the next thing a count?" - and because "3" and "three" mean the same thing
in oracle text and should tokenize to the same value.

The stream is a flat list with a cursor rather than a tree. Oracle text is
templated, not nested, and a cursor over a list makes backtracking - which a
recursive-descent parser does constantly - a single integer assignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class TokenKind(IntEnum):
    WORD = 0
    NUMBER = 1
    SYMBOL = 2  # {T}, {2}, {G/U}
    PUNCT = 3  # , . : ; " -
    PT = 4  # 2/2, +1/+1, -X/-X
    END = 5


#: Written-out numbers, which oracle text uses below eleven and digits above.
WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fifteen": 15, "twenty": 20,
    "a": 1, "an": 1,
}

#: Ordinals, for "the second time" and chapter-like phrasing.
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_SYMBOL = re.compile(r"\{[^}]{1,8}\}")
_PT = re.compile(r"[+-]?(?:\d+|[XYZ*])/[+-]?(?:\d+|[XYZ*])")
_NUMBER = re.compile(r"\d+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
#: ``+`` and the Unicode minus are here so a loyalty cost keeps its sign.
#: Dropping the ``+`` made "+1:" and "1:" tokenize identically, and dropping
#: the sign entirely is how every planeswalker ability in the format failed to
#: parse - the effect read fine and the cost was unreadable.
_PUNCT = re.compile(r'[,.:;"()\[\]+−]|--?')


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str
    #: For NUMBER, the value. Written-out numbers carry it too, so the grammar
    #: never has to care which form the card used.
    value: int = 0

    def __str__(self) -> str:
        return self.text

    @property
    def lower(self) -> str:
        return self.text.lower()


END = Token(TokenKind.END, "")


def tokenize(text: str) -> list[Token]:
    """Split one ability's text into tokens.

    Unrecognised characters are dropped rather than raising: a stray character
    is not worth failing an ability over, and full consumption will catch
    anything that actually mattered because the surrounding words will not
    parse either.
    """
    tokens: list[Token] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char.isspace():
            index += 1
            continue

        # Order matters. P/T patterns contain digits and slashes, so they must
        # be tried before plain numbers, or "+1/+1" tokenizes as three things.
        if char == "{":
            match = _SYMBOL.match(text, index)
            if match:
                tokens.append(Token(TokenKind.SYMBOL, match.group()))
                index = match.end()
                continue

        match = _PT.match(text, index)
        if match:
            tokens.append(Token(TokenKind.PT, match.group()))
            index = match.end()
            continue

        match = _NUMBER.match(text, index)
        if match:
            tokens.append(
                Token(TokenKind.NUMBER, match.group(), int(match.group()))
            )
            index = match.end()
            continue

        match = _WORD.match(text, index)
        if match:
            word = match.group()
            value = WORD_NUMBERS.get(word.lower(), 0)
            kind = TokenKind.NUMBER if word.lower() in WORD_NUMBERS else TokenKind.WORD
            tokens.append(Token(kind, word, value))
            index = match.end()
            continue

        match = _PUNCT.match(text, index)
        if match:
            tokens.append(Token(TokenKind.PUNCT, match.group()))
            index = match.end()
            continue

        index += 1  # Unrecognised: skip it.

    return tokens


@dataclass(slots=True)
class Stream:
    """A cursor over tokens, with the backtracking a descent parser needs.

    ``mark``/``reset`` rather than a parser-combinator library: a grammar rule
    that fails must leave the cursor exactly where it found it, and two
    integers make that obvious at every call site.
    """

    tokens: list[Token]
    pos: int = 0
    #: The furthest position any attempt reached, across all the backtracking.
    #: Useful for knowing how close the grammar got; misleading as a failure
    #: point, because a deep probe that failed still moves it.
    furthest: int = 0
    #: Where the parse actually gave up: the position at which no clause could
    #: match. This is the one the report wants. The high-water mark drifts to
    #: the end of the sentence as the grammar improves - a probe reaches the
    #: last token, fails, and every report then says "failed at '.'", which
    #: names nothing at all.
    blocked: int = 0

    @classmethod
    def of(cls, text: str) -> Stream:
        return cls(tokenize(text))

    def __bool__(self) -> bool:
        return self.pos < len(self.tokens)

    @property
    def done(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self, ahead: int = 0) -> Token:
        index = self.pos + ahead
        return self.tokens[index] if index < len(self.tokens) else END

    def next(self) -> Token:
        token = self.peek()
        self.pos += 1
        if self.pos > self.furthest:
            self.furthest = self.pos
        return token

    def mark(self) -> int:
        return self.pos

    def reset(self, mark: int) -> None:
        self.pos = mark

    # -- matching helpers ---------------------------------------------------

    def at(self, *words: str) -> bool:
        """Whether the next token is one of these words, case-insensitively."""
        return self.peek().lower in {w.lower() for w in words}

    def accept(self, *words: str) -> bool:
        """Consume the next token if it is one of these words."""
        if self.at(*words):
            self.next()
            return True
        return False

    def accept_phrase(self, phrase: str) -> bool:
        """Consume a multi-word phrase, or nothing at all.

        All-or-nothing: a phrase that matches three of its four words leaves
        the cursor untouched, because a half-consumed phrase is how a parser
        ends up confidently wrong.
        """
        words = phrase.split()
        mark = self.mark()
        for word in words:
            if not self.accept(word):
                self.reset(mark)
                return False
        return True

    def at_phrase(self, phrase: str) -> bool:
        mark = self.mark()
        found = self.accept_phrase(phrase)
        self.reset(mark)
        return found

    def accept_kind(self, kind: TokenKind) -> Token | None:
        if self.peek().kind is kind:
            return self.next()
        return None

    def accept_number(self) -> int | None:
        """A count, in digits or words. ``None`` if the next token is neither."""
        token = self.peek()
        if token.kind is TokenKind.NUMBER:
            self.next()
            return token.value
        return None

    def skip_punct(self, *marks: str) -> None:
        while self.peek().kind is TokenKind.PUNCT and (
            not marks or self.peek().text in marks
        ):
            self.next()

    def remaining(self) -> tuple[str, ...]:
        return tuple(token.text for token in self.tokens[self.pos :])

    def unreached(self) -> tuple[str, ...]:
        """What was left where the parse gave up."""
        return tuple(token.text for token in self.tokens[self.blocked :])

    def text(self) -> str:
        return " ".join(token.text for token in self.tokens)

    def __str__(self) -> str:
        consumed = " ".join(t.text for t in self.tokens[: self.pos])
        rest = " ".join(t.text for t in self.tokens[self.pos :])
        return f"{consumed} | {rest}"
