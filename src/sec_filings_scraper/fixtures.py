from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .client import SecClientError

FIXTURE_RETRIEVED_AT = "2026-01-15T12:00:00Z"
FIXTURE_DIR = Path(__file__).with_name("fixture_data")


class FixtureSource:
    """Deterministic, offline SEC response source used by tests and demos."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def _load(self, name: str) -> dict[str, Any]:
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    async def get_json(self, url: str) -> dict[str, Any]:
        if url.endswith("company_tickers.json"):
            data = self._load("company_tickers.json")
            if self.scenario == "changed_layout":
                data["metadata"] = {"ignored_future_field": True}
            return data

        if "/submissions/" in url:
            if self.scenario == "blocked":
                raise SecClientError("http_403", "Fixture SEC access was blocked")
            if self.scenario == "rate_limited":
                raise SecClientError("http_429", "Fixture SEC rate limit", True)
            data = self._load("submissions_CIK0001234567.json")
            if self.scenario == "malformed":
                data["filings"]["recent"]["form"] = "not-a-list"
            return data

        if "/companyfacts/" in url:
            if self.scenario in {"partial_failure", "rate_limited"}:
                raise SecClientError("http_429", "Fixture SEC rate limit", True)
            data = self._load("companyfacts_CIK0001234567.json")
            if self.scenario == "missing_fields":
                del data["facts"]["dei"]["EntityPublicFloat"]
            elif self.scenario == "malformed":
                data["facts"] = []
            elif self.scenario == "stale":
                for concept in data["facts"]["dei"].values():
                    for units in concept["units"].values():
                        for fact in units:
                            fact["end"] = "2019-12-31"
                            fact["filed"] = "2020-02-01"
            elif self.scenario == "corrected":
                facts = data["facts"]["dei"]["EntityPublicFloat"]["units"]["USD"]
                corrected = copy.deepcopy(facts[-1])
                corrected.update({"val": 7654321, "filed": "2025-01-10", "form": "10-K/A"})
                facts.append(corrected)
            return data

        raise SecClientError("fixture_not_found", f"No sanitized fixture for {url}")

