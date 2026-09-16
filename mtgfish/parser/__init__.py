"""Oracle text -> Effect IR.

The parser is deliberately subordinate to the rules engine. It does not decide
what happens; it decides which of the effects the engine *already implements*
a card is asking for. Everything it can emit is an ``EffectKind`` with an
executor and a test behind it, so the worst a mis-parse can do is the wrong
legal thing - never an illegal one.

Two rules govern the whole design, and previous attempts at this project died
by breaking them:

**Full consumption.** If any token of an ability is left unconsumed, the entire
ability is ``UNPARSED`` and never fires. There is no partial credit and no
fuzzy fallback. An ability that is 90% understood is 100% dangerous, because
the missing 10% is exactly the clause that made the card worth playing.

**No invention.** The parser may only produce opcodes the engine executes. A
grammar rule that wants an effect the engine lacks is a bug in the grammar, not
a licence to approximate.

The pipeline:

    oracle_text
      -> normalize   strip reminder text, fix dashes, resolve self-reference
      -> split       one ability per line, with modal blocks kept together
      -> classify    keyword / activated / triggered / static / spell
      -> tokenize    a flat token stream
      -> parse       recursive descent over registered clause patterns
      -> Ability     with Effect trees the engine can run

Coverage is measured over the real card pool, never self-reported.
"""

from __future__ import annotations

from .compile import ParsedCard, parse_card, parse_face
from .coverage import CoverageReport, measure
from .errors import ParseFailure

__all__ = [
    "CoverageReport",
    "ParseFailure",
    "ParsedCard",
    "measure",
    "parse_card",
    "parse_face",
]
