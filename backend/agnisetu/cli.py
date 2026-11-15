"""Command line interface for data operations and for running the server."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys

from . import firms, ingest
from .config import get_settings
from .logging_setup import configure_logging


def _cmd_ingest_archive(args: argparse.Namespace) -> int:
    summary = ingest.ingest_archive(
        countries=args.countries,
        from_year=args.from_year,
        to_year=args.to_year,
        datasets=args.datasets,
        workers=args.workers,
        force=args.force,
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed"] else 0


def _cmd_refresh_nrt(_: argparse.Namespace) -> int:
    report = ingest.refresh_nrt()
    print(json.dumps(report, indent=2))
    return 0 if all(f["status"] in {"ok", "empty", "not_published"} for f in report["feeds"].values()) else 1


def _cmd_backfill_api(args: argparse.Namespace) -> int:
    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None
    report = ingest.backfill_api(start=start, end=end, feeds=args.feeds)
    print(json.dumps(report, indent=2))
    return 0 if all(f["status"] == "ok" for f in report["feeds"].values()) else 1


def _cmd_build_mask(_: argparse.Namespace) -> int:
    print(json.dumps({"cells": ingest.build_static_mask()}, indent=2))
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    from .store import get_store

    print(json.dumps(get_store().coverage_summary(), indent=2))
    return 0


def _cmd_check(_: argparse.Namespace) -> int:
    print(json.dumps(ingest.data_status(), indent=2))
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "agnisetu.main:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_config=None,
        proxy_headers=settings.trust_forwarded_for,
        server_header=False,
        date_header=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agnisetu", description="AgniSetu data and server commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest-archive", help="Download yearly FIRMS country archive files (keyless)")
    p.add_argument("--countries", nargs="+", choices=sorted(firms.COUNTRIES), default=None)
    p.add_argument("--datasets", nargs="+", choices=sorted(firms.ARCHIVE_DATASETS), default=None)
    p.add_argument("--from-year", type=int, default=None)
    p.add_argument("--to-year", type=int, default=None)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--force", action="store_true", help="Download again even when a file is already ingested")
    p.set_defaults(func=_cmd_ingest_archive)

    p = sub.add_parser("refresh-nrt", help="Pull the rolling 7 day near real time feeds (keyless)")
    p.set_defaults(func=_cmd_refresh_nrt)

    p = sub.add_parser("backfill-api", help="Fill the current year through the FIRMS area API (needs MAP_KEY)")
    p.add_argument("--start", default=None, help="First day, YYYY-MM-DD. Default: day after the archive ends")
    p.add_argument("--end", default=None, help="Last day, YYYY-MM-DD. Default: today (UTC)")
    p.add_argument("--feeds", nargs="+", choices=sorted(firms.NRT_FEEDS), default=None)
    p.set_defaults(func=_cmd_backfill_api)

    p = sub.add_parser("build-mask", help="Rebuild the static heat source mask from the archive")
    p.set_defaults(func=_cmd_build_mask)

    p = sub.add_parser("status", help="Print store coverage")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("check", help="Report what the store holds and what it still lacks (no network)")
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("serve", help="Run the web application")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ingest.IngestError, ingest.IngestBusyError, ValueError) as exc:
        logging.getLogger("agnisetu.cli").error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
