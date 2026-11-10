"""FastAPI app: JSON API + the static web UI.

Messages meant for people (warnings, errors, progress) are sent as {"key", "params",
"text"} (see messages.py) so the page shows them in the interface language.
"""

from __future__ import annotations

import csv
import io
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .converter import convert
from .geocode import Geocoder, PostcodeTable
from .messages import as_msg, msg
from .ollama import Ollama, PageWatch, TranslationJob, host_from
from .search import LANGS, Engine

STATIC = Path(__file__).parent / "static"


def fail(status: int, key: str, **params) -> HTTPException:
    return HTTPException(status, msg(key, **params))


def create_app(db_path: Path = config.DB_PATH) -> FastAPI:
    app = FastAPI(title="Better Zivi", version="0.1.0")
    engine = Engine(db_path)
    ollama = Ollama()
    tr_job = TranslationJob()
    # Ollama is freed once the page is gone (unless a translation or AI build still runs)
    watch = PageWatch(ollama, host=lambda: engine.ollama_host,
                      enabled=lambda: engine.ollama_auto_stop,
                      busy=lambda: bool(tr_job.progress.get("running")
                                        or engine.semantic.progress.get("running")),
                      # background tabs only run their timers about once a minute
                      grace=90)
    state = {"import": {"running": False, "message": "", "progress": 0.0, "error": None,
                        "report": None, "started": None}}
    geo: dict = {}

    def geocoder() -> Geocoder:
        if "g" not in geo:
            db = sqlite3.connect(":memory:", check_same_thread=False)
            geo["g"] = Geocoder(db, table=PostcodeTable())
        return geo["g"]

    def lang_of(value: str | None) -> str:
        """A translation target given by the page, else the interface language."""
        return value if value in LANGS else engine.ui_lang

    # ------------------------------------------------------------------ data
    @app.get("/api/stats")
    def stats():
        return {**engine.stats(), "import": state["import"]}

    @app.get("/api/labels")
    def labels():
        """French label of a coded value -> {de, it} (sectors, task types…)."""
        return engine.labels

    @app.post("/api/prefs")
    def prefs(body: dict = Body(...)):
        """The interface language: which translations and which AI index to use."""
        if body.get("lang") in LANGS:
            engine.set_ui_lang(body["lang"])
        return {"lang": engine.ui_lang}

    @app.post("/api/search")
    def search(q: dict = Body(default={})):
        if not engine.loaded:
            return {"total": 0, "items": [], "points": [], "facets": {}, "unlocated": 0,
                    "warnings": [msg("no_data")]}
        return engine.search(q)

    @app.get("/api/assignment/{aid}")
    def assignment(aid: int, lang: str | None = None):
        d = engine.detail(aid, lang_of(lang))
        if not d:
            raise fail(404, "unknown_assignment")
        return d

    @app.get("/api/favorites")
    def favorites():
        return {"ids": sorted(engine.favorites)}

    @app.post("/api/favorites/{aid}")
    def set_favorite(aid: int, body: dict = Body(default={})):
        if aid not in engine.rows:
            raise fail(404, "unknown_assignment")
        on = engine.set_favorite(aid, bool(body.get("favorite", True)))
        return {"id": aid, "favorite": on, "count": len(engine.favorites & set(engine.rows))}

    @app.delete("/api/favorites")
    def clear_favorites():
        n = engine.clear_favorites()
        return {"cleared": n}

    # ------------------------------------------------------------------ translation
    # Into the interface language (or ?lang=…). With the browser engine the page translates
    # and sends the results here; with Ollama the server runs the job itself.
    @app.get("/api/translate")
    def translate_stats(lang: str | None = None):
        return engine.translation_stats(lang_of(lang))

    @app.get("/api/translate/todo")
    def translate_todo(limit: int = 30, lang: str | None = None):
        return {"items": engine.translation_todo(max(1, min(limit, 200)), lang_of(lang))}

    @app.post("/api/translate/save")
    def translate_save(body: dict = Body(...)):
        saved = engine.save_translations(body.get("items") or [], lang_of(body.get("lang")))
        return {"saved": saved}

    @app.get("/api/translate/job")
    def translate_job():
        return {**tr_job.progress, "model": engine.tr_model, "host": engine.ollama_host}

    @app.post("/api/translate/job")
    def translate_job_start(body: dict = Body(default={})):
        if tr_job.progress.get("running"):
            raise fail(409, "tr_running")
        if body.get("model"):
            engine.tr_model = str(body["model"]).strip()
        if body.get("host"):
            engine.ollama_host = host_from(body["host"])
        target = lang_of(body.get("lang"))
        engine.save_ai_settings()
        engine.retry_failed_translations(target)  # texts set aside earlier get another chance
        threading.Thread(target=tr_job.run, daemon=True, kwargs={
            "host": engine.ollama_host, "model": engine.tr_model, "target": target,
            "todo": lambda n: engine.translation_todo(n, target),
            "save": lambda items: engine.save_translations(items, target),
            "refresh": lambda: engine.rebuild_fts_tr(target)}).start()
        return {"started": True, "target": target}

    @app.post("/api/translate/job/stop")
    def translate_job_stop():
        tr_job.cancel = True  # stops after the text in progress
        return {"stopping": bool(tr_job.progress.get("running"))}

    @app.post("/api/translate/settings")
    def translate_settings(body: dict = Body(...)):
        if body.get("model"):
            engine.tr_model = str(body["model"]).strip()
        engine.save_ai_settings()
        return {"model": engine.tr_model, "host": engine.ollama_host}

    @app.post("/api/translate/retry")
    def translate_retry(lang: str | None = None):
        engine.retry_failed_translations(lang_of(lang))
        return engine.translation_stats(lang_of(lang))

    @app.post("/api/translate/finish")
    def translate_finish(lang: str | None = None):
        engine.rebuild_fts_tr(lang_of(lang), force=True)  # searchable right away
        return engine.translation_stats(lang_of(lang))

    @app.delete("/api/translate")
    def translate_clear(lang: str | None = None):
        engine.clear_translations(lang_of(lang))  # only this language
        return engine.translation_stats(lang_of(lang))

    # ------------------------------------------------------------------ Ollama
    @app.get("/api/ollama")
    def ollama_status(host: str | None = None):
        return {**ollama.status(host_from(host) if host else engine.ollama_host),
                "auto_stop": engine.ollama_auto_stop}

    @app.post("/api/alive")
    def alive():
        """Sent by the page every 20 s: someone is using the app."""
        watch.alive()
        return {"ok": True}

    @app.post("/api/leave")
    def leave():
        """Sent by the page when it closes (navigator.sendBeacon)."""
        watch.left()
        return {"ok": True}

    @app.post("/api/ollama/auto-stop")
    def ollama_auto_stop(body: dict = Body(...)):
        engine.ollama_auto_stop = bool(body.get("on"))
        engine.save_ai_settings()
        return {"auto_stop": engine.ollama_auto_stop}

    @app.post("/api/ollama/start")
    def ollama_start():
        if not ollama.start():
            raise fail(404, "ollama_missing")
        return {"started": True}

    @app.post("/api/ollama/pull")
    def ollama_pull(body: dict = Body(...)):
        model = str(body.get("model") or "").strip()
        if not model:
            raise fail(400, "model_required")
        ollama.pull(host_from(body.get("host")) if body.get("host") else engine.ollama_host,
                    model)
        return ollama.pulls[model]

    @app.get("/api/geocode")
    def geocode(q: str):
        return {"results": geocoder().search_place(q)}

    @app.post("/api/export")
    def export(q: dict = Body(default={})):
        ids_order: list[int] = []
        offset, total = 0, 1
        while offset < total:
            page = engine.search({**q, "offset": offset, "limit": 500})
            total = page["total"]
            ids_order += [it["id"] for it in page["items"]]
            offset += 500
        buf = io.StringIO()
        cols = ["number", "title", "employer", "place", "canton", "min_weeks", "hours_per_week",
                "weekend", "night", "housing", "meals", "reputation_check", "first_week",
                "last_week", "contact_name", "contact_phone", "contact_email", "url"]
        w = csv.writer(buf, delimiter=";")
        w.writerow(cols + ["task_areas"])
        for i in ids_order:
            d = engine.detail(i)
            w.writerow([d.get(c) for c in cols] + [", ".join(d["task_areas"])])
        data = "﻿" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly
        return StreamingResponse(iter([data]), media_type="text/csv",
                                 headers={"Content-Disposition":
                                          "attachment; filename=zivi_resultats.csv"})

    # ------------------------------------------------------------------ import
    def run_import(path: Path):
        st = state["import"]

        def progress(message: dict, frac: float):
            st["message"], st["progress"] = message, frac

        try:
            report = convert(path, db_path, progress=progress)
            engine.load()
            geo.clear()
            st["report"] = report
        except Exception as exc:
            st["error"] = as_msg(exc)
            st["message"] = msg("import_failed")
        finally:
            st["running"] = False

    @app.post("/api/import")
    async def import_json(file: UploadFile = File(...)):
        st = state["import"]
        if st["running"]:
            raise fail(409, "import_running")
        uploads = config.DATA_DIR / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "upload.json").name
        dest = uploads / name
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        st.update({"running": True, "message": msg("import_start"), "progress": 0.0,
                   "error": None, "report": None, "started": time.time(), "file": name})
        threading.Thread(target=run_import, args=(dest,), daemon=True).start()
        return {"started": True, "file": name}

    @app.get("/api/import/status")
    def import_status():
        return state["import"]

    # ------------------------------------------------------------------ AI
    @app.get("/api/ai")
    def ai_get():
        out = engine.stats()["ai_index"] | {"settings": engine.ai.public()}
        out["translated"] = engine.ai_translated
        out["lang"] = engine.ui_lang
        out["key"] = engine.ai_index_key
        out["coverage"] = engine.ai_coverage(engine.ui_lang)
        if not out.get("running"):
            db = engine.connect()
            try:  # what "Construire l'index" would do now
                out["pending"] = engine.semantic.pending(db, engine.ai_texts(),
                                                         engine.ai_index_key)
                # every language already built for this model (none is ever erased)
                out["built"] = {k: n for k, n in db.execute(
                    "SELECT model, COUNT(*) FROM embeddings WHERE model=? OR model LIKE ? "
                    "GROUP BY model", (engine.ai.model, f"{engine.ai.model}@%"))}
            finally:
                db.close()
        return out

    @app.post("/api/ai/config")
    def ai_config(body: dict = Body(...)):
        s = engine.ai
        if body.get("base_url"):
            s.base_url = body["base_url"].strip()
        if body.get("model"):
            s.model = body["model"].strip()
        if "api_key" in body and body["api_key"] is not None:
            s.api_key = body["api_key"].strip()  # kept in memory only
        if "translated" in body:
            engine.ai_translated = bool(body["translated"])
        if body.get("lang") in LANGS:
            engine.ui_lang = body["lang"]
        engine.save_ai_settings()  # URL, model and options, never the token
        engine.reload_ai_index()  # the vectors of this model and language, if built
        return {"settings": s.public(), "index_ready": engine.semantic.ready}

    @app.post("/api/ai/test")
    def ai_test():
        from .semantic import Embedder
        if not engine.ai.configured:
            raise fail(400, "ai_setup_first")
        try:
            v = Embedder(engine.ai).embed(["test"])
            return {"ok": True, "dimensions": int(v.shape[1])}
        except Exception as exc:
            raise HTTPException(400, as_msg(exc))

    @app.post("/api/ai/index")
    def ai_index(body: dict = Body(default={})):
        if not engine.ai.configured:
            raise fail(400, "ai_setup_first")
        if engine.semantic.progress.get("running"):
            raise fail(409, "ai_building")
        key = engine.ai_index_key
        if body.get("rebuild"):  # start from scratch, for this model and language only
            db = engine.connect()
            try:
                engine.semantic.clear(db, key)
            finally:
                db.close()
        texts = engine.ai_texts()
        threading.Thread(target=engine.semantic.build, daemon=True,
                         args=(str(db_path), texts, engine.ai), kwargs={"key": key}).start()
        return {"started": True, "texts": len(texts), "key": key}

    @app.post("/api/ai/stop")
    def ai_stop():
        engine.semantic.cancel = True  # the build stops after the current batch
        return {"stopping": bool(engine.semantic.progress.get("running"))}

    @app.delete("/api/ai/index")
    def ai_index_delete():
        if engine.semantic.progress.get("running"):
            raise fail(409, "ai_stop_first")
        db = engine.connect()
        try:
            engine.semantic.clear(db, engine.ai_index_key)  # this language only
        finally:
            db.close()
        return engine.stats()["ai_index"]

    # ------------------------------------------------------------------ UI
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
