/**
 * Commander colour identity in play (CR 903.4). Ported from
 * mtgfish/rules/cr903_commander/cr903_color_identity.py.
 */

import type { CardDef } from "../../kernel/card";
import { Color } from "../../kernel/enums";
import { player, type Game } from "../../kernel/game";
import type { PlayerId } from "../../kernel/ids";

/** The combined identity of this player's commanders; null (undefined, CR 903.4f) with none. */
export function commanderColorIdentity(game: Game, playerId: PlayerId): Color | null {
  const commanders = player(game, playerId).commanders;
  if (commanders.length === 0) return null;
  let identity: number = Color.NONE;
  for (const id of commanders) {
    const card = game.objects.get(id)?.card as CardDef | null | undefined;
    if (card != null) identity |= card.color_identity;
  }
  return identity as Color;
}
