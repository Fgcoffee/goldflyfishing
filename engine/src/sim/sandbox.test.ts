// The TS sandbox: a hand-driven board on real cards' IR, in Python Sandbox's
// JSON, and rebuilt exactly from its session.
import { describe, expect, it } from "vitest";
import type { CardDocument } from "../ir";
import { Sandbox, STRICT_BENCH } from "./sandbox";

const FILES = import.meta.glob("../../../schema/samples/*.json", { eager: true, import: "default" });
const byName = new Map<string, CardDocument>();
for (const [path, doc] of Object.entries(FILES)) {
  if (!path.endsWith("decks.json")) byName.set((doc as CardDocument).name, doc as CardDocument);
}
function card(name: string): CardDocument {
  const doc = byName.get(name);
  if (doc === undefined) throw new Error(`no sample ${name}`);
  return doc;
}

type State = {
  players: { life: number; mana: number; battlefield: { name: string; tapped: boolean }[]; hand: { name: string }[]; graveyard: { name: string }[] }[];
  stack: { name: string }[];
  phase: string;
  step: string;
  error: string;
  message: string;
  log_index: number;
  rules: Record<string, boolean>;
};
type Legal = { index: number; kind: string; name: string; description: string };
type Group = { candidates: { id: number; name: string; is_player: boolean }[] };

/** A sandbox with Mountain on the battlefield and Lightning Bolt in hand. */
function boltBoard(): Sandbox {
  const sandbox = new Sandbox();
  for (const name of ["Mountain", "Lightning Bolt"]) sandbox.learn(card(name));
  sandbox.put(card("Mountain").oracle_id, "battlefield");
  sandbox.put(card("Lightning Bolt").oracle_id, "hand");
  return sandbox;
}

function boltTheOpponent(sandbox: Sandbox): State {
  const cast = (sandbox.legal(0) as Legal[]).find((a) => a.kind === "CAST_SPELL" && a.name === "Lightning Bolt");
  if (cast === undefined) throw new Error("Lightning Bolt isn't castable");
  const [group] = sandbox.targetsFor(cast.index, 0) as Group[];
  const opponent = group?.candidates.find((c) => c.is_player && c.name.startsWith("Opponent"));
  if (opponent === undefined) throw new Error("the opponent isn't a target");
  sandbox.perform(cast.index, 0, [[opponent.id]]);
  return sandbox.resolveTop() as State;
}

describe("the TS sandbox", () => {
  it("starts with two empty boards at 40 life, in the first main phase", () => {
    const state = new Sandbox().state() as State;
    expect(state.players.map((p) => p.life)).toEqual([40, 40]);
    expect([state.phase, state.step]).toEqual(["PRECOMBAT_MAIN", "MAIN"]);
    expect(state.rules.players_cannot_lose).toBe(true);
  });

  it("puts cards and plays them through the engine, with chosen targets", () => {
    const sandbox = boltBoard();
    const state = sandbox.state() as State;
    expect(state.players[0]?.battlefield.map((o) => o.name)).toEqual(["Mountain"]);
    expect(state.players[0]?.hand.map((o) => o.name)).toEqual(["Lightning Bolt"]);
    const after = boltTheOpponent(sandbox);
    expect(after.players[1]?.life).toBe(37);
    expect(after.players[0]?.graveyard.map((o) => o.name)).toEqual(["Lightning Bolt"]);
    expect(after.players[0]?.battlefield[0]?.tapped).toBe(true);
  });

  it("refuses what it doesn't know, and says why", () => {
    const sandbox = new Sandbox();
    expect((sandbox.put("no-such-card") as { error: string }).error).toContain("doesn't have card");
    sandbox.learn(card("Mountain"));
    expect((sandbox.put(card("Mountain").oracle_id, "sideboard") as { error: string }).error).toContain("cannot place");
    expect((sandbox.perform(99) as State).error).toBe("no such action");
  });

  it("rebuilds the exact position from its session", () => {
    const sandbox = boltBoard();
    boltTheOpponent(sandbox);
    sandbox.giveMana(2);
    sandbox.advance();
    const session = JSON.parse(JSON.stringify(sandbox.session()));
    const again = Sandbox.replay(session).state() as State;
    const original = sandbox.state() as State;
    expect(again.players).toEqual(original.players);
    expect([again.phase, again.step, again.log_index]).toEqual([original.phase, original.step, original.log_index]);
    expect(session.ops.map((o: { op: string }) => o.op)).toEqual(["put", "put", "perform", "resolve", "give_mana", "advance"]);
  });

  it("keeps mana across steps on the bench, and empties it under strict rules", () => {
    const bench = new Sandbox();
    bench.giveMana(1);
    expect((bench.advance() as State).players[0]?.mana).toBe(6);
    const strict = new Sandbox({ rules: STRICT_BENCH });
    strict.giveMana(1);
    expect((strict.advance() as State).players[0]?.mana).toBe(0);
  });

  it("switches a rule back on, by name, and refuses an unknown one", () => {
    const sandbox = new Sandbox();
    const on = sandbox.setRule("mana_pools_persist", false) as State;
    expect(on.rules.mana_pools_persist).toBe(false);
    expect(on.message).toContain("mana pools persist is back on");
    expect((sandbox.setRule("no_such_rule", true) as State).error).toContain("no such bench rule");
  });

  it("advances step by step into combat", () => {
    const sandbox = new Sandbox();
    const state = sandbox.advance() as State;
    expect(state.phase).toBe("COMBAT");
  });
});
