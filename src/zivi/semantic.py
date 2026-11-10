"""Optional semantic search with embeddings from any OpenAI-compatible endpoint.

The API key is only kept in memory (set through the UI or the ZIVI_AI_API_KEY environment
variable) and is never written to disk. Vectors are stored in the ``embeddings`` table; the
chosen base URL and model are saved in ``ai_settings.json`` next to the database.
Works with OpenAI, Mistral, Azure OpenAI (v1-compatible), or a local Ollama
(base_url http://localhost:11434/v1, model e.g. nomic-embed-text, any key).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass, field

import httpx
import numpy as np

from . import config
from .converter import EMBEDDINGS_SQL
from .messages import ZiviError, as_msg, msg

MAX_CHARS = 6000  # keep each text well under typical 8k-token limits


@dataclass
class AISettings:
    base_url: str = config.AI_BASE_URL
    model: str = config.AI_MODEL
    api_key: str = field(default=config.AI_API_KEY, repr=False)

    @property
    def local(self) -> bool:
        return "localhost" in self.base_url or "127.0.0.1" in self.base_url

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model and (self.api_key or self.local))

    def public(self) -> dict:
        key = self.api_key
        return {"base_url": self.base_url, "model": self.model, "configured": self.configured,
                "key_hint": f"…{key[-4:]}" if len(key) > 8 else ("set" if key else "")}


def assignment_text(row: dict, task_areas: list[str]) -> str:
    parts = [row.get("title"), row.get("employer"), row.get("place"),
             ", ".join(task_areas), row.get("tasks"), row.get("required_skills"),
             row.get("desired_skills"), row.get("employer_description")]
    return "\n".join(p for p in parts if p)[:MAX_CHARS]


class BatchFailed(ZiviError):
    """A batch that may succeed when smaller or later: timeout, overload, input too long."""


class Embedder:
    def __init__(self, settings: AISettings, timeout: float | None = None):
        self.s = settings
        # a local model on the CPU can take minutes for one batch; a cloud API answers fast
        self.timeout = timeout or (600.0 if settings.local else 120.0)

    def embed(self, texts: list[str]) -> np.ndarray:
        headers = {"Content-Type": "application/json"}
        if self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        # localhost -> 127.0.0.1: avoids a ~2 s IPv6 detour per request on Windows
        url = self.s.base_url.rstrip("/").replace("://localhost", "://127.0.0.1") + "/embeddings"
        try:
            r = httpx.post(url, headers=headers,
                           timeout=httpx.Timeout(self.timeout, connect=15.0),
                           json={"model": self.s.model, "input": texts})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise BatchFailed("no_response", kind=type(exc).__name__) from exc
        if r.status_code in (400, 413, 429) or r.status_code >= 500:
            # overloaded, rate-limited, or a text too long for the model's context
            raise BatchFailed("api_error", status=r.status_code, body=r.text[:300])
        if r.status_code >= 400:  # wrong token, unknown model…: retrying won't help
            raise ZiviError("api_error", status=r.status_code, body=r.text[:300])
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
        self.cancel = False
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

    def pending(self, db: sqlite3.Connection, texts: dict[int, str], model: str) -> int:
        """How many assignments the next build would (re)compute for this model."""
        db.execute(EMBEDDINGS_SQL)
        have = {r[0]: r[1] for r in db.execute(
            "SELECT assignment_id, text_hash FROM embeddings WHERE model=?", (model,))}
        return sum(1 for aid, t in texts.items()
                   if have.get(aid) != hashlib.sha1(t.encode()).hexdigest())

    def clear(self, db: sqlite3.Connection, model: str) -> None:
        """Delete the vectors of one model (the next build starts from scratch)."""
        db.execute(EMBEDDINGS_SQL)
        db.execute("DELETE FROM embeddings WHERE model=?", (model,))
        db.commit()
        self.load(db, model)

    def build(self, db_path: str, texts: dict[int, str], settings: AISettings,
              batch: int | None = None, retries: int = 3, pause: float = 3.0,
              key: str | None = None) -> None:
        """Embed every assignment whose text changed since the last build (runs in a thread).

        Each batch is saved as soon as it is done, so a stopped or failed build resumes
        where it left off. A batch that times out is retried after a pause, then split in
        two; a single text that still fails is skipped (counted in progress["skipped"])
        instead of stopping everything.
        """
        # vectors are stored under `key` (the model, or 'model@de' for an index on the text
        # translated into German); the embedding API is always called with the model
        key = key or settings.model
        size = batch or (8 if settings.local else 64)
        self.cancel = False
        self.progress = {"running": True, "done": 0, "total": 0, "error": None,
                         "skipped": 0, "batch": size, "cancelled": False, "note": None}
        db = None
        try:
            db = sqlite3.connect(db_path)
            db.execute(EMBEDDINGS_SQL)
            have = {r[0]: r[1] for r in db.execute(
                "SELECT assignment_id, text_hash FROM embeddings WHERE model=?",
                (key,))}
            todo = []
            for aid, text in texts.items():
                h = hashlib.sha1(text.encode()).hexdigest()
                if have.get(aid) != h:
                    todo.append((aid, text, h))
            self.progress["total"] = len(todo)
            emb = Embedder(settings)
            i = 0
            while i < len(todo):
                if self.cancel:
                    self.progress["cancelled"] = True
                    break
                chunk = todo[i:i + size]
                vecs = None
                for attempt in range(retries):
                    try:
                        vecs = np.asarray(emb.embed([t for _, t, _ in chunk]), dtype=np.float32)
                        self.progress["note"] = None
                        break
                    except BatchFailed as exc:
                        self.progress["note"] = msg("retry", attempt=attempt + 1, of=retries - 1,
                                                    error=as_msg(exc))
                        if attempt < retries - 1 and not self.cancel:
                            time.sleep(pause * (attempt + 1))
                if vecs is None:
                    if self.cancel:
                        continue
                    if size > 1:  # smaller batches finish within the timeout
                        size = max(1, size // 2)
                        self.progress["batch"] = size
                        continue
                    self.progress["skipped"] += 1  # this one text keeps failing: move on
                    i += 1
                    continue
                vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
                db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                               [(aid, key, h, v.tobytes())
                                for (aid, _, h), v in zip(chunk, vecs)])
                db.commit()
                i += len(chunk)
                self.progress["done"] = i - self.progress["skipped"]
        except Exception as exc:  # reported to the UI
            self.progress["error"] = as_msg(exc)
        finally:
            if db is not None:
                try:  # use what is already done, even after a stop or an error
                    self.load(db, key)
                finally:
                    db.close()
            self.progress["running"] = False

    def scores(self, query: str, settings: AISettings) -> dict[int, float]:
        """Cosine similarity of every indexed assignment to the query."""
        if not self.ready:
            return {}
        q = np.asarray(Embedder(settings).embed([query])[0], dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        with self._lock:
            if self.mat.shape[1] != q.shape[0]:
                raise ZiviError("index_other_model", model=self.model)
            sims = self.mat @ q
            return dict(zip(self.ids.tolist(), sims.tolist()))
