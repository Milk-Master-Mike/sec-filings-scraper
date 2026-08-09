from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from . import CONTRACT_VERSION, __version__
from .client import SecClient, SecClientError
from .config import Settings
from .fixtures import FIXTURE_RETRIEVED_AT, FixtureSource
from .models import (
    Capabilities,
    CollectedRecords,
    CollectionRequest,
    CollectionResponse,
    PartialFailure,
    Resolution,
    ScrapeRun,
)
from .parser import (
    COMPANYFACTS_URL,
    SUBMISSIONS_URL,
    TICKERS_URL,
    company_record,
    parse_company_facts,
    parse_filings,
    resolve_company,
    utc_now,
)

FIXTURE_SCENARIOS = [
    "normal",
    "ambiguous",
    "changed_layout",
    "missing_fields",
    "malformed",
    "blocked",
    "stale",
    "corrected",
    "rate_limited",
    "partial_failure",
]


def capabilities(settings: Settings | None = None) -> Capabilities:
    configured = settings or Settings()
    return Capabilities(
        collector="sec-filings-scraper",
        version=__version__,
        contract_version=CONTRACT_VERSION,
        datasets=["identity", "filings", "shares_outstanding", "public_float"],
        fixture_scenarios=FIXTURE_SCENARIOS,
        source_ids=["sec-company-tickers", "sec-submissions", "sec-companyfacts"],
        limits={
            "max_concurrency": configured.max_concurrency,
            "requests_per_second": configured.requests_per_second,
        },
    )


class CollectorService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    @staticmethod
    def _run_id(
        request: CollectionRequest, started_at: datetime, requested_run_id: str | None = None
    ) -> str:
        if requested_run_id is not None:
            return requested_run_id
        if request.fixture_mode:
            stable = f"{request.query}:{request.fixture_scenario}:{started_at.isoformat()}"
            return str(uuid.uuid5(uuid.NAMESPACE_URL, stable))
        return str(uuid.uuid4())

    @staticmethod
    def _failure(dataset: str, source: str, exc: Exception) -> PartialFailure:
        if isinstance(exc, SecClientError):
            return PartialFailure(
                dataset=dataset,
                source=source,
                error_code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )
        return PartialFailure(
            dataset=dataset,
            source=source,
            error_code="parse_error",
            message=str(exc),
            retryable=False,
        )

    async def resolve(
        self, query: str, fixture_mode: bool = False, fixture_scenario: str = "normal"
    ) -> Resolution:
        if fixture_mode and fixture_scenario not in FIXTURE_SCENARIOS:
            raise ValueError(f"Unknown fixture scenario: {fixture_scenario}")
        source: Any = FixtureSource(fixture_scenario) if fixture_mode else SecClient(self.settings)
        return resolve_company(await source.get_json(TICKERS_URL), query)

    async def collect(
        self, request: CollectionRequest, requested_run_id: str | None = None
    ) -> CollectionResponse:
        if request.fixture_mode and request.fixture_scenario not in FIXTURE_SCENARIOS:
            raise ValueError(f"Unknown fixture scenario: {request.fixture_scenario}")

        started_at = (
            datetime.fromisoformat(FIXTURE_RETRIEVED_AT)
            if request.fixture_mode
            else utc_now()
        )
        source: Any = (
            FixtureSource(request.fixture_scenario)
            if request.fixture_mode
            else SecClient(self.settings)
        )
        failures: list[PartialFailure] = []
        warnings: list[str] = []

        if request.resolved_entity is not None:
            resolution = Resolution(status="resolved", entity=request.resolved_entity)
        else:
            try:
                ticker_payload = await source.get_json(TICKERS_URL)
                resolution = resolve_company(ticker_payload, request.query)
            except Exception as exc:  # noqa: BLE001 - source failure becomes partial evidence
                failures.append(self._failure("identity", "sec-company-tickers", exc))
                resolution = Resolution(status="not_found")

        if resolution.status != "resolved" or resolution.entity is None:
            status = "ambiguous" if resolution.status == "ambiguous" else (
                "failed" if failures else "not_found"
            )
            completed_at = started_at if request.fixture_mode else utc_now()
            return CollectionResponse(
                contract_version=CONTRACT_VERSION,
                collector_version=__version__,
                query=request.query,
                run=ScrapeRun(
                    run_id=self._run_id(request, started_at, requested_run_id),
                    started_at=started_at,
                    completed_at=completed_at,
                    fixture_mode=request.fixture_mode,
                    status=status,
                ),
                resolution=resolution,
                partial_failures=failures,
                warnings=[
                    "Select a specific ticker or CIK before collection."
                ] if resolution.status == "ambiguous" else [],
            )

        entity = resolution.entity
        records = CollectedRecords()
        if "identity" in request.requested_datasets:
            records.companies.append(company_record(entity, started_at))

        async def fetch_filings() -> tuple[str, Any]:
            url = SUBMISSIONS_URL.format(cik=entity.cik)
            try:
                return "filings", await source.get_json(url)
            except Exception as exc:  # noqa: BLE001 - source failure becomes partial evidence
                return "filings", exc

        async def fetch_facts() -> tuple[str, Any]:
            url = COMPANYFACTS_URL.format(cik=entity.cik)
            try:
                return "facts", await source.get_json(url)
            except Exception as exc:  # noqa: BLE001 - source failure becomes partial evidence
                return "facts", exc

        tasks = []
        if "filings" in request.requested_datasets:
            tasks.append(fetch_filings())
        fact_datasets = {
            item
            for item in request.requested_datasets
            if item in {"shares_outstanding", "public_float"}
        }
        if fact_datasets:
            tasks.append(fetch_facts())

        results = await asyncio.gather(*tasks)
        for result in results:
            kind, payload = result
            if isinstance(payload, Exception):
                affected = ["filings"] if kind == "filings" else sorted(fact_datasets)
                source_id = "sec-submissions" if kind == "filings" else "sec-companyfacts"
                for dataset in affected:
                    failures.append(self._failure(dataset, source_id, payload))
                continue

            try:
                if kind == "filings":
                    records.filings.extend(parse_filings(payload, entity, started_at))
                else:
                    evidence, missing = parse_company_facts(
                        payload,
                        entity,
                        started_at,
                        request.as_of or started_at,
                    )
                    records.evidence.extend(
                        item for item in evidence if item.dataset in fact_datasets
                    )
                    for dataset in missing:
                        if dataset in fact_datasets:
                            failures.append(
                                PartialFailure(
                                    dataset=dataset,
                                    source="sec-companyfacts",
                                    error_code="missing_fact",
                                    message=f"SEC company facts did not contain {dataset}.",
                                )
                            )
            except Exception as exc:  # noqa: BLE001 - parser failure becomes partial evidence
                affected = ["filings"] if kind == "filings" else sorted(fact_datasets)
                source_id = "sec-submissions" if kind == "filings" else "sec-companyfacts"
                for dataset in affected:
                    failures.append(self._failure(dataset, source_id, exc))

        if any(item.dataset == "public_float" for item in records.evidence):
            warnings.append(
                "Public float shown here is the issuer-reported, dated SEC value; "
                "it is not a current tradable free-float estimate."
            )
        completed_at = started_at if request.fixture_mode else utc_now()
        status = "partial" if failures else "complete"
        return CollectionResponse(
            contract_version=CONTRACT_VERSION,
            collector_version=__version__,
            query=request.query,
            run=ScrapeRun(
                run_id=self._run_id(request, started_at, requested_run_id),
                started_at=started_at,
                completed_at=completed_at,
                fixture_mode=request.fixture_mode,
                status=status,
            ),
            resolution=resolution,
            records=records,
            partial_failures=failures,
            warnings=warnings,
        )
