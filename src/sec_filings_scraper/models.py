"""Temporary contract-compatible boundary.

These models intentionally mirror market-data-contracts 0.1.x concepts. They
live here until that separately versioned package is published.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    StringConstraints,
    field_validator,
)

Dataset = Literal["identity", "filings", "shares_outstanding", "public_float"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolvedEntity(StrictModel):
    company_id: str | None = None
    cik: str = Field(pattern=r"^\d{10}$")
    ticker: str
    name: str
    exchange: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


class CollectionRequest(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    resolved_entity: ResolvedEntity | None = None
    requested_datasets: list[Dataset] = Field(
        default_factory=lambda: [
            "identity",
            "filings",
            "shares_outstanding",
            "public_float",
        ]
    )
    as_of: datetime | None = None
    source_settings: dict[str, Any] = Field(default_factory=dict)
    fixture_mode: bool = False
    fixture_scenario: str = "normal"

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        return value.strip()

    @field_validator("requested_datasets", mode="before")
    @classmethod
    def normalize_dataset_aliases(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        aliases = {
            "company_identity": ("identity",),
            "shares": ("shares_outstanding",),
            "shares_and_float": ("shares_outstanding", "public_float"),
        }
        for item in value:
            for dataset in aliases.get(item, (item,)):
                if dataset not in normalized:
                    normalized.append(dataset)
        return normalized


class ResolutionCandidate(ResolvedEntity):
    match_reason: str


class Resolution(StrictModel):
    status: Literal["resolved", "ambiguous", "not_found"]
    entity: ResolvedEntity | None = None
    candidates: list[ResolutionCandidate] = Field(default_factory=list)


class Provenance(StrictModel):
    source_url: HttpUrl
    retrieved_at: AwareDatetime
    effective_date: date
    units: str = Field(min_length=1)
    parser_version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class SourceEvidence(StrictModel):
    evidence_id: str
    dataset: Dataset
    field: str
    value: Any
    provenance: Provenance


class FilingEvent(StrictModel):
    accession_number: str
    form: str
    filing_date: str
    report_date: str | None
    primary_document: str | None
    source_url: str
    provenance: Provenance


class CompanyRecord(StrictModel):
    company_id: str
    cik: str
    ticker: str
    name: str
    exchange: str | None
    provenance: Provenance


class PartialFailure(StrictModel):
    dataset: str
    source: str
    error_code: str
    message: str
    retryable: bool = False


class ScrapeRun(StrictModel):
    run_id: str
    started_at: datetime
    completed_at: datetime
    fixture_mode: bool
    status: Literal["complete", "partial", "ambiguous", "not_found", "failed"]


class CollectedRecords(StrictModel):
    companies: list[CompanyRecord] = Field(default_factory=list)
    filings: list[FilingEvent] = Field(default_factory=list)
    evidence: list[SourceEvidence] = Field(default_factory=list)


class CollectionResponse(StrictModel):
    contract_version: str
    collector_version: str
    query: str
    run: ScrapeRun
    resolution: Resolution
    records: CollectedRecords = Field(default_factory=CollectedRecords)
    partial_failures: list[PartialFailure] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Capabilities(StrictModel):
    collector: str
    version: str
    contract_version: str
    datasets: list[Dataset]
    fixture_scenarios: list[str]
    source_ids: list[str]
    limits: dict[str, int | float]


class WorkbenchCandidate(StrictModel):
    company_id: str
    name: str
    ticker: str
    cik: str = Field(pattern=r"^\d{10}$")
    exchange: str | None = None


class ResolveResponse(StrictModel):
    query: str
    status: Literal["resolved", "ambiguous", "not_found"]
    requires_selection: bool
    candidates: list[WorkbenchCandidate] = Field(default_factory=list)


# Exact checked-in market-data-contracts 0.1.0 request boundary. The collector
# keeps its legacy CLI/HTTP request alongside this adapter until the shared
# package can be consumed as a released dependency.
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractSearchQuery(StrictModel):
    kind: Literal["ticker", "company_name", "identifier"]
    value: NonEmptyString


class ContractSourceEvidence(StrictModel):
    evidence_id: NonEmptyString
    source_name: NonEmptyString
    source_url: HttpUrl
    retrieved_at: AwareDatetime
    effective_date: date
    units: NonEmptyString
    parser_version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    confidence: Decimal = Field(ge=0, le=1, decimal_places=6)
    warnings: tuple[NonEmptyString, ...] = ()
    source_record_id: NonEmptyString | None = None
    excerpt: str | None = Field(default=None, max_length=500)


class ContractCompany(StrictModel):
    kind: Literal["company"] = "company"
    company_id: NonEmptyString
    legal_name: NonEmptyString
    cik: str | None = Field(default=None, pattern=r"^\d{10}$")
    lei: str | None = Field(default=None, pattern=r"^[A-Z0-9]{20}$")
    aliases: tuple[NonEmptyString, ...] = ()
    jurisdiction: str | None = Field(default=None, min_length=2, max_length=80)
    evidence: ContractSourceEvidence


def _secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if any(marker in normalized for marker in ("secret", "password", "token", "apikey", "credential")):
                return str(key)
            found = _secret_key(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _secret_key(item)
            if found:
                return found
    return None


class ContractCollectorRequest(StrictModel):
    request_id: UUID
    contract_version: Literal["0.1.0"] = "0.1.0"
    query: ContractSearchQuery
    resolved_entity: ContractCompany | None = None
    requested_datasets: tuple[NonEmptyString, ...] = Field(min_length=1)
    as_of: AwareDatetime
    source_settings: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("source_settings")
    @classmethod
    def reject_secret_settings(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        key = _secret_key(value)
        if key:
            raise ValueError(f"source_settings cannot contain secret-like key: {key}")
        return value

    def to_legacy(self) -> CollectionRequest:
        aliases = {
            "company_identity": ("identity",),
            "identity": ("identity",),
            "filings": ("filings",),
            "shares_and_float": ("shares_outstanding", "public_float"),
            "shares": ("shares_outstanding",),
            "shares_outstanding": ("shares_outstanding",),
            "public_float": ("public_float",),
        }
        datasets: list[Dataset] = []
        for requested in self.requested_datasets:
            if requested not in aliases:
                raise ValueError(f"Unsupported SEC dataset in contract request: {requested}")
            for dataset in aliases[requested]:
                if dataset not in datasets:
                    datasets.append(dataset)  # type: ignore[arg-type]

        fixture_value = self.source_settings.get("fixture_mode", False)
        if not isinstance(fixture_value, bool):
            raise TypeError("source_settings.fixture_mode must be a boolean")
        fixture_scenario = self.source_settings.get("fixture_scenario", "normal")
        if not isinstance(fixture_scenario, str):
            raise TypeError("source_settings.fixture_scenario must be a string")

        resolved = None
        company = self.resolved_entity
        if company is not None and company.cik and self.query.kind == "ticker":
            resolved = ResolvedEntity(
                company_id=company.company_id,
                cik=company.cik,
                ticker=self.query.value,
                name=company.legal_name,
            )
        return CollectionRequest(
            query=self.query.value,
            resolved_entity=resolved,
            requested_datasets=datasets,
            as_of=self.as_of,
            source_settings=dict(self.source_settings),
            fixture_mode=fixture_value,
            fixture_scenario=fixture_scenario,
        )
