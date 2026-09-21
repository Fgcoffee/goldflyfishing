"""CR 704.5z: one Role per permanent per player.

"If a permanent has more than one Role controlled by the same player attached
to it, each of those Roles except the one with the most recent timestamp is put
into its owner's graveyard."

This state-based action was missing. The engine creates Role tokens (CR
701.54), so a second Role from the same player sat alongside the first and both
grants applied - a creature with two Cursed Roles was twice as cursed as any
real game allows.

Per *player*, like the legend rule and unlike the world rule: two opponents can
each have a Role on the same creature and both stay.
"""

from __future__ import annotations

from harness import make_board

from mtgfish.rules.cr111_tokens import create_tokens
from mtgfish.rules.effects import TokenSpec
from mtgfish.rules.enums import CardType, Zone
from mtgfish.rules.ids import PlayerId


def _role(board, host, controller=0):
    """Create a Role token and attach it to ``host``."""
    spec = TokenSpec(name="Role", types=CardType.ENCHANTMENT, subtypes=("Aura", "Role"))
    token = create_tokens(board.game, spec, PlayerId(controller), 1)[0]
    token.attached_to = host.id
    host.attachments.append(token.id)
    token.timestamp = board.game.ids.timestamp()
    board.game.invalidate_characteristics()
    return token


def _roles_on(board, host):
    """Roles still on the battlefield attached to ``host``.

    A Role that loses this rule is a token, so CR 704.5d makes it cease to
    exist outright - it is gone from ``game.objects``, not merely moved.
    """
    return [
        oid
        for oid in host.attachments
        if (obj := board.game.objects.get(oid)) is not None
        and obj.zone is Zone.BATTLEFIELD
    ]


def _gone(board, token):
    obj = board.game.objects.get(token.id)
    return obj is None or obj.zone is not Zone.BATTLEFIELD


def test_one_role_stays(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    role = _role(board, bear)
    board.sba()
    assert board.game.objects[role.id].zone is Zone.BATTLEFIELD


def test_a_second_role_from_the_same_player_replaces_the_first(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    first = _role(board, bear, controller=0)
    second = _role(board, bear, controller=0)

    board.sba()

    assert _gone(board, first)
    assert board.game.objects[second.id].zone is Zone.BATTLEFIELD


def test_the_most_recent_role_is_the_one_kept(card_db):
    """The rule names the timestamp, not the order they happen to be listed."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    first = _role(board, bear, controller=0)
    second = _role(board, bear, controller=0)
    third = _role(board, bear, controller=0)

    board.sba()

    survivors = _roles_on(board, bear)
    assert survivors == [third.id]
    assert _gone(board, first)
    assert _gone(board, second)


def test_two_players_may_each_have_a_role_on_the_same_creature(card_db):
    """Per player, not per permanent - both survive."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    mine = _role(board, bear, controller=0)
    theirs = _role(board, bear, controller=1)

    board.sba()

    assert board.game.objects[mine.id].zone is Zone.BATTLEFIELD
    assert board.game.objects[theirs.id].zone is Zone.BATTLEFIELD


def test_roles_on_different_permanents_do_not_interact(card_db):
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    lion = board.play("Savannah Lions")
    on_bear = _role(board, bear, controller=0)
    on_lion = _role(board, lion, controller=0)

    board.sba()

    assert board.game.objects[on_bear.id].zone is Zone.BATTLEFIELD
    assert board.game.objects[on_lion.id].zone is Zone.BATTLEFIELD


def test_an_ordinary_aura_is_not_a_role(card_db):
    """Only Roles are culled; two Pacifisms on one creature both stay."""
    board = make_board(card_db)
    bear = board.play("Grizzly Bears")
    spec = TokenSpec(name="Spirit", types=CardType.ENCHANTMENT, subtypes=("Aura",))
    auras = []
    for _ in range(2):
        token = create_tokens(board.game, spec, PlayerId(0), 1)[0]
        token.attached_to = bear.id
        bear.attachments.append(token.id)
        auras.append(token)
    board.game.invalidate_characteristics()

    board.sba()

    assert all(board.game.objects[a.id].zone is Zone.BATTLEFIELD for a in auras)
