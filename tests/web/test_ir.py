"""Card IR served to the browser engine: /api/ir, /api/ir/decks, /ir/v1/<id>.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mtgfish.ir.store import code_fingerprint
from mtgfish.web.auth.ratelimit import RateLimiter
from mtgfish.web.ir import LIMITS, IrStore, build_router

SAMPLES = Path(__file__).resolve().parents[2] / "schema" / "samples"
KRENKO = "68418069-f615-40ef-ae0d-764192acae00"
GOBLIN_GUIDE = "51d9564b-44fc-4de1-9119-09d7b4089378"
GOBLIN_LACKEY = "213fbbdd-f1bc-4bba-9124-7b26e9a706c8"

BUILD = code_fingerprint()[:16]

DECK = "1 Goblin Lackey\n1 Goblin Guide\n\n// Commander\n1 Krenko, Mob Boss\n"


@pytest.fixture
def ir_dir(tmp_path) -> Path:
    """An export made of the sample cards, with Goblin Guide left out of the
    index, as if the export were older than the card data."""
    out = tmp_path / "v1"
    out.mkdir()
    cards = {}
    for path in SAMPLES.glob("*-*-*-*-*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["oracle_id"] == GOBLIN_GUIDE:
            continue
        shutil.copy(path, out / path.name)
        cards[document["oracle_id"]] = {
            "name": document["name"], "card_hash": document["card_hash"],
        }
    # Written by the code running now, so IRStore treats the export as current.
    index = {"ir_version": 1, "built_at": "2026-11-10T12:00:00", "snapshot": "snap",
             "code": code_fingerprint(), "cards": cards, "names": {}}
    (out / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return out


def make_client(root: Path, card_db=None, limits=None) -> TestClient:
    factory = (lambda: type(card_db)(card_db.path)) if card_db is not None else None
    store = IrStore(root, db_factory=factory)
    app = FastAPI()
    app.include_router(build_router(store, RateLimiter(limits or LIMITS)))
    return TestClient(app)


@pytest.fixture
def client(ir_dir, card_db):
    return make_client(ir_dir, card_db)


def test_status_says_what_is_exported(ir_dir):
    reply = make_client(ir_dir).get("/api/ir").json()
    assert reply["available"] is True
    assert reply["ir_version"] == 1 and reply["snapshot"] == "snap"
    assert reply["cards"] > 200


def test_status_without_an_export(tmp_path):
    assert make_client(tmp_path).get("/api/ir").json() == {"available": False, "ir_version": 1}


def test_a_deck_resolves_to_oracle_ids_and_card_urls(client, ir_dir):
    reply = client.post("/api/ir/decks", json={"decklists": [DECK]})
    assert reply.status_code == 200
    body = reply.json()
    [deck] = body["decks"]
    assert deck["commanders"] == [KRENKO]
    assert {c["oracle_id"] for c in deck["cards"]} == {GOBLIN_LACKEY, GOBLIN_GUIDE}
    assert deck["unresolved"] == []

    lackey = body["cards"][GOBLIN_LACKEY]
    digest = json.loads((ir_dir / f"{GOBLIN_LACKEY}.json").read_text("utf-8"))["card_hash"]
    assert lackey["card_hash"] == digest
    assert lackey["url"] == f"/ir/v1/{GOBLIN_LACKEY}.json?h={digest}&b={BUILD}"
    assert body["build"] == BUILD


def test_a_card_missing_from_the_export_is_parsed_live_and_sent_inline(client, caplog):
    with caplog.at_level("WARNING", logger="mtgfish.web.ir"):
        body = client.post("/api/ir/decks", json={"decklists": [DECK]}).json()
    assert body["decks"][0]["live_ir"] == ["Goblin Guide"]
    guide = body["cards"][GOBLIN_GUIDE]
    # Inline, with no URL: a live parse is never a cacheable file.
    assert "url" not in guide
    assert guide["document"]["oracle_id"] == GOBLIN_GUIDE
    assert guide["document"]["ir_version"] == 1
    assert guide["card_hash"] == guide["document"]["card_hash"]
    assert "parsed 1 card(s) live" in caplog.text and "Goblin Guide" in caplog.text


def test_exported_cards_are_not_parsed_live(client, caplog):
    with caplog.at_level("WARNING", logger="mtgfish.web.ir"):
        body = client.post("/api/ir/decks", json={"decklists": ["1 Goblin Lackey"]}).json()
    assert body["decks"][0]["live_ir"] == []
    assert "url" in body["cards"][GOBLIN_LACKEY]
    assert "live" not in caplog.text


def test_a_card_exported_from_older_text_is_parsed_live(ir_dir, card_db):
    path = ir_dir / f"{GOBLIN_LACKEY}.json"
    document = json.loads(path.read_text("utf-8"))
    document["card_hash"] = "older-text"
    path.write_text(json.dumps(document), encoding="utf-8")
    body = make_client(ir_dir, card_db).post(
        "/api/ir/decks", json={"decklists": ["1 Goblin Lackey"]}).json()
    lackey = body["cards"][GOBLIN_LACKEY]
    assert "url" not in lackey and lackey["card_hash"] != "older-text"


def test_an_export_from_other_parser_code_is_served_live(ir_dir, card_db):
    index = json.loads((ir_dir / "index.json").read_text("utf-8"))
    index["code"] = "written-by-older-parser-code"
    (ir_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    body = make_client(ir_dir, card_db).post("/api/ir/decks", json={"decklists": [DECK]}).json()
    assert all("url" not in ref for ref in body["cards"].values())
    assert sorted(body["decks"][0]["live_ir"]) == ["Goblin Guide", "Goblin Lackey", "Krenko, Mob Boss"]


def test_the_build_falls_back_to_the_export_time(ir_dir):
    index = json.loads((ir_dir / "index.json").read_text("utf-8"))
    del index["code"]
    (ir_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    assert make_client(ir_dir).get("/api/ir").json()["build"] == "2026-11-10T12:00:00"


def test_a_card_under_another_build_is_not_immutable(client, ir_dir):
    digest = json.loads((ir_dir / f"{KRENKO}.json").read_text("utf-8"))["card_hash"]
    reply = client.get(f"/ir/v1/{KRENKO}.json?h={digest}&b=old-build")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-cache"


def test_unknown_names_are_listed(client):
    body = client.post("/api/ir/decks", json={"decklists": ["1 Not A Real Card Name"]}).json()
    assert body["decks"][0]["unresolved"] == ["Not A Real Card Name"]


def test_a_card_file_with_its_hash_is_immutable(client, ir_dir):
    url = client.post("/api/ir/decks", json={"decklists": [DECK]}).json()["cards"][KRENKO]["url"]
    reply = client.get(url)
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert reply.json() == json.loads((ir_dir / f"{KRENKO}.json").read_text("utf-8"))


def test_a_card_file_without_its_hash_revalidates(client):
    reply = client.get(f"/ir/v1/{KRENKO}.json")
    assert reply.headers["cache-control"] == "no-cache"
    again = client.get(f"/ir/v1/{KRENKO}.json", headers={"If-None-Match": reply.headers["etag"]})
    assert again.status_code == 304


def test_card_files_are_gzipped_when_asked(client):
    reply = client.get(f"/ir/v1/{KRENKO}.json", headers={"Accept-Encoding": "gzip"})
    assert reply.headers.get("content-encoding") == "gzip"
    assert reply.json()["oracle_id"] == KRENKO


@pytest.mark.parametrize("name", [
    "not-an-id.json", f"{KRENKO}.txt", "..%2Findex.json", f"{GOBLIN_GUIDE}.json",
    "00000000-0000-0000-0000-000000000000.json",
])
def test_only_exported_cards_are_served(ir_dir, name):
    assert make_client(ir_dir).get(f"/ir/v1/{name}").status_code == 404


def test_the_index_itself_is_not_served(ir_dir):
    assert make_client(ir_dir).get("/ir/v1/index.json").status_code == 404


def test_decks_must_be_json(ir_dir):
    reply = make_client(ir_dir).post("/api/ir/decks", content=b"x",
                                     headers={"content-type": "text/plain"})
    assert reply.status_code == 415


@pytest.mark.parametrize("body", [{}, {"decklists": []}, {"decklists": ["a"] * 5},
                                  {"decklists": [3]}, {"decklists": ["x" * 60_001]}])
def test_bad_deck_requests_are_refused(ir_dir, body):
    assert make_client(ir_dir).post("/api/ir/decks", json=body).status_code == 400


def test_decks_without_an_export_say_so(tmp_path):
    reply = make_client(tmp_path).post("/api/ir/decks", json={"decklists": [DECK]})
    assert reply.status_code == 503


def test_deck_requests_are_rate_limited(ir_dir):
    client = make_client(ir_dir, limits={"ir_decks": (1, 60), "ir_card": (1, 60)})
    client.post("/api/ir/decks", json={})
    assert client.post("/api/ir/decks", json={}).status_code == 429
    client.get(f"/ir/v1/{KRENKO}.json")
    assert client.get(f"/ir/v1/{KRENKO}.json").status_code == 429


def test_a_new_export_is_picked_up_without_a_restart(ir_dir):
    client = make_client(ir_dir)
    assert client.get("/api/ir").json()["snapshot"] == "snap"
    index = json.loads((ir_dir / "index.json").read_text("utf-8"))
    index["snapshot"] = "newer"
    path = ir_dir / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    import os

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert client.get("/api/ir").json()["snapshot"] == "newer"


def test_the_app_serves_ir_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("MTGFISH_IR_DIR", str(tmp_path))
    from mtgfish.web.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/ir").json()["available"] is False
        assert client.get(f"/ir/v1/{KRENKO}.json").status_code == 404
