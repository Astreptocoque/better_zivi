from __future__ import annotations

import json

import numpy as np
from fastapi.testclient import TestClient

from zivi.api import create_app
from zivi.converter import convert, flatten_text, parse_address
from zivi.search import Engine, fts_query


def ids(res):
    return sorted(it["number"] - 110000 for it in res["items"])


# ---------------------------------------------------------------- converter

def test_report(store):
    _, report = store
    assert report["assignments"] == 5
    assert report["with_location"] == 4  # the Ethiopian one cannot be located offline
    assert report["with_weeks"] == 5
    assert report["unknown_detail_fields"] == {}


def test_address_variants():
    assert parse_address({"strasse": "A 1", "plz": "3800", "ort": "Interlaken"})["plz"] == "3800"
    a = parse_address({"adresszeile1": "B", "ort": {"plz": 1003, "name": "Lausanne"}})
    assert (a["plz"], a["city"]) == ("1003", "Lausanne")
    a = parse_address({"zeile": "Weg 3, 8001 Zürich"})
    assert (a["plz"], a["city"]) == ("8001", "Zürich")
    a = parse_address({"koordLat": 46.1, "koordLong": 7.1, "ort": "X"})
    assert a["lat"] == 46.1


def test_flatten_tasks_keeps_percent():
    assert flatten_text([{"prozent": 90, "taetigkeit": "Pflege"}]).startswith("90% Pflege")


def test_attributes(store):
    db, _ = store
    e = Engine(db)
    r1 = e.rows[5000001]
    assert (r1["housing"], r1["meals"], r1["weekend"], r1["canton"]) == (1, 1, "M", "BE")
    assert e.rows[5000003]["reputation_check"] == 1
    assert e.rows[5000005]["abroad"] == 1
    assert e.rows[5000002]["language"] == "fr"
    assert e.rows[5000004]["language"] == "it"


def test_reimport_keeps_geocache(tmp_path, store, sample_records, table):
    db, _ = store
    from zivi.geocode import Geocoder
    src = tmp_path / "again.json"
    src.write_text(json.dumps(sample_records), encoding="utf-8")
    calls = []

    class Spy(Geocoder):
        def locate(self, **kw):
            calls.append(kw)
            return super().locate(**kw)

    convert(src, db, geocoder_factory=lambda d: Spy(d, online=False, table=table),
            progress=lambda m, f: None)
    import sqlite3
    n = sqlite3.connect(db).execute("SELECT count(*) FROM geocache").fetchone()[0]
    assert n >= 4 and calls  # cache table carried over and used


# ---------------------------------------------------------------- search

def test_text_search_prefix_and_accents(store):
    e = Engine(store[0])
    assert ids(e.search({"text": "betreu"})) == [1]
    assert ids(e.search({"text": "paturages"})) == [2]  # accent-insensitive
    assert ids(e.search({"text": "anziani OR küche"})) == [3, 4]
    assert ids(e.search({"text": '"plein air"'})) == [2]
    assert 1 not in ids(e.search({"text": "-betreuung"}))


def test_fts_query_builder():
    assert fts_query("pflege") == ('"pflege"*', None)
    pos, neg = fts_query('a "b c" -d')
    assert pos == '"a"* AND "b c"' and neg == '"d"*'


def test_attribute_filters(store):
    e = Engine(store[0])
    assert ids(e.search({"housing": 1})) == [1, 4, 5]
    assert ids(e.search({"housing": 1, "meals": 1})) == [1]
    assert ids(e.search({"weekend": ["M"]})) == [1]
    assert ids(e.search({"night": ["J"]})) == [3]
    assert ids(e.search({"reputation_check": 0, "abroad": 0})) == [1, 2, 4]
    assert ids(e.search({"task_areas": ["Contacts humains / Vieillesse"]})) == [1, 4]
    assert ids(e.search({"task_areas": ["Travail en plein air", "Travail physique"],
                         "task_areas_mode": "all"})) == [2]
    assert ids(e.search({"min_weeks_max": 4})) == [1, 3, 4, 5]
    assert ids(e.search({"canton": ["VD", "TI"]})) == [2, 4]
    assert ids(e.search({"language": ["it"]})) == [4]


def test_period_filters(store):
    e = Engine(store[0])
    assert ids(e.search({"period": {"from": "2027-09-01", "to": "2027-09-30"}})) == [2]
    assert ids(e.search({"period": {"from": "2027-08-02", "to": "2027-08-31",
                                    "duration_weeks": 3}})) == [1]
    assert ids(e.search({"period": {"from": "2027-08-02", "to": "2027-08-09",
                                    "mode": "all"}})) == [1, 4]


def test_geo_radius_and_distance_sort(store):
    e = Engine(store[0])
    bern = {"lat": 46.948, "lon": 7.447}
    res = e.search({"geo": {"centers": [{**bern, "radius_km": 60}]}})
    assert ids(res) == [1]
    res = e.search({"geo": {"centers": [{**bern, "radius_km": 100},
                                        {"lat": 46.195, "lon": 9.023, "radius_km": 5}]}})
    assert ids(res) == [1, 2, 3, 4]  # Interlaken, Lausanne (~79 km), Zürich (~96 km), Bellinzona
    assert res["sort"] == "distance"
    assert res["items"][0]["number"] == 110004  # 0 km from the Bellinzona centre


def test_points_cover_all_matches_and_facets_ignore_own_filter(store):
    e = Engine(store[0])
    res = e.search({"housing": 1, "limit": 1})
    assert len(res["items"]) == 1 and res["total"] == 3
    assert len(res["points"]) == 2 and res["unlocated"] == 1
    housing = dict(res["facets"]["housing"])
    assert housing == {1: 3, 0: 2}  # counts as if the housing filter were not set


def test_semantic_and_hybrid(store, monkeypatch):
    db, _ = store
    e = Engine(db)
    # fake embedder: vector = presence of 3 keywords
    import zivi.semantic as sem

    def fake_embed(self, texts):
        keys = ["alt", "küche", "air"]
        v = np.array([[float(k in t.lower()) + 0.01 for k in keys] for t in texts],
                     dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    monkeypatch.setattr(sem.Embedder, "embed", fake_embed)
    e.ai.api_key = "test-key"
    e.semantic.build(str(db), e.ai_texts(), e.ai)
    assert e.semantic.progress["error"] is None and e.semantic.ready
    res = e.search({"text": "küche", "text_mode": "semantic", "min_similarity": 0.9})
    assert ids(res) == [3] and res["mode_used"] == "semantic"
    res = e.search({"text": "air", "text_mode": "hybrid"})
    assert res["items"][0]["number"] == 110002 and res["mode_used"] == "hybrid"


def test_semantic_without_token_falls_back(store):
    e = Engine(store[0])
    e.ai.api_key = ""
    e.ai.base_url = "https://api.openai.com/v1"
    res = e.search({"text": "betreuung", "text_mode": "semantic"})
    assert ids(res) == [1] and res["warnings"]


# ---------------------------------------------------------------- api

def test_api_roundtrip(store, tmp_path, monkeypatch, sample_records, table):
    db, _ = store
    import zivi.api as api_mod
    monkeypatch.setattr(api_mod, "PostcodeTable", lambda: table)
    monkeypatch.setattr(api_mod.config, "DATA_DIR", tmp_path)
    client = TestClient(create_app(db))
    assert client.get("/").status_code == 200
    s = client.get("/api/stats").json()
    assert s["assignments"] == 5 and s["located"] == 4
    res = client.post("/api/search", json={"text": "alpages"}).json()
    assert res["total"] == 1
    d = client.get(f"/api/assignment/{res['items'][0]['id']}").json()
    assert d["weeks"] == ["2027-09-06", "2027-09-13"] and d["contact_email"]
    g = client.get("/api/geocode", params={"q": "laus"}).json()["results"]
    assert g and g[0]["label"].lower().startswith("laus")
    csv_text = client.post("/api/export", json={"housing": 1}).text
    assert csv_text.count("\n") == 4  # header + 3 rows

    # upload a new file through the UI endpoint
    import zivi.converter as conv
    from zivi.geocode import Geocoder
    real = conv.convert
    monkeypatch.setattr(api_mod, "convert", lambda p, d, progress: real(
        p, d, progress=progress,
        geocoder_factory=lambda x: Geocoder(x, online=False, table=table)))
    payload = json.dumps(sample_records[:2]).encode()
    r = client.post("/api/import", files={"file": ("new.json", payload, "application/json")})
    assert r.status_code == 200
    import time
    for _ in range(50):
        st = client.get("/api/import/status").json()
        if not st["running"]:
            break
        time.sleep(0.1)
    assert st["error"] is None and st["report"]["assignments"] == 2
    assert client.get("/api/stats").json()["assignments"] == 2


def test_semantic_keeps_only_top_k(store, monkeypatch):
    db, _ = store
    e = Engine(db)
    import zivi.semantic as sem
    monkeypatch.setattr(sem.Embedder, "embed", lambda self, texts: np.array(
        [[float(len(t) % 7) + 1, 1.0] for t in texts], dtype=np.float32) /
        np.linalg.norm([[float(len(t) % 7) + 1, 1.0] for t in texts], axis=1, keepdims=True))
    e.ai.api_key = "k"
    e.semantic.build(str(db), e.ai_texts(), e.ai)
    assert e.search({"text": "x", "text_mode": "semantic", "ai_top_k": 2})["total"] == 2
    assert e.search({"text": "x", "text_mode": "semantic", "ai_top_k": 0})["total"] == 5


def test_favorites_filter_facets_and_persistence(store):
    db, _ = store
    e = Engine(db)
    all_ids = sorted(e.rows)
    a, b = all_ids[0], all_ids[1]
    assert e.set_favorite(a, True) and e.set_favorite(b, True)
    assert not e.set_favorite(b, False)
    res = e.search({"favorites_only": True})
    assert [it["id"] for it in res["items"]] == [a] and res["favorites"] == 1
    assert res["items"][0]["favorite"] is True
    # facets only count favourites
    assert sum(n for _, n in res["facets"]["housing"]) == 1
    # points carry the sector code and the favourite flag
    assert all(p[7] for p in res["points"])
    # favourites survive a reload of the engine
    assert Engine(db).favorites == {a}


def test_sector_code():
    from zivi.search import sector_code
    assert sector_code("2 Social") == "2"
    assert sector_code(None) is None


def test_place_suggestions_fold_postal_districts(tmp_path):
    from zivi.geocode import PostcodeTable
    rows = [
        ("1000", "Lausanne 26", "VD", 46.5557, 6.6962),
        ("1000", "Lausanne 10", "VD", 46.5218, 6.633),
        ("1001", "Lausanne", "VD", 46.516, 6.6328),
        ("1014", "Lausanne Adm cant", "VD", 46.52, 6.63),
        ("4417", "Lausen", "BL", 47.47, 7.76),
        ("4001", "Basel", "BS", 47.556, 7.59),
        ("4040", "Basel", "BL", 47.5186, 7.6174),  # PostFinance centre in Münchenstein
        ("9470", "Buchs SG", "SG", 47.17, 9.47),
        ("5033", "Buchs AG", "AG", 47.39, 8.08),
    ]
    (tmp_path / "CH.txt").write_text("".join(
        f"CH\t{plz}\t{name}\tx\t{c}\t\t\t\t\t{la}\t{lo}\t\n" for plz, name, c, la, lo in rows),
        encoding="utf-8")
    (tmp_path / "LI.txt").write_text("LI\t9490\tVaduz\tVaduz\t7001\t\t\t\t\t47.14\t9.52\t6\n",
                                     encoding="utf-8")
    t = PostcodeTable(tmp_path, download=False)
    lausanne = t.suggest("lausanne")
    assert [s["label"] for s in lausanne] == ["Lausanne · VD"]
    assert (lausanne[0]["lat"], lausanne[0]["lon"]) == (46.516, 6.6328)  # the plain entry
    assert [s["label"] for s in t.suggest("laus")] == ["Lausen · BL", "Lausanne · VD"]
    assert [s["label"] for s in t.suggest("basel")] == ["Basel · BS"]
    assert [s["label"] for s in t.suggest("buchs")] == ["Buchs AG", "Buchs SG"]
    assert [s["label"] for s in t.suggest("vaduz")] == ["Vaduz · FL"]


def test_rarity_sort_and_traits(store):
    e = Engine(store[0])
    res = e.search({"sort": "rarity"})
    assert res["sort"] == "rarity" and res["total"] == 5
    scores = [it["rarity"] for it in res["items"]]
    assert scores == sorted(scores, reverse=True)
    top = res["items"][0]
    # with 5 records a value held by a single one (20 %) is not rare at the 5 % threshold,
    # so lower it to check the traits are reported rarest first
    import zivi.search as search_mod
    old = search_mod.RARE_SHARE
    try:
        search_mod.RARE_SHARE = 0.2
        e._compute_rarity()
        rare = e.rare[top["id"]]
        assert rare and all(r["count"] == 1 for r in rare)
        assert {"field", "label", "value", "count", "share"} <= rare[0].keys()
        assert e.detail(top["id"])["rare"] == rare
    finally:
        search_mod.RARE_SHARE = old


def test_ai_provider_and_model_survive_restart(store, monkeypatch):
    db, _ = store
    monkeypatch.delenv("ZIVI_AI_MODEL", raising=False)
    monkeypatch.delenv("ZIVI_AI_BASE_URL", raising=False)
    import zivi.semantic as sem

    def fake_embed(self, texts):
        return np.ones((len(texts), 3), dtype=np.float32) / np.sqrt(3)

    monkeypatch.setattr(sem.Embedder, "embed", fake_embed)
    client = TestClient(create_app(db))
    r = client.post("/api/ai/config", json={"base_url": "http://localhost:11434/v1",
                                             "model": "nomic-embed-text", "api_key": "secret"})
    assert r.status_code == 200
    e = Engine(db)  # build directly so the test does not wait on the API's thread
    e.ai.base_url, e.ai.model = "http://localhost:11434/v1", "nomic-embed-text"
    e.semantic.build(str(db), e.ai_texts(), e.ai)
    assert e.semantic.ready

    # "restart": a new server reads the saved choice and loads the local index
    info = TestClient(create_app(db)).get("/api/ai").json()
    assert info["settings"]["model"] == "nomic-embed-text"
    assert info["settings"]["base_url"] == "http://localhost:11434/v1"
    assert info["ready"] and info["vectors"] == 5
    saved = (db.parent / "ai_settings.json").read_text("utf-8")
    assert "secret" not in saved  # the token is never written to disk

    # an explicit environment variable still wins
    monkeypatch.setenv("ZIVI_AI_MODEL", "text-embedding-3-small")
    assert Engine(db).ai.model != "nomic-embed-text"
