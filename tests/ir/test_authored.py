"""Authored readings (mtgfish/ir/authored): current, valid, and served."""

from __future__ import annotations

import json

import pytest

from mtgfish.ir import authored


def test_every_authored_reading_is_current_and_reads(card_db):
    """Each file decodes, matches its card's present text, has one entry per
    face, and holds no unread node. A card whose Scryfall text changed fails
    here until it is re-authored or removed."""
    from mtgfish.tools.authored import problems

    assert problems() == []


@pytest.mark.parametrize("oracle_id", authored.oracle_ids())
def test_an_authored_card_is_served_and_validates(card_db, oracle_id):
    jsonschema = pytest.importorskip("jsonschema")
    from mtgfish.ir.schema import json_schema
    from mtgfish.tools.ir_export import _card_document

    card = card_db.by_oracle_id(oracle_id)
    document = json.loads(json.dumps(_card_document(card)))
    assert all(face.get("authored") and face["fully_read"] and not face["failures"] for face in document["faces"])
    errors = list(jsonschema.Draft202012Validator(json_schema()).iter_errors(document))
    assert not errors, errors[0].message[:300]


def test_a_card_whose_text_changed_falls_back_to_the_parser(card_db):
    card = card_db.lookup("Cabal Ritual")
    assert authored.abilities(card, "not its hash") is None


def test_each_paraphrase_still_reads_as_its_stored_ir(card_db):
    """The stored IR is what the paraphrase read as when it was written. If
    the grammar now reads a paraphrase differently, say so: the reading is
    still the stored one, but the reviewer's check has gone out of date."""
    from mtgfish.ir.codec import encode
    from mtgfish.tools.authored import _paraphrase

    drift = []
    for oracle_id in authored.oracle_ids():
        data = authored.entry(oracle_id)
        card = card_db.by_oracle_id(oracle_id)
        for f, face in enumerate(data["faces"]):
            for item in face["abilities"]:
                if not item["as"]:
                    continue
                import dataclasses

                now = dataclasses.replace(_paraphrase(card, f, item["as"]), text=item["text"])
                if encode(now, "Ability") != item["ir"]:
                    drift.append(f"{data['name']}: {item['text']}")
    assert drift == []
