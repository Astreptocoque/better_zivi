"""Search engine over the converted SQLite store.

The ~4000 assignments are held in memory; filtering is plain Python and full-text search
uses SQLite FTS5 (bm25 ranking, accent-insensitive). Facet counts are computed per field
while ignoring that field's own filter, so multi-select filters behave as expected.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

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
    expr = ""
    for t in pos:
        if t == "OR":
            expr += " OR "
        elif expr and not expr.endswith(" OR "):
            expr += " AND " + t
        else:
            expr += t
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

    def save_ai_settings(self) -> None:
        self.ai_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ai_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"base_url": self.ai.base_url, "model": self.ai.model}),
                       "utf-8")
        tmp.replace(self.ai_path)

    # ------------------------------------------------------------------ favourites
    def _load_favorites(self) -> None:
        try:
            self.favorites = {int(i) for i in json.loads(self.fav_path.read_text("utf-8"))}
        except (OSError, ValueError, TypeError):
            self.favorites = set()

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
            self.rare, self.rarity = {}, {}
            self.report: dict = {}
            if not Path(self.db_path).exists():
                return
            db = self.connect()
            try:
                cols = ", ".join(SUMMARY_FIELDS + ("tasks", "required_skills", "desired_skills",
                                                   "employer_description", "courses"))
                for r in db.execute(f"SELECT {cols} FROM assignments"):
                    self.rows[r["id"]] = dict(r)
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
                self.semantic.load(db, self.ai.model)
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
        return {i: assignment_text(r, self.task_areas.get(i, [])) for i, r in self.rows.items()}

    def fts_ids(self, text: str) -> dict[int, float] | None:
        """id -> bm25 score (higher is better) for the text; None if the text has no terms."""
        pos, neg = fts_query(text)
        if not pos and not neg:
            return None
        db = self.connect()
        try:
            if pos:
                found = {r[0]: -r[1] for r in db.execute(
                    "SELECT rowid, bm25(fts, 10.0, 4.0, 3.0, 1.0, 1.0, 0.5, 0.5, 2.0) "
                    "FROM fts WHERE fts MATCH ?", (pos,))}
            else:
                found = {i: 0.0 for i in self.rows}
            if neg:
                for r in db.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (neg,)):
                    found.pop(r[0], None)
            return found
        except sqlite3.OperationalError:
            # malformed expression: fall back to a plain substring search
            low = text.lower()
            return {i: 1.0 for i, r in self.rows.items()
                    if low in " ".join(str(v) for v in r.values() if v).lower()}
        finally:
            db.close()

    def snippets(self, text: str, ids: list[int]) -> dict[int, str]:
        pos, _ = fts_query(text)
        if not pos or not ids:
            return {}
        db = self.connect()
        try:
            q = (f"SELECT rowid, snippet(fts, -1, '<mark>', '</mark>', '…', 14) FROM fts "
                 f"WHERE fts MATCH ? AND rowid IN ({','.join('?' * len(ids))})")
            return {r[0]: r[1] for r in db.execute(q, [pos, *ids])}
        except sqlite3.OperationalError:
            return {}
        finally:
            db.close()

    # ------------------------------------------------------------------ filters
    def _filters(self, q: dict) -> dict[str, Callable[[int, dict], bool]]:
        """Named predicates; each facet is computed without its own predicate."""
        f: dict[str, Callable[[int, dict], bool]] = {}

        if q.get("favorites_only"):
            favs = set(self.favorites)
            f["favorites"] = lambda i, r: i in favs

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
        text = (q.get("text") or "").strip()
        mode = q.get("text_mode") or "keyword"

        # --- relevance
        kw: dict[int, float] | None = None
        sem: dict[int, float] | None = None
        if text and mode in ("keyword", "hybrid"):
            kw = self.fts_ids(text)
        if text and mode in ("semantic", "hybrid"):
            if not self.ai.configured:
                warnings.append("La recherche IA n'est pas configurée : ajoutez un jeton dans "
                                "Réglages IA. Recherche par mots-clés utilisée à la place.")
            elif not self.semantic.ready:
                warnings.append("L'index IA n'est pas encore construit : cliquez sur « Construire "
                                "l'index IA ». Recherche par mots-clés utilisée à la place.")
            else:
                try:
                    sem = self.semantic.scores(text, self.ai)
                except Exception as exc:
                    warnings.append(f"La recherche IA a échoué ({exc}) ; recherche par mots-clés "
                                    "utilisée à la place.")
            if sem is None and kw is None:
                kw = self.fts_ids(text)

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
        snips = self.snippets(text, page) if text and kw is not None else {}
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
            item["locations"] = [{"lat": l["lat"], "lon": l["lon"], "label": l["label"]}
                                 for l in self.locations.get(i, []) if l["lat"] is not None]
            items.append(item)

        points = []
        for i in order:
            r = self.rows[i]
            for l in self.locations.get(i, []):
                if l["lat"] is not None:
                    points.append([i, round(l["lat"], 5), round(l["lon"], 5), r["title"],
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
                "ai_index": {"ready": self.semantic.ready, "vectors": len(self.semantic.ids),
                             "model": self.semantic.model, **self.semantic.progress}}
