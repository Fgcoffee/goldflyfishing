"""Human verdicts on what the parser made of a card.

The parser can prove a card was read *completely* and can be cross-checked
against somebody else's reading, but neither answers "is this parse actually
right?" for a specific card. Only a person who knows the card can answer that,
and this is where their answer is kept.

Three verdicts, and the asymmetry between them is the point:

``approved``  a person read the explanation and it matches the card. Recorded
              so the same card never has to be checked twice, and so a grammar
              change that alters an approved card can be flagged.
``inert``     a person read it and it is *wrong*. The abilities are suppressed
              exactly as if the grammar had failed on them. This is the one
              that matters: a card that parses into the wrong behaviour is far
              more damaging to a simulation than one that parses into nothing,
              because nothing is visibly missing.
``unreviewed`` nobody has looked.

An approval is pinned to a fingerprint of the parse, not just the card name. A
grammar change that alters how an approved card reads invalidates the approval
automatically - otherwise an approval granted in March silently vouches for
different behaviour in June, which is worse than never having asked.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Verdict(StrEnum):
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    INERT = "inert"


#: Where verdicts live by default: alongside the card database, not in the
#: package, because they are the user's judgements and survive a reinstall.
DEFAULT_PATH = Path.home() / ".mtgfish" / "verdicts.json"


@dataclass(frozen=True, slots=True)
class Review:
    card: str
    verdict: Verdict
    #: The parse this verdict was given for. An approval of a parse that has
    #: since changed is not an approval of the current one.
    fingerprint: str
    note: str = ""

    @property
    def is_inert(self) -> bool:
        return self.verdict is Verdict.INERT


def fingerprint(parsed) -> str:
    """A stable digest of what the parser currently makes of a card.

    Built from the rendered explanation rather than the raw dataclasses,
    because that is what the reviewer actually read and agreed to. Two parses
    that explain identically are the same parse for review purposes, even if
    an unrelated field moved.
    """
    from .explain import explain_card

    rows = explain_card(parsed)
    payload = "\n".join(f"{row['kind']}|{row['understood']}" for row in rows)
    return hashlib.sha256(payload.encode("utf8")).hexdigest()[:16]


class VerdictStore:
    """Verdicts on disk, keyed by card name.

    Deliberately a flat JSON file. It is small, a person may reasonably want
    to read or hand-edit it, and putting it in the card database would tie a
    user's judgements to a file that gets rebuilt whenever Scryfall updates.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._reviews: dict[str, Review] = {}
        self._load()

    # -- reading ------------------------------------------------------------

    def review_for(self, card_name: str) -> Review | None:
        return self._reviews.get(card_name)

    def status(self, card_name: str, parsed) -> tuple[Verdict, bool]:
        """This card's verdict, and whether it still applies to this parse.

        The second value is what stops a stale approval vouching for behaviour
        it never saw: it is False when the grammar has changed the card since
        the verdict was given.
        """
        review = self._reviews.get(card_name)
        if review is None:
            return Verdict.UNREVIEWED, True
        return review.verdict, review.fingerprint == fingerprint(parsed)

    def is_suppressed(self, card_name: str) -> bool:
        """Whether this card's abilities should be treated as unreadable.

        A stale *inert* verdict still suppresses. Someone judged this card's
        behaviour wrong; a grammar change might have fixed it, but assuming so
        without a second look is how a known-bad card gets back into a run.
        """
        review = self._reviews.get(card_name)
        return review is not None and review.is_inert

    def counts(self) -> dict[str, int]:
        tally = {verdict.value: 0 for verdict in Verdict}
        for review in self._reviews.values():
            tally[review.verdict.value] += 1
        return tally

    # -- writing ------------------------------------------------------------

    def record(
        self, card_name: str, verdict: Verdict, parsed, note: str = ""
    ) -> Review:
        review = Review(
            card=card_name,
            verdict=Verdict(verdict),
            fingerprint=fingerprint(parsed),
            note=note,
        )
        self._reviews[card_name] = review
        self._save()
        return review

    def clear(self, card_name: str) -> None:
        if self._reviews.pop(card_name, None) is not None:
            self._save()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt verdict file must not stop the program starting. The
            # cost is a lost set of judgements, which is recoverable; refusing
            # to launch is not.
            return
        for name, entry in raw.get("reviews", {}).items():
            self._reviews[name] = Review(
                card=name,
                verdict=Verdict(entry.get("verdict", "unreviewed")),
                fingerprint=entry.get("fingerprint", ""),
                note=entry.get("note", ""),
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "reviews": {
                name: {
                    "verdict": review.verdict.value,
                    "fingerprint": review.fingerprint,
                    "note": review.note,
                }
                for name, review in sorted(self._reviews.items())
            },
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf8")
