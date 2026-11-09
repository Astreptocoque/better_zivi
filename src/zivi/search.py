"""Search engine over the converted SQLite store.

The ~4000 assignments are held in memory; filtering is plain Python and full-text search
uses SQLite FTS5 (bm25 ranking, accent-insensitive). Facet counts are computed per field
while ignoring that field's own filter, so multi-select filters behave as expected.
"""

from __future__ import annotations

import math
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
        self.load()

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
                    import json
                    self.report = json.loads(rep[0])
                self.semantic.load(db, self.ai.model)
            finally:
                db.close()

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
                warnings.append("AI search is not configured: add a token in the AI panel. "
                                "Falling back to keyword search.")
            elif not self.semantic.ready:
                warnings.append("The AI index is not built yet: click 'Build AI index'. "
                                "Falling back to keyword search.")
            else:
                try:
                    sem = self.semantic.scores(text, self.ai)
                except Exception as exc:
                    warnings.append(f"AI search failed ({exc}); keyword search used instead.")
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
                                   r["employer"], l["label"] or r["place"]])
        unlocated = sum(1 for i in matched
                        if not any(l["lat"] is not None for l in self.locations.get(i, [])))
        return {"total": len(matched), "offset": offset, "items": items, "points": points,
                "unlocated": unlocated, "facets": facets, "sort": sort, "warnings": warnings,
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
                "ai_index": {"ready": self.semantic.ready, "vectors": len(self.semantic.ids),
                             "model": self.semantic.model, **self.semantic.progress}}
