"""Command line: ``zivi import FILE``, ``zivi serve``, ``zivi search TEXT``, ``zivi embed``."""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

from . import config


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="zivi", description=__doc__)
    p.add_argument("--db", default=str(config.DB_PATH), help="SQLite store path")
    sub = p.add_subparsers(dest="cmd", required=True)

    imp = sub.add_parser("import", help="convert a ZiviConnect JSON export into the store")
    imp.add_argument("file")

    srv = sub.add_parser("serve", help="start the web UI")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8765)
    srv.add_argument("--no-browser", action="store_true")

    s = sub.add_parser("search", help="quick search from the terminal")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--near", help="place name or postcode")
    s.add_argument("--radius", type=float, default=20)
    s.add_argument("--housing", action="store_true")
    s.add_argument("--meals", action="store_true")
    s.add_argument("--limit", type=int, default=20)

    doc = sub.add_parser("doctor", help="show the structure of a JSON export (for debugging)")
    doc.add_argument("file")

    e = sub.add_parser("embed", help="build the AI (embedding) index; needs ZIVI_AI_API_KEY")
    e.add_argument("--model", default=config.AI_MODEL)
    e.add_argument("--base-url", default=config.AI_BASE_URL)

    args = p.parse_args(argv)
    db = Path(args.db)

    if args.cmd == "import":
        from .converter import convert
        report = convert(args.file, db)
        print(json.dumps(report, indent=2, ensure_ascii=False))

    elif args.cmd == "serve":
        import uvicorn

        from .api import create_app
        url = f"http://{args.host}:{args.port}"
        print(f"Better Zivi running at {url}  (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        uvicorn.run(create_app(db), host=args.host, port=args.port, log_level="warning")

    elif args.cmd == "search":
        import sqlite3

        from .geocode import Geocoder
        from .search import Engine
        eng = Engine(db)
        q: dict = {"text": args.text, "limit": args.limit}
        if args.housing:
            q["housing"] = 1
        if args.meals:
            q["meals"] = 1
        if args.near:
            hits = Geocoder(sqlite3.connect(":memory:")).search_place(args.near)
            if not hits:
                raise SystemExit(f"Place not found: {args.near}")
            q["geo"] = {"centers": [{**hits[0], "radius_km": args.radius}]}
            print(f"Near {hits[0]['label']} ({args.radius:g} km)")
        res = eng.search(q)
        print(f"{res['total']} results")
        for it in res["items"]:
            dist = f" {it['distance_km']} km" if it["distance_km"] is not None else ""
            print(f"  {it['number']}  {it['title']} — {it['employer']} ({it['place']}){dist}")
        for w in res["warnings"]:
            print("!", w)

    elif args.cmd == "doctor":
        from .converter import extract_addresses, iter_records, normalise
        with open(args.file, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        recs = list(iter_records(data))
        print(f"{len(recs)} records")
        first = recs[0]
        print("record keys:", sorted(first))
        det = first.get("detail") or {}
        print("detail keys:", sorted(det))
        sample = next((r for r in recs if (r.get("detail") or {}).get("adresseList")), None)
        if sample:
            print("adresseList example:", json.dumps(sample["detail"]["adresseList"][:2],
                                                     ensure_ascii=False)[:800])
            print("parsed as:", extract_addresses(sample["detail"]))
        else:
            print("no record has an adresseList; eibAdresse example:",
                  json.dumps(det.get("eibAdresse"), ensure_ascii=False)[:400])
        row = normalise(first)["row"]
        print("normalised example:", {k: v for k, v in row.items()
                                      if k not in ("raw", "tasks", "employer_description")})

    elif args.cmd == "embed":
        from .search import Engine
        eng = Engine(db)
        eng.ai.model, eng.ai.base_url = args.model, args.base_url
        if not eng.ai.configured:
            raise SystemExit("Set ZIVI_AI_API_KEY (or OPENAI_API_KEY) first.")
        eng.semantic.build(str(db), eng.ai_texts(), eng.ai)
        prog = eng.semantic.progress
        if prog["error"]:
            raise SystemExit(f"Failed: {prog['error']}")
        print(f"Embedded {prog['done']} new/changed texts; index has {len(eng.semantic.ids)}.")


if __name__ == "__main__":
    main()
