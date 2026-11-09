"""Sandbox verdicts that belong to one person, not to the whole server.

The desktop app keeps verdicts in one file in the user's home folder - right
for one person on one machine. On the web that file would be shared by
everyone, so one person's "switch this card off" would change every other
person's sandbox. Here each browser gets its own store instead:

* signed in: ``DbVerdictStore``, kept in the ``user_verdicts`` table;
* anonymous: ``MemoryVerdictStore``, kept for the session and then forgotten.

Both are the parser's own ``VerdictStore`` with only where it reads and
writes changed, so every rule about verdicts stays in one place.
"""

from __future__ import annotations

import json

from ...parser.verdicts import Review, Verdict, VerdictStore
from ..models import UserVerdicts, now


class MemoryVerdictStore(VerdictStore):
    def __init__(self) -> None:
        super().__init__(path=None)

    def _load(self) -> None:
        return

    def _save(self) -> None:
        return


class DbVerdictStore(VerdictStore):
    def __init__(self, database, user_id: str) -> None:
        self._database = database
        self._user_id = user_id
        super().__init__(path=None)

    def _load(self) -> None:
        with self._database.session() as db:
            row = db.get(UserVerdicts, self._user_id)
            raw = json.loads(row.data) if row and row.data else {}
        for name, entry in (raw.get("reviews") or {}).items():
            try:
                verdict = Verdict(entry.get("verdict", "unreviewed"))
            except ValueError:
                continue
            self._reviews[name] = Review(
                card=name,
                verdict=verdict,
                fingerprint=entry.get("fingerprint", ""),
                note=entry.get("note", ""),
            )

    def _save(self) -> None:
        payload = json.dumps(
            {
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
        )
        with self._database.session() as db:
            row = db.get(UserVerdicts, self._user_id)
            if row is None:
                db.add(UserVerdicts(user_id=self._user_id, data=payload, updated_at=now()))
            else:
                row.data = payload
                row.updated_at = now()
            db.commit()


def use_verdicts(bridge, store: VerdictStore) -> None:
    """Point a bridge's sandbox (and its ability reader) at ``store``.

    Call on the bridge's own thread: resetting the sandbox builds a game.
    """
    from ...parser.compile import OracleAbilities

    sandbox = bridge.sandbox
    sandbox.verdicts = store
    # A fresh reader: the old one has abilities cached under the old verdicts.
    sandbox.provider = OracleAbilities(verdicts=store)
    sandbox.reset()
