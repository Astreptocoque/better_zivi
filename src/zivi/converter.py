"""Convert a ZiviConnect JSON export into an indexed SQLite store.

Accepted inputs:
* ``{"meta": {...}, "assignments": [...]}`` (full export with ``detail`` per assignment),
* a plain list of such assignments,
* the early 150-record export (``fullText`` instead of ``detail``),
* raw detail objects (``id``, ``name``, ``pflichtenheftNummer`` ... at top level).

Fields are recognised by name, so small changes in the site's structure do not break the
import; a report of unknown/missing fields is printed at the end.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .geocode import Geocoder

Progress = Callable[[str, float], None]

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE assignments(
    id INTEGER PRIMARY KEY,
    number INTEGER, title TEXT, employer TEXT, employer_id INTEGER, place TEXT, url TEXT,
    sector TEXT, min_weeks INTEGER, hours_per_week REAL, work_model TEXT,
    weekend TEXT, night TEXT, housing INTEGER, meals INTEGER, reputation_check INTEGER,
    priority_program INTEGER, abroad INTEGER, language TEXT, canton TEXT,
    contact_name TEXT, contact_role TEXT, contact_phone TEXT, contact_email TEXT,
    employer_address TEXT, employer_description TEXT,
    tasks TEXT, required_skills TEXT, desired_skills TEXT, courses TEXT, how_to_apply TEXT,
    first_week TEXT, last_week TEXT, n_weeks INTEGER,
    raw TEXT
);
CREATE TABLE task_areas(assignment_id INTEGER, code TEXT, label TEXT);
CREATE TABLE weeks(assignment_id INTEGER, week TEXT);
CREATE TABLE locations(assignment_id INTEGER, label TEXT, street TEXT, plz TEXT, city TEXT,
                       country TEXT, lat REAL, lon REAL, canton TEXT, source TEXT);
CREATE VIRTUAL TABLE fts USING fts5(
    title, employer, place, tasks, skills, employer_description, courses, task_areas,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE INDEX ix_ta ON task_areas(assignment_id);
CREATE INDEX ix_weeks ON weeks(assignment_id);
CREATE INDEX ix_loc ON locations(assignment_id);
"""

GEOCACHE_SQL = """CREATE TABLE IF NOT EXISTS geocache(
    key TEXT PRIMARY KEY, lat REAL, lon REAL, canton TEXT, source TEXT)"""
# One vector per assignment and model; text_hash lets a re-import skip unchanged texts.
EMBEDDINGS_SQL = """CREATE TABLE IF NOT EXISTS embeddings(
    assignment_id INTEGER, model TEXT, text_hash TEXT, vec BLOB,
    PRIMARY KEY(assignment_id, model))"""

# --------------------------------------------------------------------------- helpers

CODED_TEXT_KEYS = ("fr", "de", "it", "textFr", "textDe", "textIt")
YES = {"j", "ja", "oui", "yes", "true", "1", "ie", "y"}
NO = {"n", "nein", "non", "no", "false", "0", "de"}


def coded(v: Any) -> tuple[str | None, str | None]:
    """Return (code, label) for a coded value like {code, de, fr, it} / {code, textDe ...}."""
    if v is None:
        return None, None
    if isinstance(v, dict):
        code = v.get("code")
        label = next((v[k] for k in CODED_TEXT_KEYS if v.get(k)), None)
        return (str(code) if code is not None else None), label
    return str(v), str(v)


def yes_no(v: Any) -> int | None:
    """Booleans and coded yes/no values (the site uses IE=yes, DE=no for housing/meals)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    code, label = coded(v)
    for s in (code, label):
        if s is None:
            continue
        s = str(s).strip().lower()
        if s in YES:
            return 1
        if s in NO:
            return 0
    return None


def jnm(v: Any) -> str | None:
    """Weekend / night work: J (yes), N (no), M (possible)."""
    code, label = coded(v)
    s = (code or label or "").strip().upper()
    if s in {"J", "N", "M"}:
        return s
    low = (label or "").lower()
    if low.startswith(("oui", "ja", "si")):
        return "J"
    if low.startswith(("non", "nein", "no")):
        return "N"
    if low.startswith(("poss", "mög", "mog")):
        return "M"
    return None


SKIP_KEYS = {"id", "version", "domain", "code", "sort", "merkliste", "eibId", "eibNummer"}


def flatten_text(v: Any, prefer_lang: tuple[str, ...] = ("fr", "de", "it")) -> str:
    """All human-readable text inside a value, one line per string."""
    out: list[str] = []

    def walk(x: Any) -> None:
        if x is None:
            return
        if isinstance(x, str):
            s = x.strip()
            if s:
                out.append(s)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            return
        elif isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            if "code" in x and any(k in x for k in CODED_TEXT_KEYS):
                _, label = coded(x)
                if label:
                    out.append(label)
                return
            pct = next((x[k] for k in x if "prozent" in k.lower() or "anteil" in k.lower()
                        or k.lower() in {"percent", "pourcentage"}), None)
            parts: list[str] = []
            for k, val in x.items():
                if k in SKIP_KEYS or "prozent" in k.lower() or "anteil" in k.lower():
                    continue
                before = len(out)
                walk(val)
                parts.extend(out[before:])
                del out[before:]
            if parts:
                if isinstance(pct, (int, float)):
                    parts[0] = f"{pct:g}% {parts[0]}"
                out.append("\n".join(parts))

    walk(v)
    return "\n".join(out)


def find_key(d: dict, *names: str) -> Any:
    """First value whose key matches one of ``names`` (case-insensitive), searched recursively."""
    targets = {n.lower() for n in names}
    stack: list[Any] = [d]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in targets and v not in (None, "", []):
                    return v
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


STOP = {
    "de": {"der", "die", "und", "mit", "für", "ist", "im", "zu", "von", "den", "das", "bei",
           "sie", "wir", "auf", "des", "ein", "eine", "unterstützung"},
    "fr": {"le", "la", "les", "et", "des", "pour", "avec", "du", "une", "est", "dans", "au",
           "aux", "sur", "nous", "vous", "en"},
    "it": {"il", "lo", "gli", "e", "per", "con", "del", "della", "delle", "degli", "che",
           "una", "di", "nel", "nella", "nelle", "alla", "alle", "ai", "al", "dei", "sono",
           "collaboratore", "assistenza", "anziani", "cure"},
}


def detect_language(text: str) -> str | None:
    """Rough DE/FR/IT guess from stop words (the export has no language field)."""
    words = re.findall(r"[a-zàâäéèêëîïôöùûüç]+", text.lower())
    if len(words) < 3:
        return None
    scores = sorted(((sum(w in s for w in words), lang) for lang, s in STOP.items()),
                    reverse=True)
    (best, lang), (second, _) = scores[0], scores[1]
    return lang if best >= 2 and best > second else None


PLZ_RE = re.compile(r"\b(\d{4})\b\s+([A-Za-zÀ-ÿ][^\n,;]*)")


def extract_addresses(detail: dict) -> list[dict]:
    """Addresses of the assignment (adresseList), falling back to the employer address."""
    raw = detail.get("adresseList") or detail.get("einsatzadresseList") or []
    if isinstance(raw, dict):
        raw = [raw]
    addrs = [parse_address(a) for a in raw if a]
    addrs = [a for a in addrs if a.get("plz") or a.get("city") or a.get("lat")]
    if not addrs and detail.get("eibAdresse"):
        a = parse_address(detail["eibAdresse"])
        if a.get("plz") or a.get("city") or a.get("lat"):
            a["label"] = "employer"
            addrs.append(a)
    return addrs


def parse_address(a: Any) -> dict:
    if isinstance(a, str):
        m = PLZ_RE.search(a)
        return {"plz": m.group(1), "city": m.group(2).strip(), "label": a} if m else {"city": a}
    if not isinstance(a, dict):
        return {}
    plz = find_key(a, "plz", "postleitzahl", "zip", "npa", "postcode", "postalcode")
    city = find_key(a, "ort", "ortschaft", "ortbezeichnung", "city", "localite", "gemeinde",
                    "ortname", "place")
    if isinstance(city, dict):
        plz = plz or find_key(city, "plz", "postleitzahl", "zip")
        city = find_key(city, "name", "bezeichnung", "text", "de", "fr")
    street = find_key(a, "strasse", "street", "rue", "adresszeile1", "adresse")
    if isinstance(street, dict):
        street = flatten_text(street)
    nr = find_key(a, "hausnummer", "housenumber", "nr")
    if street and nr and str(nr) not in str(street):
        street = f"{street} {nr}"
    country = find_key(a, "land", "country", "pays", "staat")
    if isinstance(country, dict):
        code, label = coded(country)
        country = code or label
    lat = find_key(a, "koordLat", "lat", "latitude")
    lon = find_key(a, "koordLong", "lon", "lng", "longitude")
    if not plz and not city:
        text = flatten_text(a)
        m = PLZ_RE.search(text)
        if m:
            plz, city = m.group(1), m.group(2).strip()
    out = {"plz": str(plz).strip() if plz else None,
           "city": str(city).strip() if city else None,
           "street": str(street).strip() if street else None,
           "country": str(country).strip() if country else None}
    try:
        if lat is not None and lon is not None:
            out["lat"], out["lon"] = float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    out["label"] = ", ".join(x for x in (out["street"], " ".join(
        x for x in (out["plz"], out["city"]) if x)) if x)
    return out


def iso_week(s: str) -> str | None:
    """Normalise a week start like '2027-08-02' or '02.08.2027' to ISO."""
    s = str(s).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10]
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def weeks_from_fulltext(text: str) -> list[str]:
    """Parse 'Périodes disponibles' from page text (early export): 'August 2027' then '02.08'."""
    months = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
              "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
              "november": 11, "dezember": 12, "janvier": 1, "février": 2, "mars": 3,
              "avril": 4, "juin": 6, "juillet": 7, "août": 8, "septembre": 9,
              "octobre": 10, "décembre": 12}
    out, year = [], None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"([A-Za-zäéû]+)\s+(\d{4})$", line)
        if m and m.group(1).lower() in months:
            year = int(m.group(2))
            continue
        m = re.match(r"(\d{2})\.(\d{2})$", line)
        if m and year:
            out.append(f"{year}-{m.group(2)}-{m.group(1)}")
    return out


# --------------------------------------------------------------------------- records

def iter_records(data: Any) -> Iterable[dict]:
    if isinstance(data, dict):
        for key in ("assignments", "items", "results", "data"):
            if isinstance(data.get(key), list):
                yield from data[key]
                return
        yield data
    elif isinstance(data, list):
        yield from data


def normalise(rec: dict) -> dict:
    """One input record -> flat row + lists (task areas, weeks, addresses)."""
    detail = rec.get("detail") if isinstance(rec.get("detail"), dict) else None
    if detail is None and "pflichtenheftNummer" in rec and "name" in rec:
        detail = rec  # raw detail object
    d = detail or {}

    rid = rec.get("id") or rec.get("pflichtenheftId") or d.get("id")
    number = rec.get("number") or rec.get("pflichtenheftNummer") or d.get("pflichtenheftNummer")
    title = rec.get("title") or rec.get("pflichtenheftTitel") or d.get("name")

    task_areas = []
    for ta in rec.get("taskAreas") or rec.get("aufgabenbereiche") or []:
        code, label = coded(ta)
        task_areas.append((code or label, label or code))

    sector_code, sector = coded(rec.get("sector") or rec.get("taetigkeitsbereich"))

    weeks = [w for w in (iso_week(x) for x in (d.get("availableWeeks") or [])) if w]
    if not weeks and rec.get("fullText"):
        weeks = weeks_from_fulltext(rec["fullText"])
    weeks = sorted(set(weeks))

    tasks = flatten_text(d.get("taetigkeitList")) if d else ""
    if not tasks and rec.get("fullText"):
        tasks = rec["fullText"]
    contact_name = " ".join(x for x in (d.get("kontaktPersonVorname"),
                                        d.get("kontaktPersonName")) if x)
    _, work_model = coded(d.get("arbeitszeitmodell"))
    courses = flatten_text(d.get("kursZiviList"))
    language = detect_language(" ".join([title or "", tasks]))

    row = {
        "id": int(rid) if rid is not None else None,
        "number": int(number) if number is not None else None,
        "title": title,
        "employer": rec.get("employer") or rec.get("eibName") or d.get("eibName"),
        "employer_id": rec.get("employerId") or rec.get("eibId"),
        "place": rec.get("place") or rec.get("bemerkungEinsatzadresse")
                 or d.get("bemerkungEinsatzadresse"),
        "url": rec.get("url") or (f"https://ziviconnect.admin.ch/zdp/pflichtenheft/{rid}"
                                  if rid else None),
        "sector": sector,
        "min_weeks": d.get("mindestdauerEinsatzInWochen"),
        "hours_per_week": d.get("wochenarbeitszeitInStunden"),
        "work_model": work_model,
        "weekend": jnm(d.get("wochenendarbeit")),
        "night": jnm(d.get("nachtarbeit")),
        "housing": yes_no(d.get("unterkunftAngeboten")),
        "meals": yes_no(d.get("verpflegungAngeboten")),
        "reputation_check": yes_no(d.get("abklaerungLeumundErforderlich")),
        "priority_program": yes_no(d.get("schwerpunktprogramm")),
        "abroad": None,
        "language": language,
        "canton": None,
        "contact_name": contact_name or None,
        "contact_role": d.get("kontaktPersonFunktion"),
        "contact_phone": d.get("kontaktPersonTelefon1"),
        "contact_email": d.get("kontaktPersonEmail"),
        "employer_address": flatten_text(d.get("eibAdresse")).replace("\n", ", ") or None,
        "employer_description": d.get("eibBetriebsbeschreibung"),
        "tasks": tasks,
        "required_skills": flatten_text(d.get("vorausgesetzteGrundkenntnisse")) or None,
        "desired_skills": flatten_text(d.get("erwuenschteGrundkenntnisse")) or None,
        "courses": courses or None,
        "how_to_apply": flatten_text(d.get("kontaktaufnahmeList")) or None,
        "first_week": weeks[0] if weeks else None,
        "last_week": weeks[-1] if weeks else None,
        "n_weeks": len(weeks),
        "raw": json.dumps(rec, ensure_ascii=False),
    }
    return {"row": row, "task_areas": task_areas, "weeks": weeks,
            "addresses": extract_addresses(d) if d else []}


# --------------------------------------------------------------------------- import

KNOWN_DETAIL_KEYS = {
    "id", "name", "pflichtenheftNummer", "eibName", "eibNummer", "eibAdresse",
    "eibBetriebsbeschreibung", "bemerkungEinsatzadresse", "taetigkeitList",
    "mindestdauerEinsatzInWochen", "wochenarbeitszeitInStunden", "arbeitszeitmodell",
    "wochenendarbeit", "nachtarbeit", "abklaerungLeumundErforderlich", "unterkunftAngeboten",
    "verpflegungAngeboten", "kursZiviList", "vorausgesetzteGrundkenntnisse",
    "erwuenschteGrundkenntnisse", "kontaktPersonName", "kontaktPersonVorname",
    "kontaktPersonFunktion", "kontaktPersonTelefon1", "kontaktPersonEmail",
    "kontaktaufnahmeList", "adresseList", "schwerpunktprogramm", "merkliste", "availableWeeks",
}


def convert(json_path: str | Path, db_path: str | Path, *, geocoder_factory=None,
            progress: Progress | None = None) -> dict:
    """Build a fresh database at ``db_path`` from ``json_path``. Returns a summary report.

    The geocoding cache of an existing database is kept, so re-imports are fast.
    """
    say = progress or (lambda msg, frac: print(f"[{frac:5.0%}] {msg}"))
    json_path, db_path = Path(json_path), Path(db_path)
    say("Lecture du JSON", 0.0)
    with json_path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    records = list(iter_records(data))
    if not records:
        raise ValueError("Aucune affectation trouvée dans ce fichier JSON.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    # keep the geocoding cache and AI embeddings from a previous import
    if db_path.exists():
        db.execute("ATTACH DATABASE ? AS old", (str(db_path),))
        old_tables = {r[0] for r in db.execute(
            "SELECT name FROM old.sqlite_master WHERE type='table'")}
        for table, ddl in (("geocache", GEOCACHE_SQL), ("embeddings", EMBEDDINGS_SQL)):
            if table in old_tables:
                db.execute(ddl)
                db.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM old.{table}")
        db.commit()
        db.execute("DETACH DATABASE old")

    unknown: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    normalised = []
    seen: set[int] = set()
    for i, rec in enumerate(records):
        n = normalise(rec)
        if n["row"]["id"] is None or n["row"]["id"] in seen:
            continue
        seen.add(n["row"]["id"])
        normalised.append(n)
        det = rec.get("detail") if isinstance(rec.get("detail"), dict) else None
        if det:
            unknown.update(k for k in det if k not in KNOWN_DETAIL_KEYS)
            missing.update(k for k in KNOWN_DETAIL_KEYS - {"merkliste"} if k not in det)
        if i % 500 == 0:
            say(f"Analyse des fiches {i}/{len(records)}", 0.05 + 0.15 * i / len(records))

    say(f"Écriture de {len(normalised)} affectations", 0.2)
    cols = list(normalised[0]["row"].keys())
    db.executemany(f"INSERT INTO assignments({','.join(cols)}) VALUES "
                   f"({','.join('?' * len(cols))})",
                   [tuple(n["row"][c] for c in cols) for n in normalised])
    db.executemany("INSERT INTO task_areas VALUES (?,?,?)",
                   [(n["row"]["id"], c, l) for n in normalised for c, l in n["task_areas"]])
    db.executemany("INSERT INTO weeks VALUES (?,?)",
                   [(n["row"]["id"], w) for n in normalised for w in n["weeks"]])

    # ---- geocoding
    geocoder = geocoder_factory(db) if geocoder_factory else Geocoder(db)
    total = len(normalised)
    located = 0
    loc_rows = []
    for i, n in enumerate(normalised):
        row = n["row"]
        addrs = n["addresses"] or ([{"city": row["place"], "label": row["place"]}]
                                   if row["place"] else [])
        cantons = []
        has_loc = False
        for a in addrs:
            if "lat" in a:
                g_lat, g_lon, canton, source = a["lat"], a["lon"], None, "record"
                if geocoder.table.available:
                    canton = geocoder.table.canton_near(g_lat, g_lon)
            else:
                g = geocoder.locate(plz=a.get("plz"), city=a.get("city"),
                                    street=a.get("street"), country=a.get("country"),
                                    free_text=None if (a.get("plz") or a.get("city"))
                                    else a.get("label"))
                g_lat, g_lon = (g.lat, g.lon) if g else (None, None)
                canton, source = (g.canton, g.source) if g else (None, "miss")
            loc_rows.append((row["id"], a.get("label"), a.get("street"), a.get("plz"),
                             a.get("city"), a.get("country"), g_lat, g_lon, canton, source))
            has_loc = has_loc or g_lat is not None
            if canton:
                cantons.append(canton)
        abroad = any(a.get("country") and a["country"].upper() not in {"CH", "LI", "CHE"}
                     and a["country"].lower() not in {"schweiz", "suisse", "svizzera"}
                     for a in addrs)
        if not abroad and re.search(r"\b(ausland|étranger|estero)\b|Äthiopien|Ethiopie",
                                    f"{row['title']} {row['place']}", re.I):
            abroad = True
        located += has_loc
        db.execute("UPDATE assignments SET canton=?, abroad=? WHERE id=?",
                   (",".join(sorted(set(cantons))) or None, int(abroad), row["id"]))
        if i % 100 == 0:
            db.commit()
            say(f"Géolocalisation {i}/{total}", 0.25 + 0.65 * i / total)
    db.executemany("INSERT INTO locations VALUES (?,?,?,?,?,?,?,?,?,?)", loc_rows)

    # ---- full-text index
    say("Construction de l'index plein texte", 0.92)
    ta_map: dict[int, list[str]] = {}
    for n in normalised:
        ta_map[n["row"]["id"]] = [l or c for c, l in n["task_areas"]]
    db.executemany(
        "INSERT INTO fts(rowid, title, employer, place, tasks, skills, employer_description, "
        "courses, task_areas) VALUES (?,?,?,?,?,?,?,?,?)",
        [(n["row"]["id"], n["row"]["title"], n["row"]["employer"], n["row"]["place"],
          n["row"]["tasks"],
          "\n".join(x for x in (n["row"]["required_skills"], n["row"]["desired_skills"]) if x),
          n["row"]["employer_description"], n["row"]["courses"],
          ", ".join(ta_map[n["row"]["id"]])) for n in normalised])

    report = {
        "source_file": json_path.name,
        "assignments": len(normalised),
        "with_details": sum(1 for n in normalised if n["row"]["min_weeks"] is not None
                            or n["row"]["tasks"]),
        "with_location": located,
        "locations": len(loc_rows),
        "with_weeks": sum(1 for n in normalised if n["weeks"]),
        "unknown_detail_fields": dict(unknown.most_common(20)),
        "fields_missing_in_some_records": {k: v for k, v in missing.items() if v},
        "source_meta": meta,
    }
    db.executemany("INSERT INTO meta VALUES (?,?)",
                   [("report", json.dumps(report, ensure_ascii=False)),
                    ("imported_from", str(json_path))])
    db.commit()
    db.close()
    # replace the old database atomically (embeddings are rebuilt on demand)
    if db_path.exists():
        backup = db_path.with_suffix(".previous")
        shutil.copyfile(db_path, backup)
        db_path.unlink()
    tmp.rename(db_path)
    say(f"Terminé : {report['assignments']} affectations, {located} localisées", 1.0)
    return report


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())
