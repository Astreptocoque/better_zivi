from __future__ import annotations

import json

import numpy as np
import pytest
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
    # OR groups bind tighter than the implicit AND
    assert fts_query("alpage chien OR chat")[0] == '"alpage"* AND ("chien"* OR "chat"*)'
    assert fts_query("a OR b OR c")[0] == '("a"* OR "b"* OR "c"*)'
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


def test_filter_by_rare_traits(store):
    e = Engine(store[0])
    every = e.search({})["total"]
    # any value of an assignment can be asked for; all asked values must match
    one = next(iter(e.rows))
    key = next(iter(e.trait_keys[one]))
    res = e.search({"traits": [{"field": key[0], "value": key[1]}]})
    assert 1 <= res["total"] <= every
    assert all(key in e.trait_keys[it["id"]] for it in res["items"])
    both = e.search({"traits": [{"field": key[0], "value": key[1]},
                                {"field": "abroad", "value": "Oui"}]})
    assert {it["id"] for it in both["items"]} <= {it["id"] for it in res["items"]}
    assert e.search({"traits": [{"field": "night", "value": "jamais"}]})["total"] == 0


def test_ai_index_build_survives_timeouts(store, monkeypatch):
    """Batches that time out are retried, split, and a text that always fails is skipped."""
    db, _ = store
    e = Engine(db)
    import zivi.semantic as sem
    calls = []

    def flaky_embed(self, texts):
        calls.append(len(texts))
        if len(texts) > 2:
            raise sem.BatchFailed("pas de réponse du service (ReadTimeout)")
        if any("alpages" in t.lower() for t in texts):  # one record is "too long"
            raise sem.BatchFailed("Erreur de l'API d'embeddings 400 : input too long")
        return np.ones((len(texts), 3), dtype=np.float32) / np.sqrt(3)

    monkeypatch.setattr(sem.Embedder, "embed", flaky_embed)
    e.ai.base_url, e.ai.model, e.ai.api_key = "http://localhost:11434/v1", "nomic", ""
    e.semantic.build(str(db), e.ai_texts(), e.ai, pause=0)
    p = e.semantic.progress
    assert p["error"] is None and not p["running"]
    assert p["skipped"] == 1 and p["done"] == 4 and p["batch"] == 1
    assert e.semantic.ready and len(e.semantic.ids) == 4
    assert calls[0] == 5  # started with the whole set, then smaller batches

    # a stopped build keeps what it already did, and the next build only does the rest
    monkeypatch.setattr(sem.Embedder, "embed",
                        lambda self, t: np.ones((len(t), 3), dtype=np.float32))
    e.semantic.build(str(db), e.ai_texts(), e.ai, pause=0)
    assert e.semantic.progress["total"] == 1 and len(e.semantic.ids) == 5


def test_translation_roundtrip(store):
    db, _ = store
    client = TestClient(create_app(db))
    stats = client.get("/api/translate").json()
    assert stats["done"] == 0 and stats["texts"] > 0
    todo = client.get("/api/translate/todo", params={"limit": 500}).json()["items"]
    assert len(todo) == stats["texts"]
    # the page would use the browser translator; here every text gets a recognisable word
    items = [{"hash": t["hash"], "text": f"FR zébrure {t['text']}"} for t in todo]
    items.append({"hash": "unknown", "text": "ignored"})
    assert client.post("/api/translate/save", json={"items": items}).json()["saved"] == len(todo)
    stats = client.post("/api/translate/finish").json()
    assert stats["done"] == stats["texts"] and stats["assignments_done"] == stats["assignments"]
    assert client.get("/api/translate/todo").json()["items"] == []

    found = client.post("/api/search", json={"text": "zebrure"}).json()  # accents ignored
    assert found["total"] == stats["assignments"]
    fr_view = client.post("/api/search", json={"view_lang": "fr", "limit": 50}).json()
    translated = [it for it in fr_view["items"] if it.get("title_orig")]
    assert translated and all(it["title"].startswith("FR ") for it in translated)
    assert any(p[3].startswith("FR ") for p in fr_view["points"])
    d = client.get(f"/api/assignment/{translated[0]['id']}").json()
    assert d["tr"]["title"].startswith("FR ")

    # a word only in the original text: found by default, not when the original is excluded
    word = translated[0]["title_orig"].split()[0]
    with_orig = client.post("/api/search", json={"text": f'"{word}"'}).json()["total"]
    fr_only = client.post("/api/search", json={"text": f'"{word}"',
                                               "search_original": False}).json()["total"]
    assert with_orig >= 1 and fr_only <= with_orig

    # the translations survive a new engine (restart) and can be deleted
    assert Engine(db).translation_stats()["done"] == stats["texts"]
    assert client.delete("/api/translate").json()["done"] == 0


def test_translation_failures_are_set_aside_then_retried(store):
    db, _ = store
    client = TestClient(create_app(db))
    todo = client.get("/api/translate/todo", params={"limit": 500}).json()["items"]
    bad, good = todo[0], todo[1:]
    client.post("/api/translate/save", json={"items": [{"hash": bad["hash"], "failed": True}] +
                                         [{"hash": t["hash"], "text": "ok"} for t in good]})
    # nothing left to hand out in this run, the failure is counted
    assert client.get("/api/translate/todo").json()["items"] == []
    assert client.get("/api/translate").json()["failed"] == 1
    # a new run tries it again, and a success clears it
    assert client.post("/api/translate/retry").json()["failed"] == 0
    assert [t["hash"] for t in client.get("/api/translate/todo").json()["items"]] == [bad["hash"]]
    client.post("/api/translate/save", json={"items": [{"hash": bad["hash"], "text": "ok"}]})
    st = client.get("/api/translate").json()
    assert st["done"] == st["texts"] and st["failed"] == 0


# ---------------------------------------------------------------- Ollama

class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_ollama_translate_keeps_lines(monkeypatch):
    import zivi.ollama as ol
    prompts = []

    def fake_post(url, json=None, **kw):
        prompts.append(json["prompt"])
        text = json["prompt"].split("\n\n\n", 1)[1]
        if "3 lines" in json["prompt"]:  # the model merges the lines: per-line fallback
            return FakeResponse(200, {"response": "tout sur une ligne"})
        return FakeResponse(200, {"response": "\n".join("FR " + ln for ln in text.split("\n"))})

    monkeypatch.setattr(ol.httpx, "post", fake_post)
    assert ol.translate("h", "m", "Alp-Pflege", "de") == "FR Alp-Pflege"
    assert "German (de) to French (fr)" in prompts[-1]
    out = ol.translate("h", "m", "Eins\n\nZwei\nDrei", "de")
    assert out == "FR Eins\n\nFR Zwei\nFR Drei"  # blank line kept, one call per line after merge
    assert ol.translate("h", "m", "Ciao\nMondo", "it") == "FR Ciao\nFR Mondo"
    assert "Italian (it)" in prompts[-1] and "2 lines" in prompts[-1]

    monkeypatch.setattr(ol.httpx, "post", lambda *a, **k: FakeResponse(404, {"error": "model"}))
    with pytest.raises(RuntimeError, match="pas installé"):
        ol.translate("h", "absent", "Hallo", "de")


def test_ollama_translation_job(store, monkeypatch):
    db, _ = store
    import zivi.ollama as ol
    e = Engine(db)
    always_fails = e.translation_todo(1)[0]["text"]

    def fake_translate(host, model, text, source, timeout=600.0, target="fr"):
        if text == always_fails:  # this one always times out
            raise ol.TranslateFailed("pas de réponse d'Ollama (ReadTimeout)")
        return "FR " + text

    monkeypatch.setattr(ol, "translate", fake_translate)
    job = ol.TranslationJob()
    job.run("h", "translategemma:4b", e.translation_todo, e.save_translations,
            e.rebuild_fts_tr, pause=0)
    p = job.progress
    st = e.translation_stats()
    assert p["error"] is None and not p["running"] and p["skipped"] >= 1
    assert st["done"] == st["texts"] - st["failed"] and st["failed"] == p["skipped"]
    assert p["done"] == st["done"]


def test_ollama_status_and_host():
    from zivi.ollama import Ollama, host_from, same_model
    assert host_from("http://localhost:11434/v1") == "http://127.0.0.1:11434"
    assert same_model("nomic-embed-text", "nomic-embed-text:latest")
    st = Ollama().status("http://127.0.0.1:9")  # nothing listens there
    assert st["running"] is False and st["models"] == [] and st["download_url"]


def test_ai_index_per_language(store, monkeypatch):
    """Index on the text translated into the interface language: one set of vectors per
    language, and switching language never erases another one."""
    db, _ = store
    import zivi.semantic as sem
    monkeypatch.setattr(sem.Embedder, "embed",
                        lambda self, t: np.ones((len(t), 3), dtype=np.float32))
    client = TestClient(create_app(db))
    client.post("/api/ai/config", json={"base_url": "http://localhost:11434/v1",
                                        "model": "nomic", "lang": "fr"})
    e = Engine(db)
    e.semantic.build(str(db), e.ai_texts(), e.ai, pause=0, key=e.ai_index_key)
    info = client.get("/api/ai").json()
    assert info["pending"] == 0 and info["key"] == "nomic" and info["translated"] is False

    # translate into French and into German
    for lang, prefix in (("fr", "FR "), ("de", "DE ")):
        todo = client.get("/api/translate/todo", params={"limit": 500, "lang": lang}).json()
        client.post("/api/translate/save", json={"lang": lang, "items": [
            {"hash": t["hash"], "text": prefix + t["text"]} for t in todo["items"]]})
    assert client.get("/api/ai").json()["pending"] == 0  # option off: nothing changes

    # index in the interface language: French first
    client.post("/api/ai/config", json={"translated": True})
    info = client.get("/api/ai").json()
    cov = info["coverage"]
    assert info["key"] == "nomic@fr" and cov["native"] + cov["translated"] == cov["total"]
    assert info["pending"] == cov["total"]  # a new index for this language
    e = Engine(db)
    e.semantic.build(str(db), e.ai_texts(), e.ai, pause=0, key=e.ai_index_key)
    assert all(t.startswith("FR ") for i, t in e.ai_texts().items()
               if e.rows[i].get("language") != "fr")

    # switch the interface to German: its own index, the French and original ones stay
    client.post("/api/prefs", json={"lang": "de"})
    info = client.get("/api/ai").json()
    assert info["key"] == "nomic@de" and info["pending"] == info["coverage"]["total"]
    assert set(info["built"]) == {"nomic", "nomic@fr"}
    e = Engine(db)
    assert e.ui_lang == "de" and e.ai_translated is True  # both saved
    e.semantic.build(str(db), e.ai_texts(), e.ai, pause=0, key=e.ai_index_key)
    assert set(client.get("/api/ai").json()["built"]) == {"nomic", "nomic@fr", "nomic@de"}

    # deleting the German index keeps the others
    client.delete("/api/ai/index")
    assert set(client.get("/api/ai").json()["built"]) == {"nomic", "nomic@fr"}


def test_translations_per_language(store):
    db, _ = store
    client = TestClient(create_app(db))
    st_fr = client.get("/api/translate", params={"lang": "fr"}).json()
    st_de = client.get("/api/translate", params={"lang": "de"}).json()
    # German assignments need no German translation, French ones need none into French
    assert st_fr["target"] == "fr" and st_de["target"] == "de"
    for lang, prefix in (("fr", "FR zébrure "), ("de", "DE Zebrastreifen ")):
        todo = client.get("/api/translate/todo", params={"limit": 500, "lang": lang}).json()
        client.post("/api/translate/save", json={"lang": lang, "items": [
            {"hash": t["hash"], "text": prefix + t["text"]} for t in todo["items"]]})
        client.post("/api/translate/finish", params={"lang": lang})
    # each language has its own keyword index and its own view
    fr = client.post("/api/search", json={"text": "zebrure", "lang": "fr"}).json()["total"]
    de = client.post("/api/search", json={"text": "zebrastreifen", "lang": "de"}).json()["total"]
    assert fr == st_fr["assignments"] and de == st_de["assignments"]
    view = client.post("/api/search", json={"view_lang": "de", "lang": "de"}).json()
    assert any(it["title"].startswith("DE ") for it in view["items"])
    one = next(it for it in view["items"] if it.get("title_orig"))
    assert client.get(f"/api/assignment/{one['id']}", params={"lang": "de"}).json()["tr"]
    # deleting the German translations keeps the French ones
    client.delete("/api/translate", params={"lang": "de"})
    assert client.get("/api/translate", params={"lang": "de"}).json()["done"] == 0
    assert client.get("/api/translate", params={"lang": "fr"}).json()["done"] == st_fr["texts"]


def test_messages_are_codes(store):
    db, _ = store
    client = TestClient(create_app(db))
    r = client.get("/api/assignment/999999")
    assert r.status_code == 404 and r.json()["detail"]["key"] == "unknown_assignment"
    w = client.post("/api/search", json={"text": "x", "text_mode": "semantic"}).json()["warnings"]
    assert w and all("key" in m and "text" in m for m in w)
    labels = client.get("/api/labels").json()
    assert isinstance(labels, dict)


def test_keyword_search_covers_metadata(store):
    db, _ = store
    import sqlite3
    con = sqlite3.connect(db)
    a = con.execute("SELECT id, number, contact_name, contact_email FROM assignments "
                    "WHERE contact_name IS NOT NULL LIMIT 1").fetchone()
    con.execute("UPDATE assignments SET contact_phone='079 123 45 67' WHERE id=?", (a[0],))
    # an old database: keyword index without the metadata columns
    con.execute("DROP TABLE fts")
    con.execute("CREATE VIRTUAL TABLE fts USING fts5(title, employer, place, tasks, skills, "
                "employer_description, courses, task_areas)")
    con.commit()
    con.close()
    e = Engine(db)  # upgrades the index in place
    ids_of = lambda q: [it["id"] for it in e.search({"text": q})["items"]]  # noqa: E731
    last_name = a[2].split()[-1]
    assert a[0] in ids_of(last_name)
    assert a[0] in ids_of("0791234567") and a[0] in ids_of('"079 123 45 67"')
    from zivi.converter import fts_metadata
    assert "0419806183" in fts_metadata({"contact_phone": "+41 41 980 61 83"}, [])[2]
    from zivi.search import phone_query
    for typed in ("041 980 61 83", "+41 41 980 61 83", '"0041 41 980 61 83"', "041/980.61.83"):
        assert phone_query(typed) == "0419806183"
    assert phone_query("alpage 2026") == "alpage 2026" and phone_query("18470") == "18470"
    assert ids_of(str(a[1])) == [a[0]]
    # words from a description field and from the metadata combine
    title_word = e.rows[a[0]]["title"].split()[0]
    assert a[0] in ids_of(f"{title_word} {last_name}")
