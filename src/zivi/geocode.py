"""Geocoding with a persistent cache.

Order of attempts for one address:
1. coordinates already present in the record,
2. Swiss/Liechtenstein postcode table from GeoNames (offline after a one-time download),
3. place-name match in the same table,
4. swisstopo search API (api3.geo.admin.ch), free, no key,
5. OpenStreetMap Nominatim (for assignments abroad), rate-limited to 1 request/second.

Results (including misses) are cached in the SQLite table ``geocache`` so a re-import is instant.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import config

GEONAMES_URL = "https://download.geonames.org/export/zip/{cc}.zip"
SWISSTOPO_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass
class GeoResult:
    lat: float
    lon: float
    canton: str | None = None
    source: str = ""


def norm(text: str | None) -> str:
    """Lowercase, strip accents and punctuation; used to compare place names."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    out = "".join(c if c.isalnum() else " " for c in text)
    return " ".join(out.split())


class PostcodeTable:
    """GeoNames postcode table for CH and LI: postcode/name -> lat, lon, canton."""

    def __init__(self, directory: Path = config.GEONAMES_DIR, download: bool = True):
        self.by_plz: dict[str, GeoResult] = {}
        self.by_name: dict[str, GeoResult] = {}
        self.available = False
        for cc in ("CH", "LI"):
            path = directory / f"{cc}.txt"
            if not path.exists() and download and not config.OFFLINE:
                try:
                    self._download(cc, path)
                except Exception as exc:  # network refused, etc.
                    print(f"[geocode] could not download GeoNames {cc}: {exc}")
            if path.exists():
                self._load(path)
        self.available = bool(self.by_plz)

    @staticmethod
    def _download(cc: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(GEONAMES_URL.format(cc=cc), timeout=60, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            path.write_bytes(zf.read(f"{cc}.txt"))

    def _load(self, path: Path) -> None:
        sums: dict[str, list[float]] = {}
        canton_of: dict[str, str] = {}
        with path.open(encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter="\t"):
                if len(row) < 11:
                    continue
                cc, plz, name, _a1, a1code = row[0], row[1], row[2], row[3], row[4]
                try:
                    lat, lon = float(row[9]), float(row[10])
                except ValueError:
                    continue
                canton = a1code or ("FL" if cc == "LI" else None)
                s = sums.setdefault(plz, [0.0, 0.0, 0])
                s[0] += lat
                s[1] += lon
                s[2] += 1
                canton_of[plz] = canton
                key = norm(name)
                if key and key not in self.by_name:
                    self.by_name[key] = GeoResult(lat, lon, canton, "geonames-name")
        for plz, (la, lo, n) in sums.items():
            self.by_plz[plz] = GeoResult(la / n, lo / n, canton_of.get(plz), "geonames-plz")

    def plz(self, plz: str | None) -> GeoResult | None:
        return self.by_plz.get(str(plz).strip()) if plz else None

    def name(self, name: str | None) -> GeoResult | None:
        key = norm(name)
        if not key:
            return None
        if key in self.by_name:
            return self.by_name[key]
        # "Bern 60 UPD" -> "bern", "Gwatt (Thun)" -> "gwatt"
        first = key.split(" ")[0]
        return self.by_name.get(first)

    def canton_near(self, lat: float, lon: float) -> str | None:
        best, best_d = None, 1e9
        for g in self.by_plz.values():
            d = (g.lat - lat) ** 2 + (g.lon - lon) ** 2
            if d < best_d:
                best, best_d = g.canton, d
        return best if best_d < 0.05 else None


class Geocoder:
    def __init__(self, db: sqlite3.Connection, online: bool | None = None,
                 table: PostcodeTable | None = None):
        self.db = db
        self.online = (not config.OFFLINE) if online is None else online
        self.table = table if table is not None else PostcodeTable()
        self._last_nominatim = 0.0
        self._client: httpx.Client | None = None
        db.execute("""CREATE TABLE IF NOT EXISTS geocache(
            key TEXT PRIMARY KEY, lat REAL, lon REAL, canton TEXT, source TEXT)""")  # same as converter.GEOCACHE_SQL

    # -- cache -----------------------------------------------------------
    def _cached(self, key: str) -> tuple[bool, GeoResult | None]:
        row = self.db.execute("SELECT lat, lon, canton, source FROM geocache WHERE key=?",
                              (key,)).fetchone()
        if row is None:
            return False, None
        if row[0] is None:
            return True, None
        return True, GeoResult(row[0], row[1], row[2], row[3])

    def _store(self, key: str, res: GeoResult | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?,?,?)",
                        (key, res.lat if res else None, res.lon if res else None,
                         res.canton if res else None, res.source if res else "miss"))

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15, headers={"User-Agent": config.USER_AGENT})
        return self._client

    # -- online services ---------------------------------------------------
    def swisstopo(self, text: str) -> GeoResult | None:
        r = self.client.get(SWISSTOPO_URL, params={
            "searchText": text, "type": "locations", "sr": "4326", "limit": 1,
            "origins": "zipcode,gg25,address"})
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            return None
        a = results[0].get("attrs", {})
        lat, lon = a.get("lat"), a.get("lon")
        if lat is None or lon is None:
            return None
        return GeoResult(float(lat), float(lon), None, "swisstopo")

    def nominatim(self, text: str) -> GeoResult | None:
        wait = 1.1 - (time.monotonic() - self._last_nominatim)
        if wait > 0:
            time.sleep(wait)
        self._last_nominatim = time.monotonic()
        r = self.client.get(NOMINATIM_URL, params={"q": text, "format": "json", "limit": 1})
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return GeoResult(float(data[0]["lat"]), float(data[0]["lon"]), None, "nominatim")

    # -- public API --------------------------------------------------------
    def locate(self, *, plz: str | None = None, city: str | None = None,
               street: str | None = None, country: str | None = None,
               free_text: str | None = None) -> GeoResult | None:
        """Best-effort coordinates for an address. Never raises."""
        foreign = bool(country) and norm(country) not in {"ch", "che", "schweiz", "suisse",
                                                         "svizzera", "switzerland", "li",
                                                         "liechtenstein"}
        key = "|".join(norm(x) for x in (plz, city, country, free_text) if x)
        if not key:
            return None
        hit, res = self._cached(key)
        if hit:
            return res
        res = None
        if not foreign:
            res = self.table.plz(plz) if plz else None
            if res is None:
                res = self.table.name(city) or self.table.name(free_text)
        if res is None and self.online:
            query = " ".join(x for x in (str(plz or ""), city or "", country or "") if x).strip()
            query = query or (free_text or "")
            try:
                if not foreign:
                    res = self.swisstopo(query)
                if res is None:
                    res = self.nominatim(query + ("" if foreign else " Switzerland"))
            except Exception as exc:
                print(f"[geocode] {query!r}: {exc}")
                return None  # do not cache network failures
        if res is not None and res.canton is None and self.table.available:
            res.canton = self.table.canton_near(res.lat, res.lon)
        self._store(key, res)
        return res

    def search_place(self, text: str) -> list[dict]:
        """For the map search box: candidate places for free text (postcode or name)."""
        text = (text or "").strip()
        out: list[dict] = []
        if text.isdigit():
            g = self.table.plz(text)
            if g:
                out.append({"label": text, "lat": g.lat, "lon": g.lon, "source": g.source})
        else:
            key = norm(text)
            for name, g in self.table.by_name.items():
                if name == key or name.startswith(key):
                    out.append({"label": name.title(), "lat": g.lat, "lon": g.lon,
                                "source": g.source})
                    if len(out) >= 8:
                        break
        if not out and self.online:
            try:
                r = self.client.get(SWISSTOPO_URL, params={
                    "searchText": text, "type": "locations", "sr": "4326", "limit": 8})
                for item in (r.json().get("results") or []):
                    a = item.get("attrs", {})
                    if a.get("lat") is not None:
                        label = a.get("label", text)
                        label = label.replace("<b>", "").replace("</b>", "")
                        out.append({"label": label, "lat": a["lat"], "lon": a["lon"],
                                    "source": "swisstopo"})
            except Exception as exc:
                print(f"[geocode] place search {text!r}: {exc}")
        if not out and self.online:
            try:
                g = self.nominatim(text)
                if g:
                    out.append({"label": text, "lat": g.lat, "lon": g.lon, "source": g.source})
            except Exception:
                pass
        return out
