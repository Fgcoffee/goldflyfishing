/**
 * A stand-in engine that plays no Magic. It exists so the worker protocol,
 * the pool and the page can be built and tested before the kernel can run a
 * game. It does what a real engine must do with randomness: everything comes
 * from the game's own seeded PRNG, so a seed always gives the same record.
 *
 * It shuffles each library, deals seven, "draws" a card a turn, picks a
 * winner and a turn at random, and digests what it did. Replace it with the
 * real engine; nothing else changes.
 */

import { newDigest, hexDigest, updateDigest } from "../kernel/digest";
import { newRng, randint, randrange, shuffle } from "../kernel/random";
import type { CardCast, GameRecord } from "./records";
import type { DeckSpec, GameEngine, RunSetup } from "./runner";

function library(deck: DeckSpec, setup: RunSetup): string[] {
  const names: string[] = [];
  for (const entry of deck.cards) {
    const name = setup.cards[entry.oracle_id]?.name;
    if (name === undefined) throw new Error(`no IR for card ${entry.oracle_id}`);
    for (let i = 0; i < entry.count; i += 1) names.push(name);
  }
  return names;
}

export const stubEngine: GameEngine = {
  name: "stub",
  play(setup: RunSetup, index: number, seed: number): GameRecord {
    if (setup.decks.length === 0) throw new Error("a game needs at least one deck");
    const rng = newRng(seed);
    const digest = newDigest();
    const seats = setup.decks.length;

    const libraries = setup.decks.map((deck) => library(deck, setup));
    libraries.forEach((cards, seat) => {
      shuffle(rng, cards);
      updateDigest(digest, `seat ${seat}: ${cards.join("|")}\n`);
    });

    const hero = libraries[0] ?? [];
    const rounds = randint(rng, 4, Math.max(4, setup.maxRounds));
    const stalled = rounds >= setup.maxRounds;
    const winner = stalled ? null : randrange(rng, seats);
    const openingHand = hero.slice(0, 7);
    const drawn = hero.slice(0, Math.min(hero.length, 7 + rounds));
    const cast: CardCast[] = drawn
      .filter(() => randrange(rng, 2) === 0)
      .map((name, i) => ({ name, round: Math.min(rounds, 1 + Math.floor(i / 2)) }));
    updateDigest(digest, `rounds ${rounds} winner ${winner}\n`);

    return {
      index,
      seed,
      turns: rounds * seats,
      rounds,
      winner,
      winReason: stalled ? "STALL_OUT" : "LIFE",
      eliminations: [],
      commanderLanded: setup.decks.map((deck) =>
        deck.commanders.length > 0 && randrange(rng, 4) > 0 ? randint(rng, 2, rounds) : null,
      ),
      mulligans: setup.decks.map(() => (randrange(rng, 5) === 0 ? 1 : 0)),
      openingHand,
      drawn,
      cast,
      turnsSeries: [],
      removal: [],
      runaway: "",
      loopDraw: false,
      digest: hexDigest(digest),
    };
  },
};
