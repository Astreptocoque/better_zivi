"""Synthetic data shaped like the ZiviConnect export, and an offline postcode table."""

from __future__ import annotations

import json

import pytest

from zivi import converter
from zivi.geocode import GeoResult, Geocoder, PostcodeTable


def coded(code, de, fr, it=None):
    return {"code": code, "de": de, "fr": fr, "it": it or de}


PLACES = {  # plz: (city, lat, lon, canton)
    "3800": ("Interlaken", 46.686, 7.863, "BE"),
    "1003": ("Lausanne", 46.519, 6.633, "VD"),
    "8001": ("Zürich", 47.372, 8.541, "ZH"),
    "6500": ("Bellinzona", 46.195, 9.023, "TI"),
    "1950": ("Sion", 46.233, 7.360, "VS"),
}


def make_record(i, *, title, plz, tasks, task_areas, weeks, housing="IE", meals="DE",
                weekend="N", night="N", leumund=False, min_weeks=4, addr_style=0,
                country=None):
    city = PLACES[plz][0] if plz in PLACES else "Addis Abeba"
    if addr_style == 0:
        addr = {"strasse": "Alpenstrasse 45", "plz": plz, "ort": city}
    elif addr_style == 1:
        addr = {"adresszeile1": "Rue X 1", "ort": {"plz": int(plz), "name": city}}
    else:
        addr = {"zeile": f"Weg 3, {plz} {city}"}
    if country:
        addr["land"] = coded(country, "Äthiopien", "Ethiopie")
    return {
        "id": 5000000 + i, "number": 110000 + i, "title": title,
        "sector": coded("2", "2 Sozialwesen", "2 Social"),
        "taskAreas": [coded(c, l, l) for c, l in task_areas],
        "place": city, "employer": f"Employer {i}", "employerId": 100 + i,
        "url": f"https://ziviconnect.admin.ch/zdp/pflichtenheft/{5000000 + i}",
        "detail": {
            "id": 5000000 + i, "name": title, "pflichtenheftNummer": 110000 + i,
            "eibName": f"Employer {i}", "eibNummer": 1,
            "eibAdresse": {"strasse": "Hauptweg 1", "plz": plz, "ort": city},
            "eibBetriebsbeschreibung": "Une institution qui accompagne des personnes.",
            "bemerkungEinsatzadresse": city,
            "taetigkeitList": [{"prozent": 90, "taetigkeit": tasks}],
            "mindestdauerEinsatzInWochen": min_weeks, "wochenarbeitszeitInStunden": 42,
            "arbeitszeitmodell": coded("FIX", "Feste Arbeitszeit", "Horaire de travail fixe"),
            "wochenendarbeit": coded(weekend, weekend, weekend),
            "nachtarbeit": coded(night, night, night),
            "abklaerungLeumundErforderlich": leumund,
            "unterkunftAngeboten": coded(housing, housing, housing),
            "verpflegungAngeboten": coded(meals, meals, meals),
            "kursZiviList": [coded("BBM1", "Betagtenbetreuung 1", "Assistance aux personnes âgées 1")],
            "vorausgesetzteGrundkenntnisse": "Geduld, Teamfähigkeit",
            "erwuenschteGrundkenntnisse": None,
            "kontaktPersonName": "Muster", "kontaktPersonVorname": "Max",
            "kontaktPersonFunktion": "Leitung", "kontaktPersonTelefon1": "+41 00 000 00 00",
            "kontaktPersonEmail": "max@example.org", "kontaktaufnahmeList": [],
            "adresseList": [addr], "schwerpunktprogramm": True, "merkliste": False,
            "availableWeeks": weeks,
        },
    }


@pytest.fixture
def sample_records():
    return [
        make_record(1, title="Unterstützung Pflege und Betreuung", plz="3800",
                    tasks="Betreuung von Bewohnenden im Altersheim, Begleitung zum Arzt",
                    task_areas=[("KTMM_ALTER", "Contacts humains / Vieillesse")],
                    weeks=["2027-08-02", "2027-08-09", "2027-08-16", "2027-08-23"],
                    housing="IE", meals="IE", weekend="M"),
        make_record(2, title="Entretien des alpages", plz="1003",
                    tasks="Travail en plein air dans les pâturages, entretien des clôtures",
                    task_areas=[("ARBEIT_IM_FREIEN", "Travail en plein air"),
                                ("KOERPERLICHE_ARBEIT", "Travail physique")],
                    weeks=["2027-09-06", "2027-09-13"], housing="DE", meals="IE",
                    addr_style=1, min_weeks=8),
        make_record(3, title="Mitarbeit Küche", plz="8001",
                    tasks="Mithilfe in der Küche und Reinigung",
                    task_areas=[("KTMM_KULTURGUETERERHALTUNG", "Cuisine / Intendance / Nettoyage")],
                    weeks=["2027-10-04"], housing="DE", meals="DE", night="J", leumund=True,
                    addr_style=2),
        make_record(4, title="Collaboratore servizio cure", plz="6500",
                    tasks="Assistenza agli anziani nella casa per anziani",
                    task_areas=[("KTMM_ALTER", "Contacts humains / Vieillesse")],
                    weeks=["2027-08-02", "2027-08-09"]),
        make_record(5, title="Mechaniker Äthiopien", plz="9999",
                    tasks="Wartung von Maschinen in einem Projekt im Ausland",
                    task_areas=[], weeks=["2027-08-02"], country="ET"),
    ]


@pytest.fixture
def table():
    t = PostcodeTable.__new__(PostcodeTable)
    t.by_plz = {p: GeoResult(la, lo, c, "test-plz") for p, (n, la, lo, c) in PLACES.items()}
    t.by_name = {n.lower(): GeoResult(la, lo, c, "test-name")
                 for p, (n, la, lo, c) in PLACES.items()}
    t.by_name["zurich"] = t.by_plz["8001"]
    t.available = True
    return t


@pytest.fixture
def store(tmp_path, sample_records, table):
    src = tmp_path / "export.json"
    src.write_text(json.dumps({"meta": {"siteTotal": 5}, "assignments": sample_records}),
                   encoding="utf-8")
    db = tmp_path / "zivi.sqlite"
    report = converter.convert(src, db, geocoder_factory=lambda d: Geocoder(d, online=False,
                                                                             table=table),
                               progress=lambda m, f: None)
    return db, report
