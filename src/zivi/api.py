"""FastAPI app: JSON API + the static web UI."""

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
from .ollama import Ollama, TranslationJob, host_from
from .search import Engine

STATIC = Path(__file__).parent / "static"


def create_app(db_path: Path = config.DB_PATH) -> FastAPI:
    app = FastAPI(title="Zivi search", version="0.1.0")
    engine = Engine(db_path)
    ollama = Ollama()
    tr_job = TranslationJob()
    state = {"import": {"running": False, "message": "", "progress": 0.0, "error": None,
                        "report": None, "started": None}}
    geo: dict = {}

    def geocoder() -> Geocoder:
        if "g" not in geo:
            db = sqlite3.connect(":memory:", check_same_thread=False)
            geo["g"] = Geocoder(db, table=PostcodeTable())
        return geo["g"]

    # ------------------------------------------------------------------ data
    @app.get("/api/stats")
    def stats():
        return {**engine.stats(), "import": state["import"]}

    @app.post("/api/search")
    def search(q: dict = Body(default={})):
        if not engine.loaded:
            return {"total": 0, "items": [], "points": [], "facets": {}, "unlocated": 0,
                    "warnings": ["Aucune donnée : chargez d'abord un fichier JSON."]}
        return engine.search(q)

    @app.get("/api/assignment/{aid}")
    def assignment(aid: int):
        d = engine.detail(aid)
        if not d:
            raise HTTPException(404, "Affectation inconnue")
        return d

    @app.get("/api/favorites")
    def favorites():
        return {"ids": sorted(engine.favorites)}

    @app.post("/api/favorites/{aid}")
    def set_favorite(aid: int, body: dict = Body(default={})):
        if aid not in engine.rows:
            raise HTTPException(404, "Affectation inconnue")
        on = engine.set_favorite(aid, bool(body.get("favorite", True)))
        return {"id": aid, "favorite": on, "count": len(engine.favorites & set(engine.rows))}

    @app.delete("/api/favorites")
    def clear_favorites():
        n = engine.clear_favorites()
        return {"cleared": n}

    # ------------------------------------------------------------------ translation
    # The page translates with the browser's on-device translator and sends the results
    # here; the server only hands out the texts still to do and stores the answers.
    @app.get("/api/translate")
    def translate_stats():
        return engine.translation_stats()

    @app.get("/api/translate/todo")
    def translate_todo(limit: int = 30):
        return {"items": engine.translation_todo(max(1, min(limit, 200)))}

    @app.post("/api/translate/save")
    def translate_save(body: dict = Body(...)):
        saved = engine.save_translations(body.get("items") or [])
        return {"saved": saved}

    # translation through a local Ollama model, on the server: keeps going if the page closes
    @app.get("/api/translate/job")
    def translate_job():
        return {**tr_job.progress, "model": engine.tr_model, "host": engine.ollama_host}

    @app.post("/api/translate/job")
    def translate_job_start(body: dict = Body(default={})):
        if tr_job.progress.get("running"):
            raise HTTPException(409, "La traduction est déjà en cours")
        if body.get("model"):
            engine.tr_model = str(body["model"]).strip()
        if body.get("host"):
            engine.ollama_host = host_from(body["host"])
        engine.save_ai_settings()
        engine.retry_failed_translations()  # texts set aside earlier get another chance
        threading.Thread(target=tr_job.run, daemon=True, args=(
            engine.ollama_host, engine.tr_model, engine.translation_todo,
            engine.save_translations, engine.rebuild_fts_fr)).start()
        return {"started": True}

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

    # ------------------------------------------------------------------ Ollama
    @app.get("/api/ollama")
    def ollama_status(host: str | None = None):
        return ollama.status(host_from(host) if host else engine.ollama_host)

    @app.post("/api/ollama/start")
    def ollama_start():
        if not ollama.start():
            raise HTTPException(404, "Ollama n'est pas installé sur cet ordinateur")
        return {"started": True}

    @app.post("/api/ollama/pull")
    def ollama_pull(body: dict = Body(...)):
        model = str(body.get("model") or "").strip()
        if not model:
            raise HTTPException(400, "Indiquez un modèle")
        ollama.pull(host_from(body.get("host")) if body.get("host") else engine.ollama_host,
                    model)
        return ollama.pulls[model]

    @app.post("/api/translate/retry")
    def translate_retry():
        engine.retry_failed_translations()  # a new run tries the set-aside texts again
        return engine.translation_stats()

    @app.post("/api/translate/finish")
    def translate_finish():
        engine.rebuild_fts_fr(force=True)  # make the new French text searchable now
        return engine.translation_stats()

    @app.delete("/api/translate")
    def translate_clear():
        engine.clear_translations()
        return engine.translation_stats()

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

        def progress(msg: str, frac: float):
            st["message"], st["progress"] = msg, frac

        try:
            report = convert(path, db_path, progress=progress)
            engine.load()
            geo.clear()
            st["report"] = report
        except Exception as exc:
            st["error"] = str(exc)
            st["message"] = "Échec de l'import"
        finally:
            st["running"] = False

    @app.post("/api/import")
    async def import_json(file: UploadFile = File(...)):
        st = state["import"]
        if st["running"]:
            raise HTTPException(409, "Un import est déjà en cours")
        uploads = config.DATA_DIR / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        name = Path(file.filename or "upload.json").name
        dest = uploads / name
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        st.update({"running": True, "message": "Démarrage", "progress": 0.0, "error": None,
                   "report": None, "started": time.time(), "file": name})
        threading.Thread(target=run_import, args=(dest,), daemon=True).start()
        return {"started": True, "file": name}

    @app.get("/api/import/status")
    def import_status():
        return state["import"]

    # ------------------------------------------------------------------ AI
    @app.get("/api/ai")
    def ai_get():
        out = engine.stats()["ai_index"] | {"settings": engine.ai.public()}
        out["french"] = engine.ai_french
        out["coverage"] = engine.ai_french_coverage()
        if not out.get("running"):
            db = engine.connect()
            try:  # what "Construire l'index" would do now
                out["pending"] = engine.semantic.pending(db, engine.ai_texts(), engine.ai.model)
            finally:
                db.close()
        return out

    @app.post("/api/ai/config")
    def ai_config(body: dict = Body(...)):
        s = engine.ai
        if body.get("base_url"):
            s.base_url = body["base_url"].strip()
        if body.get("model"):
            new_model = body["model"].strip()
            if new_model != s.model:
                s.model = new_model
                db = engine.connect()
                engine.semantic.load(db, s.model)
                db.close()
        if "api_key" in body and body["api_key"] is not None:
            s.api_key = body["api_key"].strip()  # kept in memory only
        if "french" in body:
            engine.ai_french = bool(body["french"])
        engine.save_ai_settings()  # URL, model and options, never the token
        return {"settings": s.public(), "index_ready": engine.semantic.ready}

    @app.post("/api/ai/test")
    def ai_test():
        from .semantic import Embedder
        if not engine.ai.configured:
            raise HTTPException(400, "Indiquez d'abord une URL de base, un modèle et un jeton")
        try:
            v = Embedder(engine.ai).embed(["test"])
            return {"ok": True, "dimensions": int(v.shape[1])}
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/ai/index")
    def ai_index(body: dict = Body(default={})):
        if not engine.ai.configured:
            raise HTTPException(400, "Indiquez d'abord une URL de base, un modèle et un jeton")
        if engine.semantic.progress.get("running"):
            raise HTTPException(409, "Construction déjà en cours")
        if body.get("rebuild"):  # start from scratch instead of only the missing ones
            db = engine.connect()
            try:
                engine.semantic.clear(db, engine.ai.model)
            finally:
                db.close()
        texts = engine.ai_texts()
        settings = engine.ai
        threading.Thread(target=engine.semantic.build,
                         args=(str(db_path), texts, settings), daemon=True).start()
        return {"started": True, "texts": len(texts)}

    @app.post("/api/ai/stop")
    def ai_stop():
        engine.semantic.cancel = True  # the build stops after the current batch
        return {"stopping": bool(engine.semantic.progress.get("running"))}

    @app.delete("/api/ai/index")
    def ai_index_delete():
        if engine.semantic.progress.get("running"):
            raise HTTPException(409, "Construction en cours : arrêtez-la d'abord")
        db = engine.connect()
        try:
            engine.semantic.clear(db, engine.ai.model)
        finally:
            db.close()
        return engine.stats()["ai_index"]

    # ------------------------------------------------------------------ UI
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
