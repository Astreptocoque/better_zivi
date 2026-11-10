"""Local Ollama: detection, start, model download, and the database translation job.

Ollama (https://ollama.com) runs open models on this computer. It serves both the AI
search embeddings (through its OpenAI-compatible /v1 API) and the translation of the
assignments into French with a translation model such as TranslateGemma.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

DEFAULT_HOST = "http://127.0.0.1:11434"
DOWNLOAD_URL = "https://ollama.com/download"
TRANSLATE_MODEL = "translategemma:4b"

LANG_NAMES = {"de": "German", "it": "Italian", "en": "English", "fr": "French"}


def fast_local(url: str) -> str:
    """'localhost' -> '127.0.0.1'. On Windows a request to 'localhost' first tries IPv6,
    which Ollama does not listen on, and waits ~2 s for that to fail on every call."""
    return url.replace("://localhost", "://127.0.0.1")


def host_from(url: str | None) -> str:
    """'http://localhost:11434/v1' (the embeddings base URL) -> 'http://127.0.0.1:11434'."""
    if not url:
        return fast_local(os.environ.get("OLLAMA_HOST_URL", DEFAULT_HOST))
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return fast_local(f"{parts.scheme or 'http'}://{parts.netloc or parts.path}".rstrip("/"))


def same_model(a: str, b: str) -> bool:
    """'nomic-embed-text' and 'nomic-embed-text:latest' are the same model."""
    norm = lambda m: m if ":" in m else f"{m}:latest"  # noqa: E731
    return norm(a.strip()) == norm(b.strip())


def executable() -> str | None:
    """Path of the ollama program on this computer, if it is installed."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Ollama.app/Contents/Resources/ollama"))
    candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((str(c) for c in candidates if c.exists()), None)


class Ollama:
    def __init__(self):
        self.pulls: dict[str, dict] = {}  # model -> download progress
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ detection
    def status(self, host: str) -> dict:
        """Running? which version? which models? (installed = the program exists here)."""
        out = {"host": host, "running": False, "version": None, "models": [],
               "installed": executable() is not None, "download_url": DOWNLOAD_URL,
               "pulls": self.pulls}
        try:
            v = httpx.get(f"{host}/api/version", timeout=3).json()
            out["running"], out["version"] = True, v.get("version")
            tags = httpx.get(f"{host}/api/tags", timeout=5).json()
            out["models"] = [{"name": m["name"], "size": m.get("size")}
                             for m in tags.get("models", [])]
        except (httpx.HTTPError, ValueError):
            pass
        return out

    def start(self) -> bool:
        """Start 'ollama serve' in the background (when installed but not running)."""
        exe = executable()
        if not exe:
            return False
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags,
                         start_new_session=sys.platform != "win32")
        return True

    # ------------------------------------------------------------------ download
    def pull(self, host: str, model: str) -> None:
        """Download a model in a background thread; progress in self.pulls[model]."""
        with self._lock:
            if self.pulls.get(model, {}).get("running"):
                return
            self.pulls[model] = {"running": True, "status": "démarrage", "completed": 0,
                                 "total": 0, "error": None, "done": False}
        threading.Thread(target=self._pull, args=(host, model), daemon=True).start()

    def _pull(self, host: str, model: str) -> None:
        p = self.pulls[model]
        try:
            with httpx.stream("POST", f"{host}/api/pull", json={"model": model, "stream": True},
                              timeout=httpx.Timeout(60.0, read=600.0)) as r:
                if r.status_code >= 400:
                    r.read()
                    raise RuntimeError(f"Ollama a répondu {r.status_code} : {r.text[:200]}")
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    p["status"] = msg.get("status", p["status"])
                    if msg.get("total"):  # one layer at a time: keep the biggest
                        if msg["total"] >= p["total"]:
                            p["total"], p["completed"] = msg["total"], msg.get("completed", 0)
                    if msg.get("status") == "success":
                        p["done"] = True
        except Exception as exc:  # reported to the UI
            p["error"] = str(exc)
        finally:
            p["running"] = False


# --------------------------------------------------------------------------- translation

def translate_prompt(text: str, source: str | None, lines: int) -> str:
    """TranslateGemma's documented prompt (works with general models too)."""
    name = LANG_NAMES.get(source or "", "")
    lead = (f"You are a professional {name} ({source}) to French (fr) translator. "
            if name else "You are a professional translator into French (fr). ")
    keep = (f"The text has {lines} lines: keep exactly the same line breaks. " if lines > 1 else "")
    return (f"{lead}Your goal is to accurately convey the meaning and nuances of the original "
            f"{name or 'source'} text while adhering to French grammar, vocabulary, and cultural "
            f"sensitivities.\nProduce only the French translation, without any additional "
            f"explanations or commentary. {keep}Please translate the following "
            f"{name or 'source'} text into French:\n\n\n{text}")


class TranslateFailed(RuntimeError):
    """This text failed (timeout, overload…); others may still work."""


def translate(host: str, model: str, text: str, source: str | None,
              timeout: float = 600.0) -> str:
    """One text through Ollama. Line breaks are kept: if the model merges or splits lines,
    the text is sent again line by line."""
    lines = text.split("\n")
    body = [ln for ln in lines if ln.strip()]

    def call(chunk: str, n: int) -> str:
        try:
            r = httpx.post(f"{host}/api/generate", timeout=httpx.Timeout(timeout, connect=10.0),
                           json={"model": model, "prompt": translate_prompt(chunk, source, n),
                                 "stream": False, "keep_alive": "15m",
                                 "options": {"temperature": 0}})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TranslateFailed(f"pas de réponse d'Ollama ({type(exc).__name__})") from exc
        if r.status_code == 404:  # model not downloaded: retrying won't help
            raise RuntimeError(f"le modèle {model} n'est pas installé dans Ollama")
        if r.status_code >= 400:
            raise TranslateFailed(f"Ollama a répondu {r.status_code} : {r.text[:200]}")
        out = (r.json().get("response") or "").strip()
        if not out:
            raise TranslateFailed("réponse vide")
        return out

    if len(body) <= 1:
        return call(text.strip(), 1)
    whole = call("\n".join(body), len(body))
    got = [ln for ln in whole.split("\n") if ln.strip()]
    if len(got) != len(body):  # lines merged or split: go line by line instead
        got = [call(ln, 1) for ln in body]
    it = iter(got)
    return "\n".join(next(it).strip() if ln.strip() else ln for ln in lines)


class TranslationJob:
    """Translates every text still to do, one at a time, saving as it goes (thread).

    `todo(limit)` hands out texts ({hash, text, lang}); `save(items)` stores results or
    {"hash", "failed": True}. A text is retried with a pause; if it keeps failing it is
    set aside and the job moves on, unless 25 texts fail in a row (Ollama is down)."""

    def __init__(self):
        self.progress = {"running": False}
        self.cancel = False

    def run(self, host: str, model: str, todo: Callable[[int], list[dict]],
            save: Callable[[list[dict]], int], refresh: Callable[[], None],
            retries: int = 3, pause: float = 3.0) -> None:
        self.cancel = False
        p = self.progress = {"running": True, "engine": "ollama", "model": model, "done": 0,
                             "chars": 0, "skipped": 0, "error": None, "cancelled": False,
                             "note": None, "started": time.time(), "rate": 0.0}
        in_a_row = 0
        try:
            while not self.cancel:
                items = todo(8)
                if not items:
                    break
                for it in items:
                    if self.cancel:
                        break
                    text = None
                    for attempt in range(retries):
                        try:
                            text = translate(host, model, it["text"], it.get("lang"))
                            p["note"] = None
                            break
                        except TranslateFailed as exc:
                            p["note"] = f"nouvel essai {attempt + 1}/{retries - 1} : {exc}"
                            if attempt < retries - 1 and not self.cancel:
                                time.sleep(pause * (attempt + 1))
                    if text is None:
                        save([{"hash": it["hash"], "failed": True}])
                        p["skipped"] += 1
                        in_a_row += 1
                        if in_a_row >= 25:
                            raise RuntimeError(f"Ollama échoue sans arrêt ({p['note']})")
                        continue
                    in_a_row = 0
                    save([{"hash": it["hash"], "text": text, "lang": it.get("lang")}])
                    p["done"] += 1
                    p["chars"] += len(it["text"])
                    p["rate"] = p["chars"] / max(time.time() - p["started"], 1)
                    if p["done"] % 50 == 0:
                        refresh()  # keep the French search index close to current
            p["cancelled"] = self.cancel
        except Exception as exc:  # reported to the UI
            p["error"] = str(exc)
        finally:
            p["running"] = False
