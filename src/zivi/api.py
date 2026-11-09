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
from .search import Engine

STATIC = Path(__file__).parent / "static"


def create_app(db_path: Path = config.DB_PATH) -> FastAPI:
    app = FastAPI(title="Zivi search", version="0.1.0")
    engine = Engine(db_path)
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
        return engine.stats()["ai_index"] | {"settings": engine.ai.public()}

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
        engine.save_ai_settings()  # URL and model only, never the token
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
    def ai_index():
        if not engine.ai.configured:
            raise HTTPException(400, "Indiquez d'abord une URL de base, un modèle et un jeton")
        if engine.semantic.progress.get("running"):
            raise HTTPException(409, "Construction déjà en cours")
        texts = engine.ai_texts()
        settings = engine.ai
        threading.Thread(target=engine.semantic.build,
                         args=(str(db_path), texts, settings), daemon=True).start()
        return {"started": True, "texts": len(texts)}

    # ------------------------------------------------------------------ UI
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
