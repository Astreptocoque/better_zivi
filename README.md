# Zivi search

A local search engine and map for ZiviConnect civil-service assignments
(*cahiers des charges*). Load the JSON export, then filter by every attribute, search the
text, and see every result on a map of Switzerland. The interface is in French, German or
Italian.

![Filters, results and map, interface in French](docs/screenshot.png)

<table>
  <tr>
    <td width="33%"><img src="docs/screenshot-rarity.png" alt="Rarest assignments first, filtered on night work, interface in German"></td>
    <td width="33%"><img src="docs/screenshot-detail.png" alt="An assignment in German, interface in Italian"></td>
    <td width="33%"><img src="docs/screenshot-ai.png" alt="AI search settings with a local Ollama model"></td>
  </tr>
  <tr>
    <td>✨ Rare traits first, filtered on night work (German)</td>
    <td>Detail panel, with its rare traits and a translate button (Italian)</td>
    <td>AI search settings with a local Ollama model (French)</td>
  </tr>
</table>

The screenshots use fictional data (made-up establishments and contacts in real Swiss towns).

## Quick start (Windows)

You need [uv](https://docs.astral.sh/uv/). Then, in PowerShell or cmd:

```powershell
git clone https://github.com/Astreptocoque/zivi_search.git
cd zivi_search
uv sync
uv run zivi serve
```

Your browser opens at http://127.0.0.1:8765. Click **Base de données** → **Charger un export
JSON…** and pick a ZiviConnect export (see [Getting the data](#getting-the-data)), or
`example/zivi_database_example.json` to try the app first. The first import downloads the
Swiss postcode table once (GeoNames, ~1 MB) to place every assignment on the map; this takes
about a minute. Re-imports reuse the cached locations.

You can also import from the terminal:

```powershell
uv run zivi import example\zivi_database_example.json
uv run zivi search "alpage" --near Lausanne --radius 40 --housing
```

## Getting the data

The assignments come from [ZiviConnect](https://ziviconnect.admin.ch) and are only visible
to people logged in there, so **the data is not included in this repository**: it belongs to
the platform, needs an account, and contains the contact persons' names, phones and e-mails.

The app works from a JSON export of the assignments, in the same format as
`example/zivi_database_example.json`. Load it with **Base de données → Charger un export
JSON…**, or from the terminal with `uv run zivi import <file>.json`.

To try the app without an account, load `example/zivi_database_example.json`: 5 real
assignments (2 in German, 2 in French, 1 in Italian) with fictional contact persons.

## Languages

The flag menu in the top bar switches the whole interface between **Français**, **Deutsch**
and **Italiano**: texts, numbers and dates, and the coded values of the data (sectors, task
types, working-hours models) in their official wording from the export. The first visit uses
the browser's language. The interface language also chooses which translation of the
assignments is shown and which AI index is used (below).

## What you can filter

| Filter | Notes |
|---|---|
| Text | Title, tasks, skills, employer, place, courses, and the metadata: contact person (name, role, e-mail, phone in any format: `041 980 61 83`, `+41 41 980 61 83`, `0419806183`), how to apply, employer and assignment addresses, assignment number, sector, working-hours model, canton. Words from different fields combine (`alpage dupont`). Accent-insensitive, prefix matching (`pflege` finds *Pflegeheim*), `"exact phrase"`, `-exclude`, `a OR b` (OR binds tighter than the other words: `alpage chien OR chat` = alpage and one of chien/chat). Hover the search box for a reminder; the ▾ button opens an advanced form with one field per option. Matches are highlighted. |
| Period | Assignments with a start week between two dates; optionally *N weeks in a row* starting in that window, or *available every week* of the period. |
| Around places | Any number of towns or postcodes, each with its own radius, or click the map to add a point. Results are sorted by distance. "Only what is visible on the map" filters to the current map view. |
| Conditions | Housing, meals, reputation check, priority programme, abroad (yes / no / any); weekend and night work (yes / possible / no); no civil-service course required. |
| Duration and hours | Minimum assignment duration (weeks), hours per week. |
| Types of tasks | Any or all of the selected types. |
| Canton, language, sector, working-hours model | Language is guessed from the description (the export has no language field). |

Every filter shows how many results each choice would give. All results, not just the
current page, are drawn on the map, coloured by sector; when zoomed out, nearby assignments
are grouped into pie-chart bubbles split by sector. The sector filter shows each sector's
colour.

**✨ Raretés** (next to the sort menu) puts first the assignments with unusual traits:
values that at most 5 % of all assignments share, such as abroad, a 1-week minimum, night
work or a rare type of task. Each result shows its rare traits with their share; the
detail panel lists them under *Ce qui la rend rare*. Click the button again to go back to
the previous sort. Click a rare trait to show every assignment that shares it; click several
to combine them. The chosen traits sit on top of the list, each with a × to remove it.

Click the star ☆ on any assignment (list, map popup or detail panel) to mark it as a
favourite. **Favoris** in the top bar then shows only your favourites: the list, the map and
the filter counts are all limited to them. Favourites are stored in `data/favorites.json`
and survive re-imports. **Export CSV** downloads the
current results with contact details (opens directly in Excel).

## The Base de données window

**Base de données** in the top bar opens a window with three sections:

- **Base de données**: counts, the imported file and its date, where the database file is,
  **Charger un export JSON…**, and under *Options avancées* buttons to clear the favourites,
  the translations or the AI index.
- **Traduction**: translates the assignments into the interface language (below).
- **Recherche IA**: the AI search settings and index (below).

## Translating the whole database

**Base de données → Traduction → Traduire la base** translates the titles, tasks, skills,
employer descriptions, courses and application notes of every assignment written in another
language into the **interface language** (into French: ~9,800 distinct texts, ~3 million
characters), on this computer: free, and nothing is sent anywhere. Titles go first. Each text
is saved as it is done (`translations` table, kept across re-imports), so **Pause** and
**Reprendre** pick up where it stopped. Each language keeps its own translations: switching
the interface to German and translating again adds German ones without touching the French.

Two engines:

- **Ollama** (recommended): a local translation model,
  [TranslateGemma](https://ollama.com/library/translategemma) by default (`translategemma:4b`,
  3.3 GB; `12b`, 8.1 GB, translates better but needs ~16 GB of memory). The job runs on the
  server, so it continues even when the page is closed. The Ollama box detects Ollama, starts
  it if it is installed but not running, downloads the model with a progress bar, or links to
  [ollama.com/download](https://ollama.com/download) when Ollama is not installed. A text
  that keeps failing is set aside and retried on the next run.
- **Navigateur**: the browser's built-in translator (recent Chrome or Edge), no install, but
  slow, and the page must stay open.

Options:

- **Afficher les affectations dans la langue de l'interface** shows the translated titles in
  the list and on the map, and opens entries translated (with *Voir l'original*).
- **Chercher aussi dans le texte original** (on by default) searches the translated text and
  the original. Turn it off to search only text in the interface language (originals in that
  language plus translations).

Without the whole-database translation, the **Traduire** button in an entry translates just
that entry on the fly with the browser's translator.

## AI search (optional)

**Base de données → Recherche IA** → choose a provider, paste your token, **Construire
l'index** (≈ 1–2 minutes and a few cents with OpenAI `text-embedding-3-small` for ~3800
assignments; longer with a local model). Then switch the search mode next to the search box:

- **AI** ranks by meaning, so "working with animals outdoors" also finds *Alp-Pflege* or *Biotoppflege*.
- **Both** combines keyword matches with AI matches.

Works with any OpenAI-compatible embeddings API: OpenAI, Mistral, or a local
[Ollama](https://ollama.com) (no token needed). With a local model, the same Ollama box as in
Traduction shows whether the model is installed and downloads it in one click.

**Construire l'index dans la langue de l'interface** builds the index from the translation
into the interface language where there is one (and the original otherwise). With a model
trained mostly on English, such as `nomic-embed-text`, a query in that language then finds
assignments written in the others much better. Each language has its own index (stored as
`model@fr`, `model@de`…): switching the interface language loads that language's index and
never erases another one; the section lists the indexes already built. It also shows how
many assignments the next build will (re)compute: newly translated ones, new assignments, and
any left over from an interrupted build.
The token is kept only in the local server's memory, never written to disk. The chosen
provider URL and model are saved in `data/ai_settings.json`, so after a restart the matching
index (for example a local Ollama one) is loaded again without opening the settings. Tick
"Remember the token in this browser" to keep it in your browser's storage instead of
re-entering it after a restart. You can also set it as an environment variable:
`ZIVI_AI_API_KEY`, `ZIVI_AI_BASE_URL`, `ZIVI_AI_MODEL`.

Building is resumable and tolerant of slow local models: local endpoints get small batches
(8 texts) and a 10-minute timeout; a batch that times out is retried, then split in half, and
a single text that still fails is skipped and counted. **Arrêter** stops after the current
batch; **Construire l'index** then only does what is missing, and **Tout reconstruire**
starts over.

## How it works

```
JSON export ──► converter ──► SQLite store (data/zivi.sqlite)
                 │  normalise fields, detect language        ├─ assignments, task_areas, weeks
                 │  geocode addresses (cached)               ├─ locations (lat/lon, canton)
                 │                                           ├─ FTS5 full-text index
                 └─ keep cached locations + AI vectors       └─ embeddings (optional)
                                    │
                         search engine (in memory, ~4k records)
                                    │
                    FastAPI ──► web UI (Leaflet map, vendored, works offline except tiles)
```

- `src/zivi/converter.py` reads the export (several formats accepted), recognises fields by
  name and prints a report of unknown or missing fields.
- `src/zivi/geocode.py` places addresses: coordinates in the record → GeoNames postcode
  table (offline) → swisstopo search API → OpenStreetMap Nominatim (for abroad).
  Set `ZIVI_OFFLINE=1` to stay offline.
- `src/zivi/search.py` filters, ranks (bm25, cosine similarity, or both fused) and computes
  facet counts.
- `src/zivi/semantic.py` builds and queries the embedding index.
- `src/zivi/ollama.py` detects, starts and downloads Ollama models and runs the translation job.
- `src/zivi/messages.py`: messages for people are sent as codes the page translates.
- `src/zivi/static/i18n.js` holds the German and Italian interface texts (French is the
  source in `app.js` and `index.html`).

If assignments don't appear on the map, run `uv run zivi doctor <file.json>`; it shows how
the addresses in the file are structured and parsed.

## Development

```powershell
uv run pytest
```

Map tiles: swisstopo (grey or colour) and OpenStreetMap. Fonts and Leaflet are bundled in
`src/zivi/static/vendor` with their licences.
