/**
 * Type lines: supertypes, card types and subtypes (CR 205). Ported from
 * mtgfish/rules/cr200_parts_of_a_card/cr205_typeline.py.
 *
 * Subtypes split by longest match against a registry of known subtypes, so
 * "Time Lord" stays one creature type while "Urza's Mine" is two land types.
 */

import {
  CARD_TYPE_NAMES,
  CARD_TYPE_ORDER,
  CardType,
  PERMANENT_TYPES,
  SUPERTYPE_DISPLAY_NAMES,
  SUPERTYPE_NAMES,
  SUPERTYPE_ORDER,
  Supertype,
  TYPE_DISPLAY_NAMES,
} from "../../kernel/enums";

const DASHES = ["—", "–", " - "] as const;

/** Which subtypes exist, and which card type each belongs to. Not game state. */
export class SubtypeRegistry {
  private readonly byType = new Map<CardType, Set<string>>();
  private readonly all = new Set<string>();
  private readonly multiword = new Set<string>();
  private maxWords = 1;

  register(cardType: CardType, subtypes: Iterable<string>): void {
    let names = this.byType.get(cardType);
    if (names === undefined) {
      names = new Set();
      this.byType.set(cardType, names);
    }
    for (const name of subtypes) {
      names.add(name);
      this.all.add(name);
      const words = name.split(" ").length;
      if (words > 1) {
        this.multiword.add(name);
        this.maxWords = Math.max(this.maxWords, words);
      }
    }
  }

  /** Every card type this subtype can appear on, as a flag. */
  typesFor(subtype: string): CardType {
    let result: CardType = CardType.NONE;
    for (const [cardType, names] of this.byType) if (names.has(subtype)) result |= cardType;
    return result;
  }

  isCreatureType(subtype: string): boolean {
    return this.byType.get(CardType.CREATURE)?.has(subtype) ?? false;
  }

  /** All creature types - what changeling grants (CR 702.73a). */
  creatureTypes(): ReadonlySet<string> {
    return this.byType.get(CardType.CREATURE) ?? new Set();
  }

  known(subtype: string): boolean {
    return this.all.has(subtype);
  }

  /**
   * Split a subtype string, longest match first. Unknown words are still
   * emitted individually, so a new set's subtype shows up instead of vanishing.
   */
  split(text: string): string[] {
    const words = text.split(/\s+/).filter((w) => w.length > 0);
    const out: string[] = [];
    let i = 0;
    while (i < words.length) {
      let matched = false;
      const upper = Math.min(this.maxWords, words.length - i);
      for (let span = upper; span > 1; span -= 1) {
        const candidate = words.slice(i, i + span).join(" ");
        if (this.multiword.has(candidate)) {
          out.push(candidate);
          i += span;
          matched = true;
          break;
        }
      }
      if (!matched) {
        out.push(words[i] as string);
        i += 1;
      }
    }
    return out;
  }
}

function defaultRegistry(): SubtypeRegistry {
  const reg = new SubtypeRegistry();
  reg.register(CardType.CREATURE, ["Time Lord"]);
  return reg;
}

let ACTIVE_REGISTRY = defaultRegistry();

/** Replace the registry once the card catalog is loaded. */
export function installRegistry(registry: SubtypeRegistry): void {
  ACTIVE_REGISTRY = registry;
}

export function activeRegistry(): SubtypeRegistry {
  return ACTIVE_REGISTRY;
}

export class TypeLineError extends Error {}

/** A parsed type line (CR 205.1). Immutable: layer 4 builds new ones. */
export interface TypeLine {
  readonly supertypes: Supertype;
  readonly types: CardType;
  readonly subtypes: readonly string[];
}

export const EMPTY_TYPE_LINE: TypeLine = Object.freeze({
  supertypes: Supertype.NONE,
  types: CardType.NONE,
  subtypes: Object.freeze([]) as readonly string[],
});

export function typeLine(
  types: CardType,
  options: { supertypes?: Supertype; subtypes?: readonly string[] } = {},
): TypeLine {
  return { supertypes: options.supertypes ?? Supertype.NONE, types, subtypes: options.subtypes ?? [] };
}

/** Parse a type line, also returning unrecognised type words. */
export function scanTypeLine(
  text: string,
  registry: SubtypeRegistry = ACTIVE_REGISTRY,
): { line: TypeLine; unknown: string[] } {
  const trimmed = text.trim();
  let left = trimmed;
  let right = "";
  for (const dash of DASHES) {
    const at = trimmed.indexOf(dash);
    if (at >= 0) {
      left = trimmed.slice(0, at);
      right = trimmed.slice(at + dash.length);
      break;
    }
  }
  let supertypes: Supertype = Supertype.NONE;
  let types: CardType = CardType.NONE;
  const unknown: string[] = [];
  for (const word of left.split(/\s+/).filter((w) => w.length > 0)) {
    const key = word.toLowerCase();
    const sup = SUPERTYPE_NAMES.get(key);
    const typ = CARD_TYPE_NAMES.get(key);
    if (sup !== undefined) supertypes |= sup;
    else if (typ !== undefined) types |= typ;
    else unknown.push(word);
  }
  const subtypes = right.trim() ? registry.split(right.trim()) : [];
  return { line: { supertypes, types, subtypes }, unknown };
}

/** Parse "Legendary Creature - Human Wizard". Strict: unknown words throw. */
export function parseTypeLine(
  text: string,
  options: { registry?: SubtypeRegistry; strict?: boolean } = {},
): TypeLine {
  const { line, unknown } = scanTypeLine(text, options.registry);
  if (unknown.length > 0 && (options.strict ?? true)) {
    throw new TypeLineError(
      `unknown type word(s) ${unknown.map((w) => JSON.stringify(w)).join(", ")} in type line ${JSON.stringify(text)}`,
    );
  }
  return line;
}

export function isPermanentType(line: TypeLine): boolean {
  return (line.types & PERMANENT_TYPES) !== 0;
}

/** Subtypes that are creature types (CR 205.3k). */
export function creatureTypesOf(line: TypeLine, registry: SubtypeRegistry = ACTIVE_REGISTRY): string[] {
  return line.subtypes.filter((s) => registry.isCreatureType(s));
}

export function withTypes(line: TypeLine, types: CardType): TypeLine {
  return { supertypes: line.supertypes, types, subtypes: line.subtypes };
}

/** Add types, as a layer-4 effect does (CR 613.1d, 205.1b). */
export function addingTypes(
  line: TypeLine,
  add: { supertypes?: Supertype; types?: CardType; subtypes?: readonly string[] },
): TypeLine {
  const merged = [...line.subtypes, ...(add.subtypes ?? []).filter((s) => !line.subtypes.includes(s))];
  return {
    supertypes: line.supertypes | (add.supertypes ?? 0),
    types: line.types | (add.types ?? 0),
    subtypes: merged,
  };
}

export function removingTypes(
  line: TypeLine,
  drop: { supertypes?: Supertype; types?: CardType; subtypes?: readonly string[] },
): TypeLine {
  const gone = new Set(drop.subtypes ?? []);
  return {
    supertypes: line.supertypes & ~(drop.supertypes ?? 0),
    types: line.types & ~(drop.types ?? 0),
    subtypes: line.subtypes.filter((s) => !gone.has(s)),
  };
}

export function typeLineText(line: TypeLine): string {
  const parts = [
    ...SUPERTYPE_ORDER.filter((s) => line.supertypes & s).map((s) => SUPERTYPE_DISPLAY_NAMES.get(s) ?? ""),
    ...CARD_TYPE_ORDER.filter((t) => line.types & t).map((t) => TYPE_DISPLAY_NAMES.get(t) ?? ""),
  ];
  const head = parts.join(" ");
  return line.subtypes.length > 0 ? `${head} — ${line.subtypes.join(" ")}` : head;
}
