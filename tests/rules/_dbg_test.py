import test_exile_costs as A, test_zone_activated_abilities as B
from mtgfish.rules.cr100_game_concepts.cr117_priority import settle
def _show(game, tag):
    print(tag, [(o.id, str(o)[:80]) for o in (game.objects[i] for i in game.stack)], "pending", getattr(game,'pending_triggers',None))
def test_a(box):
    orig=box.resolve_top
    def rt():
        _show(box.game,"before"); settle(box.game); _show(box.game,"after settle"); return orig()
    box.resolve_top=rt
    A.test_exile_this_creature_exiles_the_creature_not_a_graveyard_card.__wrapped__(box) if hasattr(A.test_exile_this_creature_exiles_the_creature_not_a_graveyard_card,'__wrapped__') else A.test_exile_this_creature_exiles_the_creature_not_a_graveyard_card(box)
