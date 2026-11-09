"""The cut lab finds the cards that cost games, without playing real games.

Real games take seconds each, and what is under test here is the bookkeeping
around them: which decks get built, how cards are scored, and which ones the
two phases keep. So a fake runner stands in for the engine. It "draws" a
random part of each deck and wins more often when a planted good card was
drawn and less often when a planted bad one was - a deck whose right answer is
known in advance.
"""

from __future__ import annotations

import collections
import random

import pytest

from mtgfish.sim import cutlab
from mtgfish.sim.records import GameRecord
from mtgfish.sim.runner import RunResult

LANDS = ["Command Tower", "Exotic Orchard", "Reliquary Tower", "Path of Ancestry"]
FILLER = [
    "Grizzly Bears", "Hill Giant", "Gray Ogre", "Craw Wurm", "Balduvian Bears",
    "Ironroot Treefolk", "Trained Armodon", "Bog Imp", "Scathe Zombies",
    "Merfolk of the Pearl Trident", "Goblin Piker", "Pearled Unicorn",
]
GOOD = ["Sol Ring", "Arcane Signet"]
BAD = ["Cancel", "Divination"]


def fake_runner(good=GOOD, bad=BAD, draw_share=0.5):
    calls = []

    def run(configs, progress=None):
        out = []
        for config in configs:
            names = []
            for line in config.decklists[0].splitlines():
                count, _, name = line.partition(" ")
                if count.isdigit():
                    names += [name] * int(count)
            result = RunResult(config=config)
            for index in range(config.games):
                # The game's luck comes from its seed alone, as in the engine:
                # every variant shares it, which is what the lab relies on.
                luck = random.Random(config.seed_for(index)).random()
                draws = random.Random(f"{config.seed_for(index)}|{','.join(names)}")
                drawn = {name for name in names if draws.random() < draw_share}
                chance = 0.3 + 0.25 * len(drawn & set(good)) - 0.25 * len(drawn & set(bad))
                record = GameRecord(index=index, seed=config.seed_for(index), turns=10)
                record.winner = 0 if luck < chance else 1
                record.drawn = drawn
                result.records.append(record)
            out.append(result)
        calls.append(len(configs))
        if progress:
            progress(sum(c.games for c in configs))
        return out

    run.calls = calls
    return run


def deck_text(cards):
    return "\n".join(f"1 {name}" for name in cards)


@pytest.fixture
def db(card_db):
    return card_db


def test_simple_mode_fixes_lands_and_tests_the_rest(db):
    pool = cutlab.split_pool(deck_text(LANDS + FILLER + GOOD), db, deck_size=14)
    assert sum(pool.fixed.values()) == len(LANDS)
    assert set(pool.unsure) == set(FILLER + GOOD)
    assert pool.slots == 10
    assert pool.cuts == len(FILLER + GOOD) - 10


def test_advanced_mode_uses_the_boxes(db):
    pool = cutlab.split_pool(
        "",
        db,
        core_text=deck_text(LANDS[:2] + GOOD),
        unsure_text=deck_text(LANDS[2:] + FILLER[:3] + ["Sol Ring"]),
        deck_size=8,
    )
    # A land in the unsure box is tested; a card in both boxes stays core.
    assert "Reliquary Tower" in pool.unsure
    assert "Sol Ring" in pool.fixed and "Sol Ring" not in pool.unsure
    assert any("both boxes" in problem for problem in pool.problems)


def test_balanced_subsets_spread_cards_evenly():
    subsets = cutlab.balanced_subsets(10, 6, 20, random.Random(1))
    usage = collections.Counter(i for subset in subsets for i in subset)
    assert all(len(subset) == 6 for subset in subsets)
    assert max(usage.values()) - min(usage.values()) <= 1


def test_variant_plan_respects_the_budget():
    for games in (10, 100, 1000, 10000):
        count, per = cutlab.variant_plan(games)
        assert count * per <= games
        assert count <= cutlab.MAX_VARIANTS


def test_cut_lab_cuts_the_bad_cards_and_keeps_the_good(db):
    unsure = FILLER + GOOD + BAD
    pool = cutlab.split_pool(deck_text(LANDS + unsure), db, deck_size=len(LANDS) + 12)
    assert pool.cuts == 4

    result = cutlab.run_cut_lab(
        pool,
        opponents=("",) * 3,
        screen_games=3000,
        final_games=3000,
        seed=7,
        runner=fake_runner(),
    )

    cut = {v.name for v in result.cuts}
    assert len(cut) == 4
    assert set(BAD) <= cut
    assert not set(GOOD) & cut
    # Every unsure card has exactly one verdict.
    assert sorted(v.name for v in result.verdicts) == sorted(unsure)
    # The final deck is the right size and holds no cut card.
    final = [line for line in result.final_decklist.splitlines() if line[:1].isdigit()]
    assert sum(int(line.split(" ")[0]) for line in final) == pool.deck_size
    assert not any(line.split(" ", 1)[1] in cut for line in final)
    assert result.final_result is not None and result.final_result.records
    assert result.played <= 6000


def test_nothing_to_cut_is_said_not_guessed(db):
    pool = cutlab.split_pool(deck_text(LANDS + GOOD), db, deck_size=10)
    result = cutlab.run_cut_lab(
        pool, opponents=("",) * 3, screen_games=100, final_games=100,
        runner=fake_runner(),
    )
    assert not result.cuts
    assert any("nothing to cut" in problem for problem in result.problems)


def test_queue_test_puts_a_good_new_card_in(db):
    deck = deck_text(LANDS + FILLER + BAD)
    base, incoming = cutlab.queue_pool(
        deck, ["Sol Ring"], db, deck_size=len(LANDS) + len(FILLER) + len(BAD)
    )
    result = cutlab.run_queue_test(
        base,
        incoming,
        opponents=("",) * 3,
        screen_games=2000,
        final_games=3000,
        seed=3,
        runner=fake_runner(),
    )
    cut = {v.name for v in result.cuts}
    assert "Sol Ring" not in cut
    assert len(cut) == 1 and cut <= set(BAD)
    change = cutlab.paired_change(result.baseline_result, result.final_result)
    assert change is not None and change["delta"] > 0


def test_queue_test_trims_an_oversized_deck_to_size(db):
    deck = deck_text(LANDS + FILLER + BAD)
    size = len(LANDS) + len(FILLER) + len(BAD) - 2  # two over the limit
    base, incoming = cutlab.queue_pool(deck, ["Sol Ring"], db, deck_size=size)
    result = cutlab.run_queue_test(
        base, incoming, opponents=("",) * 3, screen_games=1000, final_games=2000,
        seed=5, runner=fake_runner(),
    )
    final = [line for line in result.final_decklist.splitlines() if line[:1].isdigit()]
    assert sum(int(line.split(" ")[0]) for line in final) == size
    assert len(result.cuts) == 3


def test_queue_test_rejects_cards_already_in_the_deck(db):
    base, incoming = cutlab.queue_pool(deck_text(LANDS + GOOD), ["Sol Ring"], db)
    assert not incoming
    result = cutlab.run_queue_test(
        base, incoming,
        opponents=("",) * 3, screen_games=50, final_games=50,
        runner=fake_runner(),
    )
    assert any("already in the deck" in problem for problem in result.problems)
    assert result.final_result is None
