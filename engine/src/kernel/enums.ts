/**
 * Core enumerations. Ported from mtgfish/rules/kernel/enums.py.
 *
 * Every Python IntEnum / IntFlag becomes a frozen object of numbers plus a
 * type of the same name, so values stay plain integers - cheap to compare and
 * store, and identical to the Python values the card IR is written in.
 * Flags combine with `|` and are tested with `&`.
 */

/** Python `Color` (IntFlag). */
export const Color = {
  NONE: 0,
  WHITE: 1,
  BLUE: 2,
  BLACK: 4,
  RED: 8,
  GREEN: 16,
} as const;
export type Color = number;

const Color_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Color).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Color value, as Python's `.name`. */
export function colorName(value: number): string {
  return Color_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `CardType` (IntFlag). */
export const CardType = {
  NONE: 0,
  ARTIFACT: 1,
  BATTLE: 2,
  CREATURE: 4,
  ENCHANTMENT: 8,
  LAND: 16,
  PLANESWALKER: 32,
  INSTANT: 64,
  SORCERY: 128,
  KINDRED: 256,
  CONSPIRACY: 512,
  DUNGEON: 1024,
  PHENOMENON: 2048,
  PLANE: 4096,
  SCHEME: 8192,
  VANGUARD: 16384,
  EMBLEM: 32768,
  STICKERS: 65536,
  HERO: 131072,
  EVENT: 262144,
  BOSS: 524288,
  CARD: 1048576,
} as const;
export type CardType = number;

const CardType_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(CardType).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single CardType value, as Python's `.name`. */
export function cardTypeName(value: number): string {
  return CardType_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Supertype` (IntFlag). */
export const Supertype = {
  NONE: 0,
  BASIC: 1,
  LEGENDARY: 2,
  ONGOING: 4,
  SNOW: 8,
  WORLD: 16,
  TOKEN: 32,
  HOST: 64,
  ELITE: 128,
} as const;
export type Supertype = number;

const Supertype_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Supertype).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Supertype value, as Python's `.name`. */
export function supertypeName(value: number): string {
  return Supertype_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Zone` (IntEnum). */
export const Zone = {
  LIBRARY: 0,
  HAND: 1,
  BATTLEFIELD: 2,
  GRAVEYARD: 3,
  STACK: 4,
  EXILE: 5,
  COMMAND: 6,
} as const;
export type Zone = number;

const Zone_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Zone).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Zone value, as Python's `.name`. */
export function zoneName(value: number): string {
  return Zone_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Phase` (IntEnum). */
export const Phase = {
  BEGINNING: 0,
  PRECOMBAT_MAIN: 1,
  COMBAT: 2,
  POSTCOMBAT_MAIN: 3,
  ENDING: 4,
} as const;
export type Phase = number;

const Phase_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Phase).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Phase value, as Python's `.name`. */
export function phaseName(value: number): string {
  return Phase_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Step` (IntEnum). */
export const Step = {
  UNTAP: 0,
  UPKEEP: 1,
  DRAW: 2,
  MAIN: 3,
  BEGINNING_OF_COMBAT: 4,
  DECLARE_ATTACKERS: 5,
  DECLARE_BLOCKERS: 6,
  FIRST_STRIKE_COMBAT_DAMAGE: 7,
  COMBAT_DAMAGE: 8,
  END_OF_COMBAT: 9,
  END_STEP: 10,
  CLEANUP: 11,
} as const;
export type Step = number;

const Step_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Step).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Step value, as Python's `.name`. */
export function stepName(value: number): string {
  return Step_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Timing` (IntEnum). */
export const Timing = {
  SORCERY: 0,
  INSTANT: 1,
} as const;
export type Timing = number;

const Timing_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Timing).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Timing value, as Python's `.name`. */
export function timingName(value: number): string {
  return Timing_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Layer` (IntEnum). */
export const Layer = {
  COPY: 10,
  FACE_DOWN: 11,
  CONTROL: 20,
  TEXT: 30,
  TYPE: 40,
  COLOR: 50,
  ABILITY: 60,
  PT_CDA: 70,
  PT_SET: 71,
  PT_MODIFY: 72,
  PT_COUNTERS: 73,
  PT_SWITCH: 74,
} as const;
export type Layer = number;

const Layer_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Layer).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Layer value, as Python's `.name`. */
export function layerName(value: number): string {
  return Layer_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Duration` (IntEnum). */
export const Duration = {
  PERMANENT: 0,
  END_OF_TURN: 1,
  END_OF_COMBAT: 2,
  YOUR_NEXT_TURN: 3,
  END_OF_YOUR_NEXT_TURN: 4,
  WHILE_SOURCE_PERSISTS: 5,
  CUSTOM: 6,
} as const;
export type Duration = number;

const Duration_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Duration).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Duration value, as Python's `.name`. */
export function durationName(value: number): string {
  return Duration_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `LossReason` (IntEnum). */
export const LossReason = {
  LIFE: 1,
  EMPTY_LIBRARY: 2,
  POISON: 3,
  COMMANDER_DAMAGE: 4,
  EFFECT: 5,
  CONCEDE: 6,
  DRAW_AGREED: 7,
} as const;
export type LossReason = number;

const LossReason_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(LossReason).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single LossReason value, as Python's `.name`. */
export function lossReasonName(value: number): string {
  return LossReason_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `WinReason` (IntEnum). */
export const WinReason = {
  LAST_PLAYER_STANDING: 1,
  ALTERNATE_WIN_CONDITION: 2,
  STALL_OUT: 3,
  DRAW: 4,
} as const;
export type WinReason = number;

const WinReason_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(WinReason).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single WinReason value, as Python's `.name`. */
export function winReasonName(value: number): string {
  return WinReason_NAMES_BY_VALUE.get(value) ?? String(value);
}

/** Python `Layout` (IntEnum). */
export const Layout = {
  NORMAL: 0,
  SPLIT: 1,
  FLIP: 2,
  TRANSFORM: 3,
  MODAL_DFC: 4,
  MELD: 5,
  LEVELER: 6,
  CLASS: 7,
  SAGA: 8,
  ADVENTURE: 9,
  MUTATE: 10,
  PROTOTYPE: 11,
  CASE: 12,
  ROOM: 13,
  BATTLE: 14,
  TOKEN: 15,
  EMBLEM: 16,
  REVERSIBLE: 17,
  AUGMENT: 18,
  HOST: 19,
  ART_SERIES: 20,
  DOUBLE_FACED_TOKEN: 21,
  PLANAR: 22,
  SCHEME: 23,
  VANGUARD: 24,
  PREPARE: 25,
} as const;
export type Layout = number;

const Layout_NAMES_BY_VALUE: ReadonlyMap<number, string> = new Map(
  Object.entries(Layout).map(([k, v]) => [v, k] as [number, string]),
);
/** The member name of a single Layout value, as Python's `.name`. */
export function layoutName(value: number): string {
  return Layout_NAMES_BY_VALUE.get(value) ?? String(value);
}

export const COLOR_ORDER: readonly Color[] = [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN];

export const CARD_TYPE_ORDER: readonly CardType[] = [CardType.ARTIFACT, CardType.BATTLE, CardType.BOSS, CardType.CARD, CardType.CONSPIRACY, CardType.CREATURE, CardType.DUNGEON, CardType.EMBLEM, CardType.ENCHANTMENT, CardType.EVENT, CardType.HERO, CardType.INSTANT, CardType.KINDRED, CardType.LAND, CardType.PHENOMENON, CardType.PLANE, CardType.PLANESWALKER, CardType.SCHEME, CardType.SORCERY, CardType.STICKERS, CardType.VANGUARD];

export const SUPERTYPE_ORDER: readonly Supertype[] = [Supertype.BASIC, Supertype.ELITE, Supertype.LEGENDARY, Supertype.ONGOING, Supertype.SNOW, Supertype.TOKEN, Supertype.WORLD, Supertype.HOST];

export const WUBRG: Color = Color.WHITE | Color.BLUE | Color.BLACK | Color.RED | Color.GREEN;

export const PERMANENT_TYPES: CardType = CardType.ARTIFACT | CardType.BATTLE | CardType.CREATURE | CardType.ENCHANTMENT | CardType.LAND | CardType.PLANESWALKER;

export const SPELL_TYPES: CardType = CardType.ARTIFACT | CardType.BATTLE | CardType.CREATURE | CardType.ENCHANTMENT | CardType.PLANESWALKER | CardType.INSTANT | CardType.SORCERY | CardType.KINDRED;

export const ORDERED_ZONES: ReadonlySet<Zone> = new Set([Zone.LIBRARY, Zone.GRAVEYARD, Zone.STACK]);

export const PLAYER_ZONES: ReadonlySet<Zone> = new Set([Zone.LIBRARY, Zone.HAND, Zone.GRAVEYARD]);

export const PUBLIC_ZONES: ReadonlySet<Zone> = new Set([Zone.BATTLEFIELD, Zone.GRAVEYARD, Zone.STACK, Zone.COMMAND]);

export const NO_PRIORITY_STEPS: ReadonlySet<Step> = new Set([Step.UNTAP, Step.CLEANUP]);

export const COMBAT_STEPS: ReadonlySet<Step> = new Set([Step.BEGINNING_OF_COMBAT, Step.DECLARE_ATTACKERS, Step.DECLARE_BLOCKERS, Step.FIRST_STRIKE_COMBAT_DAMAGE, Step.COMBAT_DAMAGE, Step.END_OF_COMBAT]);

export const DUAL_CASTABLE_LAYOUTS: ReadonlySet<Layout> = new Set([Layout.SPLIT, Layout.MODAL_DFC, Layout.ADVENTURE]);

export const BACK_FACE_ONLY_LAYOUTS: ReadonlySet<Layout> = new Set([Layout.FLIP, Layout.TRANSFORM, Layout.MELD]);

export const COLOR_LETTERS: ReadonlyMap<Color, string> = new Map([
  [Color.WHITE, "W"],
  [Color.BLUE, "U"],
  [Color.BLACK, "B"],
  [Color.RED, "R"],
  [Color.GREEN, "G"],
]);

export const LETTER_TO_COLOR: ReadonlyMap<string, Color> = new Map([
  ["W", Color.WHITE],
  ["U", Color.BLUE],
  ["B", Color.BLACK],
  ["R", Color.RED],
  ["G", Color.GREEN],
]);

export const CARD_TYPE_NAMES: ReadonlyMap<string, CardType> = new Map([
  ["artifact", CardType.ARTIFACT],
  ["battle", CardType.BATTLE],
  ["boss", CardType.BOSS],
  ["card", CardType.CARD],
  ["conspiracy", CardType.CONSPIRACY],
  ["creature", CardType.CREATURE],
  ["dungeon", CardType.DUNGEON],
  ["emblem", CardType.EMBLEM],
  ["enchantment", CardType.ENCHANTMENT],
  ["event", CardType.EVENT],
  ["hero", CardType.HERO],
  ["instant", CardType.INSTANT],
  ["kindred", CardType.KINDRED],
  ["tribal", CardType.KINDRED],
  ["land", CardType.LAND],
  ["phenomenon", CardType.PHENOMENON],
  ["plane", CardType.PLANE],
  ["planeswalker", CardType.PLANESWALKER],
  ["scheme", CardType.SCHEME],
  ["sorcery", CardType.SORCERY],
  ["stickers", CardType.STICKERS],
  ["summon", CardType.CREATURE],
  ["vanguard", CardType.VANGUARD],
]);

export const TYPE_DISPLAY_NAMES: ReadonlyMap<CardType, string> = new Map([
  [CardType.ARTIFACT, "Artifact"],
  [CardType.BATTLE, "Battle"],
  [CardType.BOSS, "Boss"],
  [CardType.CARD, "Card"],
  [CardType.CONSPIRACY, "Conspiracy"],
  [CardType.CREATURE, "Creature"],
  [CardType.DUNGEON, "Dungeon"],
  [CardType.EMBLEM, "Emblem"],
  [CardType.ENCHANTMENT, "Enchantment"],
  [CardType.EVENT, "Event"],
  [CardType.HERO, "Hero"],
  [CardType.INSTANT, "Instant"],
  [CardType.KINDRED, "Kindred"],
  [CardType.LAND, "Land"],
  [CardType.PHENOMENON, "Phenomenon"],
  [CardType.PLANE, "Plane"],
  [CardType.PLANESWALKER, "Planeswalker"],
  [CardType.SCHEME, "Scheme"],
  [CardType.SORCERY, "Sorcery"],
  [CardType.STICKERS, "Stickers"],
  [CardType.VANGUARD, "Vanguard"],
]);

export const SUPERTYPE_NAMES: ReadonlyMap<string, Supertype> = new Map([
  ["basic", Supertype.BASIC],
  ["elite", Supertype.ELITE],
  ["legendary", Supertype.LEGENDARY],
  ["ongoing", Supertype.ONGOING],
  ["snow", Supertype.SNOW],
  ["token", Supertype.TOKEN],
  ["world", Supertype.WORLD],
  ["host", Supertype.HOST],
]);

export const SUPERTYPE_DISPLAY_NAMES: ReadonlyMap<Supertype, string> = new Map([
  [Supertype.BASIC, "Basic"],
  [Supertype.ELITE, "Elite"],
  [Supertype.LEGENDARY, "Legendary"],
  [Supertype.ONGOING, "Ongoing"],
  [Supertype.SNOW, "Snow"],
  [Supertype.TOKEN, "Token"],
  [Supertype.WORLD, "World"],
  [Supertype.HOST, "Host"],
]);

export const LAYOUT_NAMES: ReadonlyMap<string, Layout> = new Map([
  ["normal", Layout.NORMAL],
  ["split", Layout.SPLIT],
  ["flip", Layout.FLIP],
  ["transform", Layout.TRANSFORM],
  ["modal_dfc", Layout.MODAL_DFC],
  ["meld", Layout.MELD],
  ["leveler", Layout.LEVELER],
  ["class", Layout.CLASS],
  ["saga", Layout.SAGA],
  ["adventure", Layout.ADVENTURE],
  ["prepare", Layout.PREPARE],
  ["mutate", Layout.MUTATE],
  ["prototype", Layout.PROTOTYPE],
  ["case", Layout.CASE],
  ["room", Layout.ROOM],
  ["battle", Layout.BATTLE],
  ["token", Layout.TOKEN],
  ["emblem", Layout.EMBLEM],
  ["reversible_card", Layout.REVERSIBLE],
  ["augment", Layout.AUGMENT],
  ["host", Layout.HOST],
  ["art_series", Layout.ART_SERIES],
  ["double_faced_token", Layout.DOUBLE_FACED_TOKEN],
  ["planar", Layout.PLANAR],
  ["scheme", Layout.SCHEME],
  ["vanguard", Layout.VANGUARD],
]);

/** The colours in a flag, in WUBRG order (Python `iter(Color)`). */
export function colorsOf(colors: Color): Color[] {
  return COLOR_ORDER.filter((c) => (colors & c) !== 0);
}

/** How many colours a flag holds (Python `Color.count`). */
export function colorCount(colors: Color): number {
  let n = 0;
  for (let v = colors >>> 0; v !== 0; v &= v - 1) n += 1;
  return n;
}

/** Build a Color flag from letters; "C" (colorless) contributes nothing. */
export function colorsFromLetters(letters: Iterable<string>): Color {
  let result: Color = Color.NONE;
  for (const raw of letters) {
    const ch = raw.toUpperCase();
    if (ch === "C") continue;
    const c = LETTER_TO_COLOR.get(ch);
    if (c === undefined) throw new Error(`unknown color letter ${JSON.stringify(ch)}`);
    result |= c;
  }
  return result;
}

/** Render a Color flag in WUBRG order, e.g. BLUE|RED -> "UR". */
export function colorLetters(colors: Color): string {
  return COLOR_ORDER.filter((c) => (colors & c) !== 0)
    .map((c) => COLOR_LETTERS.get(c) ?? "")
    .join("");
}

/** The card types in a flag, in CR 205.2a alphabetical order. */
export function cardTypesOf(types: CardType): CardType[] {
  return CARD_TYPE_ORDER.filter((t) => (types & t) !== 0);
}

/** The supertypes in a flag, in their canonical order. */
export function supertypesOf(types: Supertype): Supertype[] {
  return SUPERTYPE_ORDER.filter((t) => (types & t) !== 0);
}

