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

from .messages import ZiviError, as_msg, msg

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
        self.proc: subprocess.Popen | None = None  # 'ollama serve' started by this app
        self.stopped_by_us = False  # stopped when the page left: restart when it is back

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
        self.proc = subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                     creationflags=flags, start_new_session=sys.platform != "win32")
        self.stopped_by_us = False
        return True

    # ------------------------------------------------------------------ release
    def running(self, host: str) -> bool:
        try:
            httpx.get(f"{host}/api/version", timeout=2)
            return True
        except httpx.HTTPError:
            return False

    def unload_all(self, host: str) -> list[str]:
        """Free the memory: ask Ollama to unload every model it has loaded (keep_alive 0).
        Generation models unload through /api/generate, embedding models through /api/embed."""
        try:
            loaded = [m["name"] for m in httpx.get(f"{host}/api/ps", timeout=3).json()
                      .get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []
        for name in loaded:
            for path, body in (("/api/generate", {"model": name, "keep_alive": 0}),
                               ("/api/embed", {"model": name, "input": [], "keep_alive": 0})):
                try:
                    if httpx.post(f"{host}{path}", json=body, timeout=15).status_code < 400:
                        break
                except httpx.HTTPError:
                    continue
        return loaded

    def stop(self) -> bool:
        """Stop the Ollama server: the one this app started, else Ollama's own processes
        (the tray app and 'ollama serve'), so it does not keep memory once the page is gone."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc = None
            self.stopped_by_us = True
            return True
        names = (["ollama app.exe", "ollama.exe"] if sys.platform == "win32" else ["ollama"])
        stopped = False
        for name in names:
            if sys.platform == "win32":
                cmd = ["taskkill", "/IM", name, "/F"]
            else:
                cmd = ["pkill", "-x", name]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                stopped = stopped or r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                continue
        self.stopped_by_us = self.stopped_by_us or stopped
        return stopped


class PageWatch:
    """Frees Ollama once no page of the app is open.

    Pages call alive() every few seconds and left() when they close. A watcher thread
    waits `grace` seconds after the last sign of life (a reload is not a departure), lets
    running jobs finish (translation, AI index), then unloads the models and stops Ollama.
    When a page comes back, Ollama is started again if this app stopped it."""

    def __init__(self, ollama: Ollama, host: Callable[[], str], enabled: Callable[[], bool],
                 busy: Callable[[], bool], grace: float = 30.0, tick: float = 5.0):
        self.ollama, self.host, self.enabled, self.busy = ollama, host, enabled, busy
        self.grace, self.tick = grace, tick
        self.last_seen: float | None = None
        self.released = False
        self.log: list[str] = []
        self._thread: threading.Thread | None = None

    def alive(self) -> None:
        self.last_seen = time.time()
        if self.released or self.ollama.stopped_by_us:
            self.released = False
            if self.ollama.stopped_by_us and not self.ollama.running(self.host()):
                self.ollama.start()  # the page is back: Ollama as it was
                self.log.append("restarted")
        if self._thread is None:
            self._thread = threading.Thread(target=self._watch, daemon=True)
            self._thread.start()

    def left(self) -> None:
        # the page is closing; give a reload `grace` seconds to say hello again
        if self.last_seen is not None:
            self.last_seen = min(self.last_seen, time.time())

    def check(self, now: float | None = None) -> bool:
        """Release if nobody is here any more; True when it released."""
        now = now or time.time()
        if (self.released or self.last_seen is None or not self.enabled()
                or now - self.last_seen < self.grace or self.busy()):
            return False
        host = self.host()
        if not self.ollama.running(host):
            self.released = True
            return False
        self.log.append("unloaded " + ", ".join(self.ollama.unload_all(host)))
        if self.ollama.stop():
            self.log.append("stopped")
        self.released = True
        return True

    def _watch(self) -> None:
        while True:
            time.sleep(self.tick)
            try:
                self.check()
            except Exception as exc:  # never kill the watcher
                self.log.append(f"error {exc}")

    # ------------------------------------------------------------------ download
    def pull(self, host: str, model: str) -> None:
        """Download a model in a background thread; progress in self.pulls[model]."""
        with self._lock:
            if self.pulls.get(model, {}).get("running"):
                return
            self.pulls[model] = {"running": True, "status": "", "completed": 0,
                                 "total": 0, "error": None, "done": False}
        threading.Thread(target=self._pull, args=(host, model), daemon=True).start()

    def _pull(self, host: str, model: str) -> None:
        p = self.pulls[model]
        try:
            with httpx.stream("POST", f"{host}/api/pull", json={"model": model, "stream": True},
                              timeout=httpx.Timeout(60.0, read=600.0)) as r:
                if r.status_code >= 400:
                    r.read()
                    raise ZiviError("ollama_http", status=r.status_code, body=r.text[:200])
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    m = json.loads(line)
                    if m.get("error"):
                        raise ZiviError(m["error"])  # Ollama's own text, shown as is
                    p["status"] = m.get("status", p["status"])
                    if m.get("total"):  # one layer at a time: keep the biggest
                        if m["total"] >= p["total"]:
                            p["total"], p["completed"] = m["total"], m.get("completed", 0)
                    if m.get("status") == "success":
                        p["done"] = True
        except Exception as exc:  # reported to the UI
            p["error"] = as_msg(exc)
        finally:
            p["running"] = False


# --------------------------------------------------------------------------- translation

def translate_prompt(text: str, source: str | None, lines: int, target: str = "fr") -> str:
    """TranslateGemma's documented prompt (works with general models too)."""
    name = LANG_NAMES.get(source or "", "")
    tname = LANG_NAMES.get(target, target)
    lead = (f"You are a professional {name} ({source}) to {tname} ({target}) translator. "
            if name else f"You are a professional translator into {tname} ({target}). ")
    keep = (f"The text has {lines} lines: keep exactly the same line breaks. " if lines > 1 else "")
    return (f"{lead}Your goal is to accurately convey the meaning and nuances of the original "
            f"{name or 'source'} text while adhering to {tname} grammar, vocabulary, and "
            f"cultural sensitivities.\nProduce only the {tname} translation, without any "
            f"additional explanations or commentary. {keep}Please translate the following "
            f"{name or 'source'} text into {tname}:\n\n\n{text}")


class TranslateFailed(ZiviError):
    """This text failed (timeout, overload…); others may still work."""


def translate(host: str, model: str, text: str, source: str | None,
              timeout: float = 600.0, target: str = "fr") -> str:
    """One text through Ollama. Line breaks are kept: if the model merges or splits lines,
    the text is sent again line by line."""
    lines = text.split("\n")
    body = [ln for ln in lines if ln.strip()]

    def call(chunk: str, n: int) -> str:
        try:
            r = httpx.post(f"{host}/api/generate", timeout=httpx.Timeout(timeout, connect=10.0),
                           json={"model": model,
                                 "prompt": translate_prompt(chunk, source, n, target),
                                 "stream": False, "keep_alive": "15m",
                                 "options": {"temperature": 0}})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TranslateFailed("ollama_no_response", kind=type(exc).__name__) from exc
        if r.status_code == 404:  # model not downloaded: retrying won't help
            raise ZiviError("ollama_model_missing", model=model)
        if r.status_code >= 400:
            raise TranslateFailed("ollama_http", status=r.status_code, body=r.text[:200])
        out = (r.json().get("response") or "").strip()
        if not out:
            raise TranslateFailed("empty_response")
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
            retries: int = 3, pause: float = 3.0, target: str = "fr") -> None:
        self.cancel = False
        p = self.progress = {"running": True, "engine": "ollama", "model": model, "done": 0,
                             "target": target,
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
                            text = translate(host, model, it["text"], it.get("lang"),
                                             target=target)
                            p["note"] = None
                            break
                        except TranslateFailed as exc:
                            p["note"] = msg("retry", attempt=attempt + 1, of=retries - 1,
                                            error=as_msg(exc))
                            if attempt < retries - 1 and not self.cancel:
                                time.sleep(pause * (attempt + 1))
                    if text is None:
                        save([{"hash": it["hash"], "failed": True}])
                        p["skipped"] += 1
                        in_a_row += 1
                        if in_a_row >= 25:
                            raise ZiviError("ollama_failing", error=p["note"]["params"]["error"]
                                            if p["note"] else "")
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
            p["error"] = as_msg(exc)
        finally:
            p["running"] = False
