from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

import uvicorn

from .models import CollectionRequest
from .service import FIXTURE_SCENARIOS, CollectorService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sec-filings-scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="collect normalized SEC evidence")
    collect.add_argument("query", help="ticker or company name")
    collect.add_argument(
        "--dataset",
        action="append",
        choices=["identity", "filings", "shares_outstanding", "public_float"],
        dest="datasets",
    )
    collect.add_argument("--fixture", action="store_true", help="disable network and use fixtures")
    collect.add_argument("--scenario", choices=FIXTURE_SCENARIOS, default="normal")

    serve = subparsers.add_parser("serve", help="run the HTTP collector")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8080, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        uvicorn.run("sec_filings_scraper.api:app", host=args.host, port=args.port)
        return 0

    request_data = {
        "query": args.query,
        "fixture_mode": args.fixture,
        "fixture_scenario": args.scenario,
    }
    if args.datasets:
        request_data["requested_datasets"] = args.datasets
    response = asyncio.run(CollectorService().collect(CollectionRequest(**request_data)))
    print(json.dumps(response.model_dump(mode="json"), indent=2))
    return 0 if response.run.status in {"complete", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

