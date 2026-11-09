"""Optional semantic search with embeddings from any OpenAI-compatible endpoint.

The API key is only kept in memory (set through the UI or the ZIVI_AI_API_KEY environment
variable) and is never written to disk. Vectors are stored in the ``embeddings`` table.
Works with OpenAI, Mistral, Azure OpenAI (v1-compatible), or a local Ollama
(base_url http://localhost:11434/v1, model e.g. nomic-embed-text, any key).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass, field

import httpx
import numpy as np

from . import config
from .converter import EMBEDDINGS_SQL

MAX_CHARS = 6000  # keep each text well under typical 8k-token limits


@dataclass
class AISettings:
    base_url: str = config.AI_BASE_URL
    model: str = config.AI_MODEL
    api_key: str = field(default=config.AI_API_KEY, repr=False)

    @property
    def configured(self) -> bool:
        local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        return bool(self.base_url and self.model and (self.api_key or local))

    def public(self) -> dict:
        key = self.api_key
        return {"base_url": self.base_url, "model": self.model, "configured": self.configured,
                "key_hint": f"…{key[-4:]}" if len(key) > 8 else ("set" if key else "")}


def assignment_text(row: dict, task_areas: list[str]) -> str:
    parts = [row.get("title"), row.get("employer"), row.get("place"),
             ", ".join(task_areas), row.get("tasks"), row.get("required_skills"),
             row.get("desired_skills"), row.get("employer_description")]
    return "\n".join(p for p in parts if p)[:MAX_CHARS]


class Embedder:
    def __init__(self, settings: AISettings):
        self.s = settings

    def embed(self, texts: list[str]) -> np.ndarray:
        headers = {"Content-Type": "application/json"}
        if self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        url = self.s.base_url.rstrip("/") + "/embeddings"
        r = httpx.post(url, headers=headers, timeout=120,
                       json={"model": self.s.model, "input": texts})
        if r.status_code >= 400:
            raise RuntimeError(f"Embedding API error {r.status_code}: {r.text[:300]}")
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        vecs = np.array([d["embedding"] for d in data], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-12)


class SemanticIndex:
    """Vectors for one model, loaded in memory for fast cosine search."""

    def __init__(self):
        self.model: str | None = None
        self.ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self.mat: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.progress = {"running": False, "done": 0, "total": 0, "error": None}
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return len(self.ids) > 0

    def load(self, db: sqlite3.Connection, model: str) -> None:
        db.execute(EMBEDDINGS_SQL)
        rows = db.execute("SELECT e.assignment_id, e.vec FROM embeddings e "
                          "JOIN assignments a ON a.id = e.assignment_id WHERE e.model=?",
                          (model,)).fetchall()
        with self._lock:
            self.model = model
            if rows:
                self.ids = np.array([r[0] for r in rows], dtype=np.int64)
                self.mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            else:
                self.ids = np.zeros(0, dtype=np.int64)
                self.mat = np.zeros((0, 0), dtype=np.float32)

    def build(self, db_path: str, texts: dict[int, str], settings: AISettings,
              batch: int = 64) -> None:
        """Embed every assignment whose text changed since the last build (runs in a thread)."""
        self.progress = {"running": True, "done": 0, "total": 0, "error": None}
        try:
            db = sqlite3.connect(db_path)
            db.execute(EMBEDDINGS_SQL)
            have = {r[0]: r[1] for r in db.execute(
                "SELECT assignment_id, text_hash FROM embeddings WHERE model=?",
                (settings.model,))}
            todo = []
            for aid, text in texts.items():
                h = hashlib.sha1(text.encode()).hexdigest()
                if have.get(aid) != h:
                    todo.append((aid, text, h))
            self.progress["total"] = len(todo)
            emb = Embedder(settings)
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                vecs = np.asarray(emb.embed([t for _, t, _ in chunk]), dtype=np.float32)
                vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
                db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                               [(aid, settings.model, h, v.tobytes())
                                for (aid, _, h), v in zip(chunk, vecs)])
                db.commit()
                self.progress["done"] = i + len(chunk)
            self.load(db, settings.model)
            db.close()
        except Exception as exc:  # reported to the UI
            self.progress["error"] = str(exc)
        finally:
            self.progress["running"] = False

    def scores(self, query: str, settings: AISettings) -> dict[int, float]:
        """Cosine similarity of every indexed assignment to the query."""
        if not self.ready:
            return {}
        q = np.asarray(Embedder(settings).embed([query])[0], dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        with self._lock:
            if self.mat.shape[1] != q.shape[0]:
                raise RuntimeError(f"the index was built with another model ({self.model}); "
                                   "rebuild it in AI settings")
            sims = self.mat @ q
            return dict(zip(self.ids.tolist(), sims.tolist()))
