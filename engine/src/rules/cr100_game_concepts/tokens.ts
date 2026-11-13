/**
 * Tokens (CR 111) and emblems (CR 114). Ported from
 * mtgfish/rules/cr100_game_concepts/cr111_tokens.py.
 *
 * Both exist without a card behind them, so each gets a synthetic card: the
 * shape layers' `printedCharacteristics` reads (faces, plus the abilities it
 * carries instead of asking the IR).
 *
 * CR 111.4: a token's name defaults to its subtypes plus "Token" ("Soldier
 * Token"), which matters the moment anything counts tokens by name.
 * CR 111.5: a token that a prohibition stops entering is never created -
 * not created and then removed.
 */

import type { Ability, ManaCost, TokenSpec } from "../../ir";
import type { FaceDef } from "../../kernel/card";
import { CardType, Color, Layout, Supertype, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { createObject, invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { ObjectKind, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { flag, ir, list, num, str, strs } from "../ir_read";
import { Act, prohibited } from "../cr500_turn_structure/restrictions";
import { replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";
import { removeFromZone } from "../zones";

const STATIC: number = ir("AbilityKind.STATIC");
const ZERO_COST: ManaCost = { $: "ManaCost", symbols: [] };

/** A stand-in card for an object that has no card (Python `SyntheticCard`). */
export interface SyntheticCard {
  readonly oracle_id: string;
  readonly name: string;
  readonly layout: Layout;
  readonly faces: readonly FaceDef[];
  readonly abilities: readonly Ability[];
  /** Each face's own abilities, for a double-faced token; empty means `abilities` serves every face. */
  readonly face_abilities: readonly (readonly Ability[])[];
  readonly mana_value: number;
  readonly color_identity: Color;
  readonly commander_legal: boolean;
}

/** CR 111.4: subtypes plus "Token", unless the effect named it. */
export function tokenName(spec: TokenSpec): string {
  const name = str(spec.name);
  if (name) return name;
  const subtypes = strs(spec.subtypes);
  return subtypes.length ? `${subtypes.join(" ")} Token` : "Token";
}

/** One face of a token, and the abilities that face has. */
function tokenFace(game: Game, spec: TokenSpec, source: ObjectId): [FaceDef, Ability[]] {
  const types = spec.types === undefined ? CardType.CREATURE : num(spec.types);
  const creature = (types & CardType.CREATURE) !== 0;
  const value = (v: TokenSpec["power"]) => matchHooks().evaluate(game, v ?? { $: "Value" }, { source });
  const keywords = strs(spec.keywords);
  const abilities: Ability[] = [
    ...list(spec.abilities),
    ...keywords.map((word): Ability => ({ $: "Ability", kind: STATIC, keyword: word, text: word })),
  ];
  const face: FaceDef = {
    name: tokenName(spec),
    mana_cost: ZERO_COST,
    has_mana_cost: false,
    type_line: { supertypes: Supertype.NONE, types, subtypes: strs(spec.subtypes) },
    oracle_text: "",
    power: creature ? String(value(spec.power)) : null,
    toughness: creature ? String(value(spec.toughness)) : null,
    loyalty: null,
    defense: null,
    colors: num(spec.colors),
    color_indicator: null,
    keywords,
    produced_mana: [],
  };
  return [face, abilities];
}

function syntheticCard(name: string, faces: FaceDef[], fields: Partial<SyntheticCard> = {}): SyntheticCard {
  return {
    oracle_id: "",
    name,
    layout: Layout.NORMAL,
    faces,
    abilities: [],
    face_abilities: [],
    mana_value: 0,
    color_identity: Color.NONE,
    commander_legal: false,
    ...fields,
  };
}

/** Turn a TokenSpec into something the characteristics layer can read. */
export function buildTokenCard(game: Game, spec: TokenSpec, source: ObjectId = NO_OBJECT): SyntheticCard {
  const [face, abilities] = tokenFace(game, spec, source);
  if (spec.back === undefined || spec.back === null) return syntheticCard(face.name, [face], { abilities });
  // CR 111.10i, 712.8: a double-faced token has its front face's
  // characteristics until it transforms, and its back face's after.
  const [back, backAbilities] = tokenFace(game, spec.back, source);
  return syntheticCard(face.name, [face, back], {
    abilities,
    layout: Layout.DOUBLE_FACED_TOKEN,
    face_abilities: [abilities, backAbilities],
  });
}

/**
 * How many tokens are actually created, after replacement (CR 614.1c):
 * Doubling Season and friends replace the token-creation event, so the
 * count goes past the replacement system before any token exists. Two
 * doublers give four times as many (CR 614.5). Null (prevented) is zero.
 */
function replacedCount(game: Game, controller: PlayerId, count: number, source: ObjectId, spec: TokenSpec): number {
  const prospective = replacementHooks().replace(
    game,
    // `data` carries what the tokens would be, so "if one or more creature
    // tokens would be created" can tell a Treasure from a Soldier.
    event(EventKind.TOKEN_CREATED, { player: controller, source, amount: count, data: [spec] }),
  );
  return prospective === null ? 0 : prospective.amount;
}

/**
 * Create tokens on the battlefield (CR 111.1); the tokens actually created,
 * which may be fewer than asked for (CR 111.5, 101.3). `card` is what a
 * token copy is built from (CR 707.2), in place of the spec's own.
 */
export function createTokens(
  game: Game,
  spec: TokenSpec,
  controller: PlayerId,
  count = 1,
  options: { source?: ObjectId; card?: unknown } = {},
): GameObject[] {
  const source = options.source ?? NO_OBJECT;
  if (count <= 0) return [];
  // CR 800.4b: no token is created under a player who has left the game.
  if (player(game, controller).hasLost) return [];
  // Replacements apply to the number asked for, before any token exists.
  count = replacedCount(game, controller, count, source, spec);
  if (count <= 0) return [];

  const card = options.card ?? buildTokenCard(game, spec, source);
  const name = (card as { name?: string }).name ?? "Token";
  const created: GameObject[] = [];
  for (let i = 0; i < count; i++) {
    const obj = createObject(game, card, controller, Zone.BATTLEFIELD, { kind: ObjectKind.TOKEN });
    obj.tapped = flag(spec.enters_tapped);
    obj.enteredBattlefieldTurn = game.turn;
    obj.summoningSick = true;
    invalidateAllCharacteristics(game);

    // CR 111.5: checked after building it, since the prohibition is about
    // the token's characteristics.
    if (prohibited(game, Act.ENTER_BATTLEFIELD, { obj }) !== null) {
      logRecord(game, `${name} is not created (it can't enter the battlefield)`, { kind: "restriction" });
      removeFromZone(game, obj);
      game.objects.delete(obj.id);
      invalidateAllCharacteristics(game);
      continue;
    }

    created.push(obj);
    // PORT: CR 508.4, `enters_attacking` - Python's cr506_combat.enter_attacking
    // comes with the rules session's combat port. Until then the token
    // enters but isn't attacking.
    emit(game, event(EventKind.TOKEN_CREATED, { objectId: obj.id, player: controller, source }));
    emit(game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: obj.id, player: controller }));
  }
  return created;
}

/**
 * Put an emblem into the command zone (CR 114.2). It has no
 * characteristics beyond its abilities (CR 114.3), which function from the
 * command zone (CR 114.4).
 */
export function createEmblem(
  game: Game,
  controller: PlayerId,
  abilities: readonly Ability[],
  options: { text?: string } = {},
): GameObject {
  const inCommandZone = abilities.map((a): Ability => {
    const out: Ability = { $: "Ability", kind: a.kind ?? 0, functions_in: { $set: [Zone.COMMAND] } } as Ability;
    if (a.effects !== undefined) out.effects = a.effects;
    if (a.text !== undefined) out.text = a.text;
    if (a.cost !== undefined) out.cost = a.cost;
    if (a.trigger !== undefined) out.trigger = a.trigger;
    if (a.static_condition !== undefined) out.static_condition = a.static_condition;
    if (a.keyword !== undefined) out.keyword = a.keyword;
    return out;
  });
  const face: FaceDef = {
    name: "",
    mana_cost: ZERO_COST,
    has_mana_cost: false,
    type_line: { supertypes: Supertype.NONE, types: CardType.EMBLEM, subtypes: [] },
    oracle_text: options.text ?? "",
    power: null,
    toughness: null,
    loyalty: null,
    defense: null,
    colors: Color.NONE,
    color_indicator: null,
    keywords: [],
    produced_mana: [],
  };
  const card = syntheticCard("Emblem", [face], { abilities: inCommandZone });
  const obj = createObject(game, card, controller, Zone.COMMAND, { kind: ObjectKind.EMBLEM });
  invalidateAllCharacteristics(game);
  logRecord(game, `${player(game, controller).name} gets an emblem`, { kind: "emblem" });
  return obj;
}
