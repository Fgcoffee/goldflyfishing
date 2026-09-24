"""The Python side of the QWebChannel bridge.

Every method here is called from JavaScript and returns a JSON string. Slots
cannot return arbitrary Python objects across the channel, and encoding once on
this side is simpler than teaching the JS to unpack Qt's variant types.

The bridge holds no state of its own beyond a sandbox and the last run. It does
nothing a caller could not do from Python, which is what keeps the UI layer
thin enough to trust without driving a window to test it.
"""

from __future__ import annotations

import json
import threading
import traceback
from dataclasses import asdict
from pathlib import Path

from ..data.db import CardDatabase
from ..sim import RunConfig, replay_game, run, summarize
from ..sim.replay import DETAIL_LEVELS, KEY_KINDS, NOISE_KINDS
from ..sim.stats import render

# Not PySide6 directly: the web server runs this same bridge without Qt, and
# qtcompat supplies stand-ins there. The desktop app still gets the real ones.
from .qtcompat import QObject, Signal, Slot
from .sandbox import Sandbox


def _json(payload) -> str:
    return json.dumps(payload, default=str)


#: Bit flags for a permanent on the wire. One board is sent per scrub, so the
#: shape matters less than it would for the whole film - but a board of eighty
#: permanents written as objects with eleven keys each is twenty times the size
#: of the same board written as short arrays, for no more information.
TAPPED, SICK, TOKEN, COMMANDER, FACE_DOWN, PHASED_OUT, UNREADABLE = (
    1, 2, 4, 8, 16, 32, 64,
)


def _permanent_payload(card) -> list:
    """One permanent as ``[id, card, flags, power, toughness, damage, extra]``.

    ``extra`` is left off entirely unless the permanent has counters, is
    attacking, or is blocking - which most of them, most of the time, are not.
    """
    flags = (
        (TAPPED if card.tapped else 0)
        | (SICK if card.sick else 0)
        | (TOKEN if card.token else 0)
        | (COMMANDER if card.commander else 0)
        | (FACE_DOWN if card.face_down else 0)
        | (PHASED_OUT if card.phased_out else 0)
        | (UNREADABLE if card.unreadable else 0)
    )
    row = [card.id, card.card, flags, card.power, card.toughness, card.damage]
    extra = {}
    if card.counters:
        extra["c"] = card.counters
    if card.attacking >= 0:
        extra["a"] = card.attacking
    if card.attacking_permanent >= 0:
        extra["ap"] = card.attacking_permanent
    if card.blocking:
        extra["b"] = list(card.blocking)
    if card.attached_to >= 0:
        extra["at"] = card.attached_to
    if extra:
        row.append(extra)
    return row


def _board_payload(snapshot, index: int) -> dict:
    return {
        "index": index,
        "frame": snapshot.frame,
        "turn": snapshot.turn,
        "phase": snapshot.phase,
        "step": snapshot.step,
        "active": snapshot.active,
        "seats": [
            {
                "player": seat.player,
                "life": seat.life,
                "hand": seat.hand,
                "library": seat.library,
                "graveyard": seat.graveyard,
                "exile": seat.exile,
                "poison": seat.poison,
                "out": seat.out,
                "permanents": [_permanent_payload(c) for c in seat.permanents],
                # Left off entirely for a replay, where they are always empty.
                "hand_cards": [_permanent_payload(c) for c in seat.hand_cards],
                "graveyard_cards": [
                    _permanent_payload(c) for c in seat.graveyard_cards
                ],
            }
            for seat in snapshot.seats
        ],
        "stack": [
            {"card": item.card, "controller": item.controller, "text": item.text}
            for item in snapshot.stack
        ],
    }


def _guard(fn):
    """Turn an exception into something the page can show.

    An unhandled exception in a slot is swallowed by Qt and the UI simply
    stops responding, which is the worst failure mode there is to debug. This
    makes it visible instead.
    """

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the page needs to hear about it
            return _json(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=6),
                }
            )

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Bridge(QObject):
    """What the page can ask for."""

    progressed = Signal(int, int)
    #: Emitted when a run finishes, carrying the report as JSON. A run of
    #: 10,000 games takes minutes; doing it on the thread that draws the
    #: window means the window stops drawing, and Windows paints it grey and
    #: calls it unresponsive.
    run_finished = Signal(str)
    #: The swap lab's own completion signal. Separate from ``run_finished``
    #: because the payload is a table of variants rather than one report, and
    #: because the two screens must not overwrite each other's results.
    lab_finished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.db = CardDatabase()
        self.db.registry()
        self.sandbox = Sandbox(db=self.db)
        #: The thread a run happens on, so a second run cannot start on top
        #: of the first.
        self._worker: threading.Thread | None = None
        #: Set to stop the run on ``_worker``. A fresh one per run, so stopping
        #: one run can never pre-cancel the next.
        self._cancel = threading.Event()
        self._result = None
        self._config: RunConfig | None = None
        self._report = None
        #: The last lab result, kept so a variant's full report can be opened
        #: without running it again.
        self._lab = None
        #: The board film of the last replay. Kept here rather than sent to the
        #: page because the whole thing is tens of megabytes of JSON and the
        #: page only ever draws one board at a time - see ``replay_board``.
        self._film = None
        #: The Archidekt sign-in, if any: ``{"token", "username"}``. Kept here
        #: and never sent to the page, so a script on the page cannot read it.
        self._archidekt: dict | None = None

    # -- sandbox ------------------------------------------------------------

    @Slot(result=str)
    @_guard
    def sandbox_reset(self) -> str:
        return _json(self.sandbox.reset())

    @Slot(result=str)
    @_guard
    def sandbox_state(self) -> str:
        return _json(self.sandbox.state())

    @Slot(str, result=str)
    @_guard
    def sandbox_search(self, text: str) -> str:
        return _json(self.sandbox.search(text))

    @Slot(str, result=str)
    @_guard
    def sandbox_inspect(self, name: str) -> str:
        return _json(self.sandbox.inspect(name))

    @Slot(str, str, str, result=str)
    @_guard
    def sandbox_judge(self, name: str, verdict: str, note: str) -> str:
        """Record a human verdict on a card's parse.

        "inert" suppresses the card's abilities everywhere, not just in the
        sandbox display - that is what makes the review worth doing.
        """
        return _json(self.sandbox.judge(name, verdict, note))

    @Slot(result=str)
    @_guard
    def sandbox_review_summary(self) -> str:
        return _json(self.sandbox.review_summary())

    @Slot(str, str, int, int, result=str)
    @_guard
    def sandbox_put(self, name: str, zone: str, player: int, count: int = 1) -> str:
        return _json(self.sandbox.put(name, zone, player, count))

    @Slot(str, bool, result=str)
    @_guard
    def sandbox_set_rule(self, name: str, on: bool) -> str:
        """Switch one of the bench's suspended rules on or off.

        The sandbox does not enforce the rules that end a session - nobody
        loses, an empty library is harmless, mana keeps - because an
        instrument that kills you on the first draw step measures nothing.
        Each one can be put back, because the rule itself is sometimes what
        is being tested.
        """
        return _json(self.sandbox.set_rule(name, bool(on)))

    @Slot(str, result=str)
    @_guard
    def sandbox_set_rules(self, values: str) -> str:
        return _json(self.sandbox.set_rules(json.loads(values) if values else {}))

    @Slot(int, result=str)
    @_guard
    def sandbox_legal(self, player: int) -> str:
        return _json(self.sandbox.legal(player))

    @Slot(int, int, result=str)
    @_guard
    def sandbox_targets(self, index: int, player: int) -> str:
        return _json(self.sandbox.targets_for(index, player))

    @Slot(int, int, str, result=str)
    @_guard
    def sandbox_perform(self, index: int, player: int, targets: str) -> str:
        chosen = json.loads(targets) if targets else None
        return _json(self.sandbox.perform(index, player, chosen))

    @Slot(result=str)
    @_guard
    def sandbox_resolve(self) -> str:
        return _json(self.sandbox.resolve_top())

    @Slot(result=str)
    @_guard
    def sandbox_settle(self) -> str:
        return _json(self.sandbox.settle())

    @Slot(result=str)
    @_guard
    def sandbox_advance(self) -> str:
        return _json(self.sandbox.advance())

    @Slot(result=str)
    @_guard
    def sandbox_next_turn(self) -> str:
        return _json(self.sandbox.next_turn())

    @Slot(int, int, result=str)
    @_guard
    def sandbox_give_mana(self, amount: int, player: int) -> str:
        return _json(self.sandbox.give_mana(amount, player))

    @Slot(int, int, result=str)
    @_guard
    def sandbox_set_life(self, life: int, player: int) -> str:
        return _json(self.sandbox.set_life(life, player))

    @Slot(result=str)
    @_guard
    def sandbox_board(self) -> str:
        """The bench as a board, drawn by the replay's viewer.

        The same shape a replay board arrives in, so one renderer draws both -
        a sandbox that looked different from the replay would be a second thing
        to learn for no reason.

        Hands and graveyards are listed rather than counted, because the bench
        has one operator who put every one of those cards there themselves.
        """
        from ..sim.board import BoardRecorder

        recorder = BoardRecorder(open_zones=True)
        recorder.sample(self.sandbox.game)
        snapshot = recorder.film.snapshots[-1]
        return _json(
            {
                "cards": self._board_cards(recorder.film),
                "board": _board_payload(snapshot, 0),
            }
        )

    # -- decks and runs -----------------------------------------------------

    @Slot(str, result=str)
    @_guard
    def validate_deck(self, text: str) -> str:
        """Check a decklist and report what the parser makes of it.

        The inert-card list is the important half: a deck can be perfectly
        legal and still be measured as a deck with ten blanks in it.
        """
        from ..data.decks import parse_decklist
        from ..data.decks.archidekt import (
            ArchidektError,
            fetch_archidekt,
            is_archidekt_url,
        )
        from ..parser import parse_card

        if is_archidekt_url(text.strip()):
            try:
                deck = fetch_archidekt(text.strip(), self.db, token=self._archidekt_token())
            except ArchidektError as exc:
                return _json({"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                return _json({"error": f"could not reach Archidekt: {exc}"})
        else:
            deck = parse_decklist(text, self.db, name="deck")

        # Three states, not two. "Inert" for a card with *any* unread ability
        # overstates the damage badly: a creature whose body and keywords are
        # understood and whose one triggered ability is not still attacks,
        # blocks and dies correctly. Reporting it identically to a card that
        # does nothing at all makes the number useless for deciding whether a
        # run is trustworthy.
        blank = []  # nothing about this card is understood
        partial = []  # some abilities read, some not
        suppressed = []  # a person marked it inert on purpose
        distinct = 0
        abilities = 0
        understood = 0

        for entry in list(deck.entries) + list(deck.commanders):
            card = getattr(entry, "card", entry)
            if card is None:
                continue
            distinct += 1

            if self.sandbox.verdicts.is_suppressed(card.name):
                suppressed.append(card.name)
                continue

            parsed = parse_card(card)
            total = sum(face.total for face in parsed.faces)
            read = sum(face.understood for face in parsed.faces)
            abilities += total
            understood += read

            if total == 0:
                continue  # A vanilla creature or a basic land is not a gap.
            if read == 0:
                blank.append(card.name)
            elif read < total:
                partial.append(card.name)

        return _json(
            {
                "name": deck.name,
                "commanders": [c.name for c in deck.commanders],
                "cards": sum(e.quantity for e in deck.entries),
                "distinct": distinct,
                "unresolved": list(deck.unresolved),
                "issues": list(deck.issues),
                "blank": sorted(set(blank)),
                "partial": sorted(set(partial)),
                "suppressed": sorted(set(suppressed)),
                # The honest headline: how much of what this deck's cards say
                # the engine will actually do.
                "abilities": abilities,
                "abilities_understood": understood,
                # Kept so nothing downstream breaks; it is the union of the
                # two real categories.
                "inert": sorted(set(blank) | set(partial)),
            }
        )

    @Slot(str, int, int, result=str)
    @_guard
    def start_run(self, decklists_json: str, games: int, seed: int) -> str:
        """Begin a simulation on a worker thread and return immediately.

        The result arrives on ``run_finished``. Running it here would block
        the thread that draws the window for the whole run, which is exactly
        what "the window stops responding" is.
        """
        if self._worker is not None and self._worker.is_alive():
            return _json({"error": "a run is already in progress"})

        decklists = [self._resolve_decklist(text) for text in json.loads(decklists_json)]
        while len(decklists) < 4:
            decklists.append(decklists[0])

        config = RunConfig(
            decklists=tuple(decklists),
            games=games,
            run_seed=seed,
            card_db_hash=self.db.content_hash,
        )
        self._cancel = threading.Event()
        self._worker = threading.Thread(
            target=self._run_worker, args=(config, self._cancel), daemon=True
        )
        self._worker.start()
        return _json({"started": True, "games": games})

    @Slot(result=str)
    @_guard
    def cancel_run(self) -> str:
        """Stop the simulation or comparison in progress.

        Returns at once; the run's ``*_finished`` signal follows with
        ``{"cancelled": true}`` when its worker processes are gone.
        """
        if self._worker is None or not self._worker.is_alive():
            return _json({"cancelled": False, "reason": "nothing is running"})
        self._cancel.set()
        return _json({"cancelled": True})

    def shutdown(self, timeout: float = 15.0) -> None:
        """Stop any run and wait for it to let go of its processes.

        Not a slot: this is for whatever owns the bridge - the window as it
        closes, the web server as it drops a session or exits.
        """
        self._cancel.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)

    def _run_worker(self, config: RunConfig, cancel: threading.Event) -> None:
        """The simulation itself, off the UI thread.

        Qt signals are safe to emit from any thread - they are queued onto the
        receiver's thread - so progress and the final report cross back
        without any locking here.
        """
        from ..sim.runner import RunCancelled

        try:
            result = run(
                config,
                progress=lambda done, total: self.progressed.emit(done, total),
                cancel=cancel,
            )
            report = summarize(result)
            self._config = config
            self._result = result
            self._report = report
            self.run_finished.emit(_json(self._report_payload(report)))
        except RunCancelled as exc:
            self.run_finished.emit(_json({"cancelled": True, "message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            self.run_finished.emit(_json({"error": f"{type(exc).__name__}: {exc}"}))

    # -- the swap lab -------------------------------------------------------

    @Slot(str, result=str)
    @_guard
    def lab_cards(self, decklist: str) -> str:
        """Every card in a deck, for the picker.

        Commanders first, then the ninety-nine in the order the list gave
        them, because that is the order the person typing already knows.
        """
        from ..sim.lab import deck_cards

        return _json({"cards": deck_cards(self._resolve_decklist(decklist), self.db)})

    @Slot(str, str, bool, result=str)
    @_guard
    def lab_plan(self, decklist: str, slots_json: str, combine: bool) -> str:
        """What the plan would run, without running it.

        Shown before the button is pressed because the variant count is the
        one thing about this screen that can surprise somebody: combined mode
        multiplies, and four slots with three candidates each is
        eighty-one decks rather than the twelve it looks like.
        """
        from ..sim.lab import Slot as LabSlot
        from ..sim.lab import plan

        slots = [
            LabSlot(
                original=str(entry.get("original", "")),
                candidates=tuple(
                    str(name) for name in entry.get("candidates", []) if str(name).strip()
                ),
            )
            for entry in json.loads(slots_json)
            if str(entry.get("original", "")).strip()
        ]
        variants, problems = plan(
            self._resolve_decklist(decklist), slots, self.db, combine=bool(combine)
        )
        return _json(
            {
                "variants": [
                    {
                        "label": variant.label,
                        "swaps": [str(swap) for swap in variant.swaps],
                        "problems": list(variant.problems),
                    }
                    for variant in variants
                ],
                "problems": problems,
            }
        )

    @Slot(str, str, bool, int, int, result=str)
    @_guard
    def start_lab(
        self,
        decklists_json: str,
        slots_json: str,
        combine: bool,
        games: int,
        seed: int,
    ) -> str:
        """Run every variant of the plan, on a worker thread.

        The opponents are whatever the deck slots hold, held fixed across
        variants: a variant that faced different decks is not a comparison.
        """
        if self._worker is not None and self._worker.is_alive():
            return _json({"error": "a run is already in progress"})

        from ..sim.lab import Slot as LabSlot
        from ..sim.lab import plan

        decklists = [self._resolve_decklist(text) for text in json.loads(decklists_json)]
        while len(decklists) < 4:
            decklists.append(decklists[0])

        slots = [
            LabSlot(
                original=str(entry.get("original", "")),
                candidates=tuple(
                    str(name) for name in entry.get("candidates", []) if str(name).strip()
                ),
            )
            for entry in json.loads(slots_json)
            if str(entry.get("original", "")).strip()
        ]
        variants, problems = plan(decklists[0], slots, self.db, combine=bool(combine))
        if len(variants) < 2:
            return _json(
                {
                    "error": "nothing to compare - add a card and at least one "
                    "replacement",
                    "problems": problems,
                }
            )

        # The content hash is read here, on the UI thread that owns the
        # database connection, and handed to the worker as a string. Reading
        # it inside the worker is what broke this feature outright.
        self._cancel = threading.Event()
        self._worker = threading.Thread(
            target=self._lab_worker,
            args=(
                variants,
                tuple(decklists[1:]),
                games,
                seed,
                problems,
                self.db.content_hash,
                self._cancel,
            ),
            daemon=True,
        )
        self._worker.start()
        return _json({"started": True, "variants": len(variants), "games": games})

    def _lab_worker(
        self, variants, opponents, games, seed, problems, card_db_hash, cancel
    ) -> None:
        from ..sim.lab import comparison, run_lab
        from ..sim.runner import RunCancelled

        try:
            result = run_lab(
                variants,
                opponents=opponents,
                games=games,
                run_seed=seed,
                card_db_hash=card_db_hash,
                progress=lambda done, total: self.progressed.emit(done, total),
                cancel=cancel,
            )
            self._lab = result
            self.lab_finished.emit(
                _json(
                    {
                        "games": result.games,
                        "seed": result.run_seed,
                        "rows": comparison(result),
                        "problems": problems + result.problems,
                    }
                )
            )
        except RunCancelled as exc:
            self.lab_finished.emit(_json({"cancelled": True, "message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            self.lab_finished.emit(_json({"error": f"{type(exc).__name__}: {exc}"}))

    @Slot(int, result=str)
    @_guard
    def lab_report(self, index: int) -> str:
        """One variant's full report, for the ordinary Results screen.

        Kept rather than re-run: the run already happened and re-running it
        would take as long again, for numbers that are already sitting here.
        """
        if self._lab is None:
            return _json({"error": "no lab results yet"})
        if not 0 <= index < len(self._lab.variants):
            return _json({"error": "no such variant"})
        variant = self._lab.variants[index]
        if variant.report is None:
            return _json({"error": variant.error or "that variant did not finish"})
        self._report = variant.report
        # The config too, not just the report: a replay rebuilds the game from
        # it, so leaving the baseline's config in place would replay a game
        # from a different deck and present it as this variant's.
        self._config = variant.config
        self._result = None
        return _json(self._report_payload(variant.report))

    def _resolve_decklist(self, text: str) -> str:
        """Turn a deck *reference* into a decklist.

        The setup screen has always said "paste a decklist, or an Archidekt
        URL", and only the first half was implemented - a URL went to the
        decklist parser, which found no card names in it and reported an empty
        deck. Fetching happens here rather than in the browser because the
        page has no network access under the artifact CSP and no API key
        handling of its own.
        """
        from ..data.decks.archidekt import (
            ArchidektError,
            fetch_archidekt,
            is_archidekt_url,
        )

        stripped = text.strip()
        if not is_archidekt_url(stripped):
            return text
        try:
            deck = fetch_archidekt(stripped, self.db, token=self._archidekt_token())
        except ArchidektError:
            return text  # Reported by validate_deck, which shows the reason.
        return deck.as_decklist()

    # -- the deck tab -------------------------------------------------------

    @Slot(str, result=str)
    @_guard
    def deck_view(self, text: str) -> str:
        """Every card in a deck with its image, parse quality, curve and pips.

        Also returns the deck as plain text, so an Archidekt URL can be turned
        into an editable list the moment someone changes a card.
        """
        from ..data.decks import parse_decklist
        from ..data.decks.archidekt import ArchidektError, fetch_archidekt, is_archidekt_url
        from .deckview import deck_view

        stripped = text.strip()
        if is_archidekt_url(stripped):
            try:
                deck = fetch_archidekt(stripped, self.db, token=self._archidekt_token())
            except ArchidektError as exc:
                return _json({"error": str(exc)})
        else:
            deck = parse_decklist(text, self.db, name="Your deck")
        view = deck_view(deck, self.db, self.sandbox.verdicts)
        view["decklist"] = deck.as_decklist()
        return _json(view)

    @Slot(str, result=str)
    @_guard
    def card_search(self, text: str) -> str:
        """Type-ahead for adding a card: names containing the text, with art."""
        from .deckview import card_row

        out = []
        for name in self.db.search_names(text, limit=20):
            card = self.db.lookup(name)
            if card is None:
                continue
            row = card_row(card, self.db.raw_card(card.oracle_id), 1, self.sandbox.verdicts)
            out.append(row)
        return _json({"cards": out})

    # -- archidekt account --------------------------------------------------

    def _archidekt_token(self) -> str | None:
        return self._archidekt["token"] if self._archidekt else None

    @Slot(str, str, result=str)
    @_guard
    def archidekt_login(self, username: str, password: str) -> str:
        """Sign in to Archidekt. The password goes to Archidekt and nowhere else."""
        from ..data.decks.archidekt import ArchidektError, archidekt_login

        if not username.strip() or not password:
            return _json({"error": "enter your Archidekt username (or email) and password"})
        try:
            account = archidekt_login(username, password)
        except ArchidektError as exc:
            return _json({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return _json({"error": f"could not reach Archidekt: {exc}"})
        self._archidekt = {"token": account["token"], "username": account["username"]}
        return _json({"signed_in": True, "username": account["username"]})

    @Slot(result=str)
    @_guard
    def archidekt_logout(self) -> str:
        self._archidekt = None
        return _json({"signed_in": False})

    @Slot(result=str)
    @_guard
    def archidekt_account(self) -> str:
        if not self._archidekt:
            return _json({"signed_in": False})
        return _json({"signed_in": True, "username": self._archidekt["username"]})

    @Slot(str, int, result=str)
    @_guard
    def archidekt_decks(self, username: str, page: int) -> str:
        """A user's decks. Private ones are included only for your own account."""
        from ..data.decks.archidekt import ArchidektError, list_archidekt_decks

        mine = self._archidekt["username"] if self._archidekt else ""
        who = username.strip() or mine
        if not who:
            return _json({"error": "sign in, or enter an Archidekt username"})
        token = self._archidekt_token() if who.lower() == mine.lower() else None
        try:
            listing = list_archidekt_decks(who, token=token, page=max(1, int(page)))
        except ArchidektError as exc:
            return _json({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return _json({"error": f"could not reach Archidekt: {exc}"})
        listing["username"] = who
        return _json(listing)

    @Slot(str, result=str)
    @_guard
    def archidekt_import(self, deck_id: str) -> str:
        """One Archidekt deck as decklist text, ready for a deck slot."""
        from ..data.decks.archidekt import ArchidektError, fetch_archidekt

        try:
            deck = fetch_archidekt(str(deck_id), self.db, token=self._archidekt_token())
        except ArchidektError as exc:
            return _json({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return _json({"error": f"could not reach Archidekt: {exc}"})
        return _json(
            {
                "name": deck.name,
                "decklist": deck.as_decklist(),
                "commanders": [card.name for card in deck.commanders],
                "cards": deck.total_cards,
                "unresolved": list(deck.unresolved),
            }
        )

    @Slot(result=str)
    @_guard
    def report(self) -> str:
        if self._report is None:
            return _json({"error": "no run yet"})
        return _json(self._report_payload(self._report))

    @Slot(result=str)
    @_guard
    def report_text(self) -> str:
        if self._report is None:
            return _json({"text": ""})
        return _json({"text": render(self._report)})

    @Slot(int, result=str)
    @_guard
    def replay(self, index: int) -> str:
        """Rebuild one game from its seed, with the full log.

        Not a lookup: the game is played again. That is why a run stores no
        logs at all and why every datapoint can be clicked.
        """
        if self._config is None:
            return _json({"error": "no run yet"})
        view = replay_game(self._config, index)
        self._film = view.film
        return _json(
            {
                "game_index": view.game_index,
                "seed": view.seed,
                "turns": view.turns,
                "winner": view.winner,
                "outcome": view.outcome,
                "final_board": view.final_board,
                "detail_levels": list(DETAIL_LEVELS),
                "key_kinds": sorted(KEY_KINDS),
                "noise_kinds": sorted(NOISE_KINDS),
                "seats": [asdict(seat) for seat in view.seats],
                # The page's table of contents. Each entry is one player's
                # turn, numbered the way that player would number it, with the
                # frame range it covers - so "jump to their turn four" is a
                # slice rather than a search.
                "turns_taken": [
                    dict(asdict(turn), label=turn.label) for turn in view.turn_list
                ],
                "frames": [
                    {
                        "turn": f.turn,
                        "phase": f.phase,
                        "step": f.step,
                        "player": f.player,
                        "kind": f.kind,
                        "text": f.text,
                        "depth": f.depth,
                        "active_player": f.active_player,
                        "player_turn": f.player_turn,
                    }
                    for f in view.frames
                ],
                # Everything the board view needs *except* the boards: the
                # cards to draw, once, with their art and what the parser made
                # of them, and the log position each board belongs to. The
                # boards themselves are fetched one at a time; all of them at
                # once is forty megabytes of JSON for one game.
                "board_cards": self._board_cards(view.film),
                "board_frames": [s.frame for s in view.film.snapshots],
            }
        )

    @Slot(int, result=str)
    @_guard
    def replay_board(self, index: int) -> str:
        """One board from the last replay, by its position in the film.

        Kept server-side and asked for a board at a time. The page knows where
        every board sits in the log (``board_frames``), so scrubbing only asks
        when it crosses into a different one.
        """
        film = self._film
        if film is None or not film.snapshots:
            return _json({"error": "no replay yet"})
        index = max(0, min(int(index), len(film.snapshots) - 1))
        return _json(_board_payload(film.snapshots[index], index))

    def _board_cards(self, film) -> list[dict]:
        """Every distinct card in the replayed game, with art and parse status.

        Art is Scryfall's, by printing id, exactly as the Deck tab does it.

        Tokens are looked up by name like anything else, because Scryfall's
        dump carries token faces too - a board of eleven Treasures with the
        real Treasure art reads at a glance, and the same board drawn as
        eleven grey rectangles does not. A token whose name matches nothing
        falls back to the placeholder, which is the honest answer for a token
        the engine invented.
        """
        from .deckview import image_urls, parse_status

        out = []
        for ref in film.cards:
            card = self.db.lookup(ref.name)
            images = {"normal": "", "large": "", "art": "", "back": None}
            status = {"status": "ok", "unread": []}
            if card is not None:
                images = image_urls(card, self.db.raw_card(card.oracle_id))
                status = parse_status(card, self.sandbox.verdicts)
            out.append(
                {
                    "name": ref.name,
                    "type_line": ref.type_line,
                    "mana_cost": ref.mana_cost,
                    "token": ref.token,
                    "art": images["art"],
                    "image": images["normal"],
                    "status": status["status"],
                    "unread": status["unread"],
                }
            )
        return out

    @Slot(str, result=str)
    @_guard
    def read_file(self, path: str) -> str:
        return _json({"text": Path(path).read_text(encoding="utf8")})

    # -- shaping ------------------------------------------------------------

    def _report_payload(self, report) -> dict:
        return {
            "games": report.games,
            "seats": report.seats,
            "win_rate": report.win_rate,
            "stall_rate": report.stall_rate,
            "wins": report.wins,
            "stalls": report.stalls,
            "average_win_round": report.average_win_round,
            "average_win_turn": report.average_win_turn,
            "win_reasons": dict(report.win_reasons),
            "loss_reasons": dict(report.loss_reasons),
            "commander_landed_games": {
                str(turn): games[:20]
                for turn, games in report.commander_landed_games.items()
            },
            "commander_landed": {
                str(k): v for k, v in report.commander_landed.items()
            },
            "commander_never": report.commander_never,
            "by_win_round": {str(k): v for k, v in report.by_win_round.items()},
            # Games stopped by the action budget rather than played out, and
            # what was looping in them. Surfaced because a runaway is a bug in
            # a card, and folding it into the stall rate would present an
            # engine fault as a property of the deck.
            "runaways": dict(report.runaways),
            # Loops noticed and shortcut (CR 732.2a), and games drawn on a
            # mandatory one (CR 104.4b). Shown so an infinite that ended a
            # game is visible as one rather than folded into the win rate.
            "loops": dict(report.loops),
            "loop_draws": report.loop_draws,
            "unparsed_cards": report.unparsed_cards,
            "digest_mismatches": report.digest_mismatches,
            "cards": [
                {
                    "name": card.name,
                    "impact": card.impact,
                    "margin": card.margin,
                    "drawn": card.drawn,
                    "cast": card.cast,
                    "average_cast_turn": card.average_cast_turn,
                    "reliable": card.reliable,
                }
                for card in report.cards
            ],
            "removal": [
                {
                    "victim": target.victim,
                    "losses": target.losses,
                    "by_source": dict(target.by_source),
                    "by_method": dict(target.by_method),
                }
                for target in report.removal[:30]
            ],
            "series": [
                {
                    "metric": series.metric,
                    "player": series.player,
                    "turns": series.turns,
                    "values": series.values,
                    "samples": series.samples,
                    "examples": series.examples,
                    "representative": series.representative,
                    "representative_value": series.representative_value,
                }
                for series in report.series
            ],
        }
