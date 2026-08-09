from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from . import PARSER_VERSION
from .models import (
    CompanyRecord,
    FilingEvent,
    Provenance,
    Resolution,
    ResolutionCandidate,
    ResolvedEntity,
    SourceEvidence,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def _cik(value: Any) -> str:
    return str(int(value)).zfill(10)


def resolve_company(payload: dict[str, Any], query: str) -> Resolution:
    normalized = query.casefold().strip()
    entries: list[ResolvedEntity] = []
    for key, item in payload.items():
        if key == "metadata" or not isinstance(item, dict):
            continue
        try:
            cik = _cik(item["cik_str"])
            ticker = str(item["ticker"])
            entries.append(
                ResolvedEntity(
                    company_id=f"sec:{cik}:{ticker.upper()}",
                    cik=cik,
                    ticker=ticker,
                    name=str(item["title"]),
                    exchange=item.get("exchange"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    ticker_matches = [item for item in entries if item.ticker.casefold() == normalized]
    if len(ticker_matches) == 1:
        return Resolution(status="resolved", entity=ticker_matches[0])

    cik_query = normalized.removeprefix("cik").lstrip("0")
    if cik_query.isdigit():
        cik_matches = [item for item in entries if item.cik.lstrip("0") == cik_query]
        if len(cik_matches) == 1:
            return Resolution(status="resolved", entity=cik_matches[0])
        if cik_matches:
            return Resolution(
                status="ambiguous",
                candidates=[
                    ResolutionCandidate(**item.model_dump(), match_reason="SEC CIK")
                    for item in sorted(cik_matches, key=lambda candidate: candidate.ticker)
                ],
            )

    exact_names = [item for item in entries if item.name.casefold() == normalized]
    candidates = exact_names or [item for item in entries if normalized in item.name.casefold()]
    if len(candidates) == 1:
        return Resolution(status="resolved", entity=candidates[0])
    if candidates:
        return Resolution(
            status="ambiguous",
            candidates=[
                ResolutionCandidate(
                    **item.model_dump(),
                    match_reason="exact company name" if exact_names else "partial company name",
                )
                for item in sorted(candidates, key=lambda candidate: candidate.ticker)
            ],
        )
    return Resolution(status="not_found")


def provenance(
    url: str,
    retrieved_at: datetime,
    effective_date: str,
    units: str,
    confidence: float = 1.0,
    warnings: list[str] | None = None,
) -> Provenance:
    return Provenance(
        source_url=url,
        retrieved_at=retrieved_at,
        effective_date=effective_date,
        units=units,
        parser_version=PARSER_VERSION,
        confidence=confidence,
        warnings=warnings or [],
    )


def company_record(entity: ResolvedEntity, retrieved_at: datetime) -> CompanyRecord:
    return CompanyRecord(
        company_id=entity.company_id or f"sec:{entity.cik}:{entity.ticker}",
        cik=entity.cik,
        ticker=entity.ticker,
        name=entity.name,
        exchange=entity.exchange,
        provenance=provenance(
            TICKERS_URL,
            retrieved_at,
            retrieved_at.date().isoformat(),
            "not_applicable",
        ),
    )


def _filing_url(cik: str, accession: str, primary_document: str | None) -> str:
    accession_compact = accession.replace("-", "")
    suffix = primary_document or f"{accession}-index.html"
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_compact}/{suffix}"


def parse_filings(
    payload: dict[str, Any], entity: ResolvedEntity, retrieved_at: datetime
) -> list[FilingEvent]:
    try:
        recent = payload["filings"]["recent"]
        accession_numbers = recent["accessionNumber"]
        forms = recent["form"]
        filing_dates = recent["filingDate"]
        if not all(isinstance(value, list) for value in (accession_numbers, forms, filing_dates)):
            raise TypeError
    except (KeyError, TypeError):
        raise ValueError("SEC submissions payload is missing recent filing arrays") from None

    report_dates = recent.get("reportDate", [])
    primary_documents = recent.get("primaryDocument", [])
    events: list[FilingEvent] = []
    for index, accession in enumerate(accession_numbers):
        if index >= len(forms) or index >= len(filing_dates):
            continue
        report_date = report_dates[index] if index < len(report_dates) else None
        primary_document = primary_documents[index] if index < len(primary_documents) else None
        url = _filing_url(entity.cik, accession, primary_document)
        events.append(
            FilingEvent(
                accession_number=accession,
                form=forms[index],
                filing_date=filing_dates[index],
                report_date=report_date or None,
                primary_document=primary_document or None,
                source_url=url,
                provenance=provenance(
                    url,
                    retrieved_at,
                    report_date or filing_dates[index],
                    "not_applicable",
                ),
            )
        )
    return events


def _latest_fact(
    payload: dict[str, Any],
    concept: str,
    preferred_units: tuple[str, ...],
    as_of: datetime,
) -> tuple[dict[str, Any], str] | None:
    try:
        units = payload["facts"]["dei"][concept]["units"]
    except (KeyError, TypeError):
        return None
    if not isinstance(units, dict):
        return None
    for unit in preferred_units:
        facts = units.get(unit)
        if isinstance(facts, list) and facts:
            valid = []
            for fact in facts:
                if not isinstance(fact, dict) or "val" not in fact:
                    continue
                filed = fact.get("filed")
                if not isinstance(filed, str):
                    continue
                try:
                    if datetime.fromisoformat(filed).date() > as_of.date():
                        continue
                except ValueError:
                    continue
                valid.append(fact)
            if valid:
                return max(valid, key=lambda fact: (fact.get("filed", ""), fact.get("end", ""))), unit
    return None


def _evidence_id(entity: ResolvedEntity, field: str, fact: dict[str, Any]) -> str:
    raw = f"{entity.cik}:{field}:{fact.get('end')}:{fact.get('filed')}:{fact.get('val')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def parse_company_facts(
    payload: dict[str, Any],
    entity: ResolvedEntity,
    retrieved_at: datetime,
    as_of: datetime,
) -> tuple[list[SourceEvidence], list[str]]:
    if not isinstance(payload.get("facts"), dict):
        raise TypeError("SEC companyfacts payload has an invalid facts object")
    url = COMPANYFACTS_URL.format(cik=entity.cik)
    evidence: list[SourceEvidence] = []
    missing: list[str] = []
    definitions = (
        (
            "shares_outstanding",
            "EntityCommonStockSharesOutstanding",
            ("shares",),
            [],
        ),
        (
            "public_float",
            "EntityPublicFloat",
            ("USD",),
            [
                (
                    "Issuer-reported public float is a dated SEC cover-page value, usually "
                    "based on non-affiliate market value; it is not current tradable free float."
                )
            ],
        ),
    )
    for field, concept, preferred_units, base_warnings in definitions:
        found = _latest_fact(payload, concept, preferred_units, as_of)
        if found is None:
            missing.append(field)
            continue
        fact, unit = found
        warnings = list(base_warnings)
        effective = fact.get("end") or fact["filed"]
        confidence = 1.0
        if effective:
            try:
                age_days = (as_of.date() - datetime.fromisoformat(effective).date()).days
                if age_days > 548:
                    warnings.append(f"Fact is stale: effective date is {age_days} days before as-of date.")
                    confidence = 0.65
            except ValueError:
                warnings.append("Fact effective date could not be parsed.")
                confidence = 0.75
        if str(fact.get("form", "")).endswith("/A"):
            warnings.append("Latest value was reported in an amended filing.")
        evidence.append(
            SourceEvidence(
                evidence_id=_evidence_id(entity, field, fact),
                dataset=field,  # type: ignore[arg-type]
                field=field,
                value=fact["val"],
                provenance=provenance(
                    url, retrieved_at, effective, unit, confidence, warnings
                ),
            )
        )
    return evidence, missing


def utc_now() -> datetime:
    return datetime.now(UTC)
