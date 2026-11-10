/** The card IR: the parser's output as versioned JSON. See docs/ir-json.md. */
export * from "./types";
export { DEFAULTS, REQUIRED } from "./defaults";
export { check, complete, enumValue, faceAbilities, irString, isEnumLiteral, readCard } from "./decode";
