"""Search engine over the converted SQLite store.

The ~4000 assignments are held in memory; filtering is plain Python and full-text search
uses SQLite FTS5 (bm25 ranking, accent-insensitive). Facet counts are computed per field
while ignoring that field's own filter, so multi-select filters behave as expected.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import re
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from .converter import FTS_COLUMNS, FTS_VERSION, FTS_WEIGHTS, TRANSLATIONS_SQL, build_fts, fts_schema
from .ollama import DEFAULT_HOST, TRANSLATE_MODEL, host_from
from .semantic import AISettings, SemanticIndex, assignment_text

SUMMARY_FIELDS = ("id", "number", "title", "employer", "place", "canton", "sector",
                  "min_weeks", "hours_per_week", "work_model", "weekend", "night", "housing",
                  "meals", "reputation_check", "priority_program", "abroad", "language",
                  "first_week", "last_week", "n_weeks", "url")

LABELS = {
    "weekend": {"J": "Oui", "N": "Non", "M": "Possible"},
    "night": {"J": "Oui", "N": "Non", "M": "Possible"},
    "bool": {1: "Oui", 0: "Non"},
    "language": {"de": "Allemand", "fr": "Français", "it": "Italien"},
}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Texts translated by the "Traduire la base" job, in the order they are handed out:
# titles first so the list reads in French early on.
TR_FIELDS = ("title", "tasks", "required_skills", "desired_skills", "employer_description",
             "courses", "how_to_apply")
TR_TARGET = "fr"


def text_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode()).hexdigest()


RARE_SHARE = 0.05  # a value shared by at most 5 % of all assignments counts as rare
RARE_DECAY = 0.3   # weight of the 2nd, 3rd… rarest trait relative to the one before
YES_NO = {1: "Oui", 0: "Non"}
J_N_M = {"J": "Oui", "N": "Non", "M": "Possible"}


def rarity_traits(r: dict, task_areas: list[str]) -> list[tuple[str, str, Any]]:
    """(key, label shown to the user, value text) for every attribute used to judge rarity."""
    out: list[tuple[str, str, Any]] = []
    if r.get("sector"):
        out.append(("sector", "Domaine", re.sub(r"^\s*\d+\s*", "", r["sector"])))
    if r.get("work_model"):
        out.append(("work_model", "Horaire", r["work_model"]))
    if r.get("language"):
        out.append(("language", "Langue", LABELS["language"].get(r["language"], r["language"])))
    for c in (r.get("canton") or "").split(","):
        if c:
            out.append(("canton", "Canton", c))
    if r.get("weekend") in J_N_M:
        out.append(("weekend", "Travail le week-end", J_N_M[r["weekend"]]))
    if r.get("night") in J_N_M:
        out.append(("night", "Travail de nuit", J_N_M[r["night"]]))
    for field, label in (("housing", "Logement"), ("meals", "Repas"),
                         ("reputation_check", "Contrôle de réputation"),
                         ("priority_program", "Programme prioritaire"),
                         ("abroad", "À l'étranger")):
        out.append((field, label, YES_NO[1 if r.get(field) else 0]))
    if r.get("min_weeks"):
        out.append(("min_weeks", "Durée minimale", f"{r['min_weeks']} semaine{'s' if r['min_weeks'] > 1 else ''}"))
    h = r.get("hours_per_week")
    if h:  # ranges, otherwise odd values like 41.36 h would all look "rare"
        band = ("moins de 40 h" if h < 40 else "40 à 42,5 h" if h <= 42.5
                else "43 à 45 h" if h <= 45 else "plus de 45 h")
        out.append(("hours_per_week", "Heures par semaine", band))
    for t in task_areas:
        out.append(("task_areas", "Tâche", t))
    return out


def sector_code(sector: str | None) -> str | None:
    """'2 Social' -> '2' (the export prefixes every sector label with its code)."""
    m = re.match(r"\s*(\d+)", sector or "")
    return m.group(1) if m else (sector or None)


def week_ord(iso: str) -> int:
    """Monday-based week number usable for 'consecutive weeks' checks."""
    return date.fromisoformat(iso).toordinal() // 7


TOKEN_RE = re.compile(r'(-?"[^"]+"|\S+)')


PHONE_RE = re.compile(r'"?\+?[\d][\d\s/.()-]{7,}"?')


def phone_query(text: str) -> str:
    """A query that is only a phone number ('041 980 61 83', '+41 41 980 61 83', '"079/123
    45 67"') becomes its digits in national form, the way phones are indexed."""
    if not PHONE_RE.fullmatch(text.strip()):
        return text
    digits = re.sub(r"\D", "", text)
    if digits.startswith("0041"):
        digits = "0" + digits[4:]
    elif digits.startswith("41") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits if len(digits) >= 9 else text


def fts_query(text: str) -> tuple[str | None, str | None]:
    """User text -> (positive FTS5 expression, negative FTS5 expression).

    Supports "exact phrases", -exclusions, OR between words, and prefix matching (pflege
    matches Pflegeheim). Everything else is treated as plain words joined with AND.
    """
    pos: list[str] = []
    neg: list[str] = []
    for tok in TOKEN_RE.findall(text or ""):
        negative = tok.startswith("-") and len(tok) > 1
        tok = tok[1:] if negative else tok
        if tok == "OR":
            if pos and pos[-1] != "OR":
                pos.append("OR")
            continue
        if tok.startswith('"') and tok.endswith('"') and len(tok) > 2:
            words = re.findall(r"\w+", tok[1:-1])
            if not words:
                continue
            term = '"' + " ".join(words) + '"'
        else:
            words = re.findall(r"\w+", tok)
            if not words:
                continue
            term = " ".join(f'"{w}"*' for w in words)
            term = f"({term})" if len(words) > 1 else term
        (neg if negative else pos).append(term)
    while pos and pos[-1] == "OR":
        pos.pop()
    if pos and pos[0] == "OR":
        pos.pop(0)
    # OR binds tighter than the implicit AND, as in web search engines:
    # "alpage chien OR chat" = alpage AND (chien OR chat). FTS5 itself ranks AND above OR,
    # so each OR group gets its own parentheses.
    groups: list[list[str]] = []
    join_next = False
    for t in pos:
        if t == "OR":
            join_next = bool(groups)
        elif join_next:
            groups[-1].append(t)
            join_next = False
        else:
            groups.append([t])
    expr = " AND ".join(g[0] if len(g) == 1 else "(" + " OR ".join(g) + ")" for g in groups)
    return (expr or None), (" OR ".join(neg) or None)


class Engine:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.lock = threading.RLock()
        self.ai = AISettings()
        self.semantic = SemanticIndex()
        self.rows: dict[int, dict] = {}
        # favourites live next to the database so a re-import keeps them
        self.fav_path = Path(self.db_path).with_name("favorites.json")
        self.favorites: set[int] = set()
        self._load_favorites()
        # provider and model chosen in the UI (never the token), so the matching AI index
        # is loaded again after a restart
        self.ai_path = Path(self.db_path).with_name("ai_settings.json")
        # database translation through Ollama (Base de données → Traduction)
        self.tr_model, self.ollama_host = TRANSLATE_MODEL, DEFAULT_HOST
        # build the AI index on the French text (translation where there is one)
        self.ai_french = False
        self._load_ai_settings()
        self.load()

    # ------------------------------------------------------------------ AI settings
    def _load_ai_settings(self) -> None:
        try:
            saved = json.loads(self.ai_path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        # an explicit environment variable still wins over the saved choice
        if saved.get("base_url") and not os.environ.get("ZIVI_AI_BASE_URL"):
            self.ai.base_url = str(saved["base_url"])
        if saved.get("model") and not os.environ.get("ZIVI_AI_MODEL"):
            self.ai.model = str(saved["model"])
        self.tr_model = str(saved.get("translate_model") or self.tr_model)
        self.ai_french = bool(saved.get("ai_french", self.ai_french))
        self.ollama_host = host_from(saved.get("ollama_host") or self.ollama_host)

    def save_ai_settings(self) -> None:
        self.ai_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ai_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"base_url": self.ai.base_url, "model": self.ai.model,
                                   "translate_model": self.tr_model,
                                   "ai_french": self.ai_french,
                                   "ollama_host": self.ollama_host}), "utf-8")
        tmp.replace(self.ai_path)

    # ------------------------------------------------------------------ favourites
    def _load_favorites(self) -> None:
        try:
            self.favorites = {int(i) for i in json.loads(self.fav_path.read_text("utf-8"))}
        except (OSError, ValueError, TypeError):
            self.favorites = set()

    def clear_favorites(self) -> int:
        with self.lock:
            n = len(self.favorites)
            self.favorites = set()
            self.fav_path.parent.mkdir(parents=True, exist_ok=True)
            self.fav_path.write_text("[]", "utf-8")
            return n

    def set_favorite(self, i: int, on: bool) -> bool:
        with self.lock:
            (self.favorites.add if on else self.favorites.discard)(i)
            self.fav_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fav_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(self.favorites)), "utf-8")
            tmp.replace(self.fav_path)
            return i in self.favorites

    # ------------------------------------------------------------------ loading
    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        return db

    @property
    def loaded(self) -> bool:
        return bool(self.rows)

    def load(self) -> None:
        with self.lock:
            self.rows, self.task_areas, self.weeks, self.locations = {}, {}, {}, {}
            self.rare, self.rarity, self.trait_keys = {}, {}, {}
            self.how_to_apply: dict[int, str] = {}
            self.tr: dict[str, str] = {}  # original-text hash -> French text
            # texts the browser's translator kept failing on: left out of the to-do list
            # until the next run asks to retry them (in memory only)
            self.tr_failed: set[str] = set()
            self._fts_fr_dirty, self._fts_fr_built = True, 0.0
            self.report: dict = {}
            self.imported_at: float | None = None
            if not Path(self.db_path).exists():
                return
            db = self.connect()
            try:
                cols = ", ".join(SUMMARY_FIELDS + ("tasks", "required_skills", "desired_skills",
                                                   "employer_description", "courses"))
                for r in db.execute(f"SELECT {cols}, how_to_apply FROM assignments"):
                    row = dict(r)
                    self.how_to_apply[r["id"]] = row.pop("how_to_apply") or ""
                    self.rows[r["id"]] = row
                db.execute(TRANSLATIONS_SQL)
                self.tr = {h: t for h, t in db.execute(
                    "SELECT src_hash, text FROM translations WHERE target=?", (TR_TARGET,))}
                ta: dict[int, list[str]] = defaultdict(list)
                for r in db.execute("SELECT assignment_id, label, code FROM task_areas"):
                    ta[r[0]].append(r[1] or r[2])
                self.task_areas = dict(ta)
                wk: dict[int, list[str]] = defaultdict(list)
                for r in db.execute("SELECT assignment_id, week FROM weeks ORDER BY week"):
                    wk[r[0]].append(r[1])
                self.weeks = {k: (v, sorted({week_ord(w) for w in v})) for k, v in wk.items()}
                loc: dict[int, list[dict]] = defaultdict(list)
                for r in db.execute("SELECT * FROM locations"):
                    loc[r["assignment_id"]].append(dict(r))
                self.locations = dict(loc)
                rep = db.execute("SELECT value FROM meta WHERE key='report'").fetchone()
                if rep:
                    self.report = json.loads(rep[0])
                when = db.execute("SELECT value FROM meta WHERE key='imported_at'").fetchone()
                self.imported_at = float(when[0]) if when else self._guess_import_time()
                self.semantic.load(db, self.ai.model)
                # databases imported before the contact/address columns: upgrade in place
                cols = [d[0] for d in db.execute("SELECT * FROM fts LIMIT 0").description]
                version = db.execute("SELECT value FROM meta WHERE key='fts_version'").fetchone()
                if cols != list(FTS_COLUMNS) or (version and version[0]) != FTS_VERSION:
                    build_fts(db)
                    db.row_factory = sqlite3.Row
                    self._fts_fr_dirty = True
            finally:
                db.close()
            self._compute_rarity()

    def _compute_rarity(self) -> None:
        """Per assignment: its rare attribute values (rarest first) and a rarity score.

        Shares are measured over the whole database so they don't change with the filters.
        The score is the surprisal (-log2 share) of the rarest value plus a decaying share of
        the next ones: one very rare trait (abroad, 0.1 %) outranks many mildly rare ones,
        and among equally rare entries, the one with more rare traits comes first.
        """
        traits = {i: rarity_traits(r, self.task_areas.get(i, [])) for i, r in self.rows.items()}
        self.trait_keys = {i: {(k, v) for k, _l, v in ts} for i, ts in traits.items()}
        counts: dict[tuple[str, Any], int] = defaultdict(int)
        for ts in traits.values():
            for key, _label, value in set(ts):
                counts[(key, value)] += 1
        n = max(len(self.rows), 1)
        self.rare: dict[int, list[dict]] = {}
        self.rarity: dict[int, float] = {}
        for i, ts in traits.items():
            rare = []
            for key, label, value in dict.fromkeys(ts):
                c = counts[(key, value)]
                if c / n <= RARE_SHARE:
                    rare.append({"field": key, "label": label, "value": value,
                                 "count": c, "share": round(c / n, 4)})
            rare.sort(key=lambda x: x["count"])
            self.rare[i] = rare
            self.rarity[i] = sum(-math.log2(max(x["share"], 1 / n)) * RARE_DECAY ** k
                                 for k, x in enumerate(rare))

    # ------------------------------------------------------------------ helpers
    def ai_texts(self) -> dict[int, str]:
        """The text embedded for each assignment: the original, or with ai_french the French
        version of each field where there is one. A model trained mostly on English
        (nomic-embed-text) matches a French query far better against French text. Each
        translation that arrives changes the text, so the next build redoes just those."""
        if not self.ai_french or not self.tr:
            return {i: assignment_text(r, self.task_areas.get(i, []))
                    for i, r in self.rows.items()}
        out = {}
        for i, r in self.rows.items():
            fr = dict(r)
            for f in ("title", "tasks", "required_skills", "desired_skills",
                      "employer_description"):
                t = self.french(i, f)
                if t:
                    fr[f] = t
            out[i] = assignment_text(fr, self.task_areas.get(i, []))
        return out

    def ai_french_coverage(self) -> dict:
        """How many assignments have their AI text in French (original or translated)."""
        n_fr = sum(1 for r in self.rows.values() if r.get("language") == TR_TARGET)
        n_tr = sum(1 for i, r in self.rows.items() if r.get("language") != TR_TARGET
                   and self.french(i, "title") and self.french(i, "tasks") is not None)
        return {"french": n_fr, "translated": n_tr, "total": len(self.rows)}

    def _guess_import_time(self) -> float | None:
        """Databases imported before the import time was recorded: the uploaded file's date."""
        name = (self.report or {}).get("source_file")
        f = Path(self.db_path).parent / "uploads" / name if name else None
        return f.stat().st_mtime if f and f.exists() else None

    # ------------------------------------------------------------------ translations
    def _text(self, i: int, field: str) -> str:
        if field == "how_to_apply":
            return self.how_to_apply.get(i, "")
        return (self.rows[i].get(field) or "").strip()

    def _to_translate(self) -> list[tuple[str, str, str | None, int]]:
        """(hash, text, source language, field rank) of every distinct non-French text."""
        seen: dict[str, tuple[str, str, str | None, int]] = {}
        for rank, field in enumerate(TR_FIELDS):
            for i, r in sorted(self.rows.items(), key=lambda kv: -(kv[1]["number"] or 0)):
                if r.get("language") == TR_TARGET:
                    continue
                t = self._text(i, field)
                if t:
                    h = text_hash(t)
                    seen.setdefault(h, (h, t, r.get("language"), rank))
        return list(seen.values())

    def translation_stats(self) -> dict:
        with self.lock:
            todo = self._to_translate()
            done = sum(1 for h, *_ in todo if h in self.tr)
            foreign = [i for i, r in self.rows.items() if r.get("language") != TR_TARGET]
            full = sum(1 for i in foreign if all(
                text_hash(t) in self.tr for t in (self._text(i, f) for f in TR_FIELDS) if t))
            return {"target": TR_TARGET, "texts": len(todo), "done": done,
                    "chars": sum(len(t) for _, t, *_ in todo),
                    "chars_done": sum(len(t) for h, t, *_ in todo if h in self.tr),
                    "assignments": len(foreign), "assignments_done": full,
                    "failed": sum(1 for h, *_ in todo if h in self.tr_failed and h not in self.tr),
                    "by_language": {lang or "?": sum(1 for i in foreign
                                                     if self.rows[i].get("language") == lang)
                                    for lang in {self.rows[i].get("language") for i in foreign}}}

    def translation_todo(self, limit: int = 30) -> list[dict]:
        with self.lock:
            return [{"hash": h, "text": t, "lang": lang}
                    for h, t, lang, _ in self._to_translate()
                    if h not in self.tr and h not in self.tr_failed][:limit]

    def retry_failed_translations(self) -> None:
        with self.lock:
            self.tr_failed = set()

    def save_translations(self, items: list[dict]) -> int:
        """Store translated texts sent by the page; unknown hashes are ignored.

        An item {"hash": …, "failed": true} sets that text aside for this run."""
        with self.lock:
            known = {h: lang for h, _t, lang, _ in self._to_translate()}
            self.tr_failed |= {it["hash"] for it in items
                               if it.get("failed") and it.get("hash") in known}
            rows = [(it["hash"], TR_TARGET, it.get("lang") or known[it["hash"]], it["text"])
                    for it in items
                    if it.get("hash") in known and isinstance(it.get("text"), str)
                    and it["text"].strip()]
            if rows:
                db = self.connect()
                try:
                    db.execute(TRANSLATIONS_SQL)
                    db.executemany("INSERT OR REPLACE INTO translations VALUES (?,?,?,?)", rows)
                    db.commit()
                finally:
                    db.close()
                self.tr.update({h: t for h, _tg, _l, t in rows})
                self.tr_failed -= {h for h, *_ in rows}
                self._fts_fr_dirty = True
            return len(rows)

    def clear_translations(self) -> None:
        with self.lock:
            db = self.connect()
            try:
                db.execute(TRANSLATIONS_SQL)
                db.execute("DELETE FROM translations WHERE target=?", (TR_TARGET,))
                db.execute("DROP TABLE IF EXISTS fts_fr")
                db.commit()
            finally:
                db.close()
            self.tr = {}
            self._fts_fr_dirty = True

    def french(self, i: int, field: str) -> str | None:
        """The French text of a field: the original for French assignments, else its
        translation (None when not translated yet)."""
        t = self._text(i, field)
        if not t:
            return ""
        if self.rows[i].get("language") == TR_TARGET:
            return t
        return self.tr.get(text_hash(t))

    def rebuild_fts_fr(self, force: bool = False) -> None:
        """Full-text index over the French text (originals + translations).

        Rebuilt lazily: at most every 30 s while a translation job is saving, or at once
        when the job ends (force)."""
        with self.lock:
            if not self.tr or not self._fts_fr_dirty:
                return
            if not force and time.time() - self._fts_fr_built < 30:
                return
            db = self.connect()
            try:
                # the metadata columns (contact, address, details) are the same in both indexes
                meta = {r[0]: tuple(r[1:]) for r in db.execute(
                    "SELECT rowid, contact, address, details FROM fts")}
                db.execute("DROP TABLE IF EXISTS fts_fr")
                db.execute(fts_schema("fts_fr"))
                rows = []
                for i, r in self.rows.items():
                    fr = {f: self.french(i, f) or "" for f in TR_FIELDS}
                    rows.append((i, fr["title"], r["employer"], r["place"], fr["tasks"],
                                 "\n".join(x for x in (fr["required_skills"],
                                                       fr["desired_skills"]) if x),
                                 fr["employer_description"], fr["courses"],
                                 ", ".join(self.task_areas.get(i, [])),
                                 *meta.get(i, ("", "", ""))))
                db.executemany(f"INSERT INTO fts_fr(rowid, {', '.join(FTS_COLUMNS)}) "
                               f"VALUES ({','.join('?' * (len(FTS_COLUMNS) + 1))})", rows)
                db.commit()
            finally:
                db.close()
            self._fts_fr_dirty, self._fts_fr_built = False, time.time()

    def keyword_ids(self, text: str, search_original: bool = True) -> dict[int, float] | None:
        """Keyword matches in the French text (once translations exist) and, unless turned
        off, in the original text too; the better score of the two is kept."""
        if not self.tr:
            return self.fts_ids(text)
        self.rebuild_fts_fr()
        fr = self.fts_ids(text, "fts_fr")
        if not search_original or fr is None:
            return fr
        orig = self.fts_ids(text) or {}
        return {i: max(fr.get(i, -1e9), orig.get(i, -1e9)) for i in fr.keys() | orig.keys()}

    def fts_ids(self, text: str, table: str = "fts") -> dict[int, float] | None:
        """id -> bm25 score (higher is better) for the text; None if the text has no terms."""
        pos, neg = fts_query(text)
        if not pos and not neg:
            return None
        db = self.connect()
        try:
            if pos:
                found = {r[0]: -r[1] for r in db.execute(
                    f"SELECT rowid, bm25({table}, {', '.join(map(str, FTS_WEIGHTS))}) "
                    f"FROM {table} WHERE {table} MATCH ?", (pos,))}
            else:
                found = {i: 0.0 for i in self.rows}
            if neg:
                for r in db.execute(f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (neg,)):
                    found.pop(r[0], None)
            return found
        except sqlite3.OperationalError:
            # malformed expression: fall back to a plain substring search
            low = text.lower()
            return {i: 1.0 for i, r in self.rows.items()
                    if low in " ".join(str(v) for v in r.values() if v).lower()}
        finally:
            db.close()

    def snippets(self, text: str, ids: list[int], tables: tuple[str, ...] = ("fts",)
                 ) -> dict[int, str]:
        """Highlighted extracts, from the first table (in order) where each id matches."""
        pos, _ = fts_query(text)
        if not pos or not ids:
            return {}
        out: dict[int, str] = {}
        db = self.connect()
        try:
            for table in tables:
                rest = [i for i in ids if i not in out]
                if not rest:
                    break
                q = (f"SELECT rowid, snippet({table}, -1, '<mark>', '</mark>', '…', 14) "
                     f"FROM {table} WHERE {table} MATCH ? "
                     f"AND rowid IN ({','.join('?' * len(rest))})")
                try:
                    out.update({r[0]: r[1] for r in db.execute(q, [pos, *rest])})
                except sqlite3.OperationalError:
                    continue
            return out
        finally:
            db.close()

    # ------------------------------------------------------------------ filters
    def _filters(self, q: dict) -> dict[str, Callable[[int, dict], bool]]:
        """Named predicates; each facet is computed without its own predicate."""
        f: dict[str, Callable[[int, dict], bool]] = {}

        if q.get("favorites_only"):
            favs = set(self.favorites)
            f["favorites"] = lambda i, r: i in favs

        # rare traits clicked in the list: {"field": "night", "value": "Oui"}; all must match
        wanted = {(t.get("field"), str(t.get("value"))) for t in (q.get("traits") or [])
                  if isinstance(t, dict) and t.get("field")}
        if wanted:
            f["traits"] = lambda i, r: wanted <= self.trait_keys.get(i, set())

        def multi(field: str, key: str | None = None):
            vals = q.get(key or field)
            if vals:
                allowed = set(vals)
                f[key or field] = lambda i, r: r.get(field) in allowed

        for field in ("weekend", "night", "sector", "work_model", "language"):
            multi(field)
        for field in ("housing", "meals", "reputation_check", "priority_program", "abroad"):
            v = q.get(field)
            if v is not None and v != "":
                want = 1 if v in (True, 1, "1", "true", "yes") else 0
                f[field] = lambda i, r, fld=field, w=want: (r.get(fld) or 0) == w

        cantons = q.get("canton")
        if cantons:
            allowed_c = set(cantons)
            f["canton"] = lambda i, r: bool(r.get("canton")) and \
                bool(allowed_c & set(r["canton"].split(",")))

        tas = q.get("task_areas")
        if tas:
            want_ta = set(tas)
            if q.get("task_areas_mode") == "all":
                f["task_areas"] = lambda i, r: want_ta <= set(self.task_areas.get(i, []))
            else:
                f["task_areas"] = lambda i, r: bool(want_ta & set(self.task_areas.get(i, [])))

        def num_range(field: str, lo_key: str, hi_key: str):
            lo, hi = q.get(lo_key), q.get(hi_key)
            if lo not in (None, "") or hi not in (None, ""):
                lo_v = float(lo) if lo not in (None, "") else -math.inf
                hi_v = float(hi) if hi not in (None, "") else math.inf
                f[field] = lambda i, r: r.get(field) is not None and lo_v <= r[field] <= hi_v

        num_range("min_weeks", "min_weeks_min", "min_weeks_max")
        num_range("hours_per_week", "hours_min", "hours_max")

        if q.get("no_course_required"):
            f["courses"] = lambda i, r: not r.get("courses")

        period = q.get("period") or {}
        p_from, p_to = period.get("from"), period.get("to")
        if p_from or p_to:
            lo = week_ord(p_from) if p_from else -10**9
            hi = week_ord(p_to) if p_to else 10**9
            mode = period.get("mode", "any")
            duration = int(period.get("duration_weeks") or 0)

            def period_ok(i: int, r: dict) -> bool:
                wk = self.weeks.get(i)
                if not wk:
                    return False
                ords = wk[1]
                if mode == "all":
                    need = set(range(lo, hi + 1))
                    return need <= set(ords)
                starts = [o for o in ords if lo <= o <= hi]
                if not starts:
                    return False
                if duration <= 1:
                    return True
                have = set(ords)
                return any(all(s + k in have for k in range(duration)) for s in starts)

            f["period"] = period_ok

        centers = [c for c in ((q.get("geo") or {}).get("centers") or [])
                   if c.get("lat") is not None and c.get("lon") is not None]
        if centers:
            self._centers = centers

            def geo_ok(i: int, r: dict) -> bool:
                return self.distance(i, centers, within=True) is not None

            f["geo"] = geo_ok
        else:
            self._centers = []

        bbox = q.get("bbox")
        if bbox and len(bbox) == 4:
            s, w, n, e = map(float, bbox)
            f["bbox"] = lambda i, r: any(
                l["lat"] is not None and s <= l["lat"] <= n and w <= l["lon"] <= e
                for l in self.locations.get(i, []))
        return f

    def distance(self, i: int, centers: list[dict], within: bool = False) -> float | None:
        best = None
        for loc in self.locations.get(i, []):
            if loc["lat"] is None:
                continue
            for c in centers:
                d = haversine(loc["lat"], loc["lon"], float(c["lat"]), float(c["lon"]))
                if within and d > float(c.get("radius_km") or 10):
                    continue
                if best is None or d < best:
                    best = d
        return best

    # ------------------------------------------------------------------ search
    def search(self, q: dict) -> dict[str, Any]:
        with self.lock:
            return self._search(q)

    def _search(self, q: dict) -> dict[str, Any]:
        warnings: list[str] = []
        ids = list(self.rows)
        text = phone_query((q.get("text") or "").strip())
        mode = q.get("text_mode") or "keyword"
        view_fr = q.get("view_lang") == TR_TARGET and bool(self.tr)
        search_original = q.get("search_original", True) is not False

        # --- relevance
        kw: dict[int, float] | None = None
        sem: dict[int, float] | None = None
        if text and mode in ("keyword", "hybrid"):
            kw = self.keyword_ids(text, search_original)
        if text and mode in ("semantic", "hybrid"):
            if not self.ai.configured:
                warnings.append("La recherche IA n'est pas configurée : ajoutez un jeton dans "
                                "Base de données → Recherche IA. Recherche par mots-clés "
                                "utilisée à la place.")
            elif not self.semantic.ready:
                warnings.append("L'index IA n'est pas encore construit : Base de données → "
                                "Recherche IA → « Construire l'index ». Recherche par mots-clés "
                                "utilisée à la place.")
            else:
                try:
                    sem = self.semantic.scores(text, self.ai)
                except Exception as exc:
                    warnings.append(f"La recherche IA a échoué ({exc}) ; recherche par mots-clés "
                                    "utilisée à la place.")
            if sem is None and kw is None:
                kw = self.keyword_ids(text, search_original)

        score: dict[int, float] = {}
        text_ok: Callable[[int], bool] = lambda i: True
        sem_keep: set[int] = set()
        if sem is not None:
            # every text has *some* similarity, so keep only the closest ones
            top_k = int(q.get("ai_top_k") if q.get("ai_top_k") is not None else 200)
            min_sim = float(q.get("min_similarity") or -1)
            ranked = [i for i in sorted(sem, key=sem.get, reverse=True) if sem[i] >= min_sim]
            sem_keep = set(ranked[:top_k] if top_k > 0 else ranked)
        if sem is not None and kw is not None:  # hybrid: reciprocal rank fusion
            sem_rank = {i: k for k, i in enumerate(sorted(sem, key=sem.get, reverse=True))}
            kw_rank = {i: k for k, i in enumerate(sorted(kw, key=kw.get, reverse=True))}
            for i in self.rows:
                score[i] = 1 / (60 + sem_rank.get(i, 10**6)) + \
                    (1.5 / (60 + kw_rank[i]) if i in kw_rank else 0)
            text_ok = lambda i: i in kw or i in sem_keep
        elif sem is not None:
            score = sem
            text_ok = lambda i: i in sem_keep
        elif kw is not None:
            score = kw
            text_ok = lambda i: i in kw

        # --- filters
        filters = self._filters(q)
        passes: dict[str, set[int]] = {}
        for name, pred in filters.items():
            passes[name] = {i for i in ids if pred(i, self.rows[i])}
        text_set = {i for i in ids if text_ok(i)}
        matched = text_set.intersection(*passes.values()) if passes else text_set

        # --- facets (each ignores its own filter)
        def base_for(name: str) -> set[int]:
            others = [s for n, s in passes.items() if n != name]
            return text_set.intersection(*others) if others else text_set

        facets: dict[str, list] = {}
        for field in ("weekend", "night", "sector", "work_model", "language", "housing",
                      "meals", "reputation_check", "priority_program", "abroad"):
            base = base_for(field)
            counts: dict[Any, int] = defaultdict(int)
            for i in base:
                v = self.rows[i].get(field)
                if field in ("housing", "meals", "reputation_check", "priority_program",
                             "abroad"):
                    v = v or 0
                if v is not None and v != "":
                    counts[v] += 1
            facets[field] = sorted(([k, c] for k, c in counts.items()), key=lambda kc: -kc[1])
        base = base_for("task_areas")
        ta_counts: dict[str, int] = defaultdict(int)
        for i in base:
            for t in self.task_areas.get(i, []):
                ta_counts[t] += 1
        facets["task_areas"] = sorted(([k, c] for k, c in ta_counts.items()),
                                      key=lambda kc: -kc[1])
        base = base_for("canton")
        c_counts: dict[str, int] = defaultdict(int)
        for i in base:
            for c in (self.rows[i].get("canton") or "").split(","):
                if c:
                    c_counts[c] += 1
        facets["canton"] = sorted(([k, c] for k, c in c_counts.items()), key=lambda kc: kc[0])

        # --- sorting
        centers = self._centers
        dist = {i: self.distance(i, centers) for i in matched} if centers else {}
        sort = q.get("sort") or ("relevance" if score else ("distance" if centers else "number"))
        if sort == "relevance" and score:
            order = sorted(matched, key=lambda i: (-score.get(i, -1e9), -(self.rows[i]["number"] or 0)))
        elif sort == "distance" and centers:
            order = sorted(matched, key=lambda i: (dist.get(i) is None, dist.get(i) or 0))
        elif sort == "rarity":
            order = sorted(matched, key=lambda i: (-self.rarity.get(i, 0),
                                                   -(self.rows[i]["number"] or 0)))
        elif sort == "min_weeks":
            order = sorted(matched, key=lambda i: (self.rows[i]["min_weeks"] or 999))
        elif sort == "title":
            order = sorted(matched, key=lambda i: (self.rows[i]["title"] or "").lower())
        elif sort == "first_week":
            order = sorted(matched, key=lambda i: self.rows[i]["first_week"] or "9999")
        else:
            order = sorted(matched, key=lambda i: -(self.rows[i]["number"] or 0))

        offset = int(q.get("offset") or 0)
        limit = min(int(q.get("limit") or 50), 500)
        page = order[offset:offset + limit]
        tables = (("fts_fr", "fts") if search_original else ("fts_fr",)) if self.tr else ("fts",)
        if not view_fr and self.tr and search_original:
            tables = ("fts", "fts_fr")
        snips = self.snippets(text, page, tables) if text and kw is not None else {}
        items = []
        for i in page:
            r = self.rows[i]
            item = {k: r.get(k) for k in SUMMARY_FIELDS}
            item["task_areas"] = self.task_areas.get(i, [])
            item["favorite"] = i in self.favorites
            item["rare"] = self.rare.get(i, [])
            item["rarity"] = round(self.rarity.get(i, 0.0), 2)
            item["score"] = round(score[i], 4) if i in score else None
            item["distance_km"] = round(dist[i], 1) if dist.get(i) is not None else None
            item["snippet"] = snips.get(i)
            if view_fr:
                fr_title = self.french(i, "title")
                if fr_title and fr_title != r["title"]:
                    item["title_orig"], item["title"] = r["title"], fr_title
            item["locations"] = [{"lat": l["lat"], "lon": l["lon"], "label": l["label"]}
                                 for l in self.locations.get(i, []) if l["lat"] is not None]
            items.append(item)

        points = []
        for i in order:
            r = self.rows[i]
            for l in self.locations.get(i, []):
                if l["lat"] is not None:
                    title = (self.french(i, "title") or r["title"]) if view_fr else r["title"]
                    points.append([i, round(l["lat"], 5), round(l["lon"], 5), title,
                                   r["employer"], l["label"] or r["place"],
                                   sector_code(r.get("sector")), i in self.favorites])
        unlocated = sum(1 for i in matched
                        if not any(l["lat"] is not None for l in self.locations.get(i, [])))
        return {"total": len(matched), "offset": offset, "items": items, "points": points,
                "unlocated": unlocated, "facets": facets,
                "favorites": len(self.favorites & self.rows.keys()), "sort": sort, "warnings": warnings,
                "mode_used": "hybrid" if (sem is not None and kw is not None)
                else "semantic" if sem is not None else "keyword" if kw is not None else None}

    # ------------------------------------------------------------------ detail
    def detail(self, i: int) -> dict | None:
        db = self.connect()
        try:
            r = db.execute("SELECT * FROM assignments WHERE id=?", (i,)).fetchone()
            if not r:
                return None
            out = {k: r[k] for k in r.keys() if k != "raw"}
            out["task_areas"] = self.task_areas.get(i, [])
            out["favorite"] = i in self.favorites
            out["rare"] = self.rare.get(i, [])
            out["weeks"] = self.weeks.get(i, ([], []))[0]
            out["locations"] = self.locations.get(i, [])
            # French translations available for this assignment, by field
            out["tr"] = {} if out.get("language") == TR_TARGET else {
                f: t for f in TR_FIELDS
                if (out.get(f) or "").strip() and (t := self.tr.get(text_hash(out[f])))}
            return out
        finally:
            db.close()

    def stats(self) -> dict:
        all_weeks = [w for v in self.weeks.values() for w in v[0]]
        return {"assignments": len(self.rows),
                "located": sum(1 for i in self.rows
                               if any(l["lat"] is not None for l in self.locations.get(i, []))),
                "first_week": min(all_weeks) if all_weeks else None,
                "last_week": max(all_weeks) if all_weeks else None,
                "report": self.report, "ai": self.ai.public(),
                "favorites": len(self.favorites & set(self.rows)),
                "db_path": self.db_path,
                "db_size": Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0,
                "imported_at": self.imported_at,
                "translations": len(self.tr),
                "ai_index": {"ready": self.semantic.ready, "vectors": len(self.semantic.ids),
                             "model": self.semantic.model, **self.semantic.progress}}
