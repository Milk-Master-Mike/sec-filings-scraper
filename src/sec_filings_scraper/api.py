from fastapi import FastAPI, HTTPException, Query

from . import __version__
from .client import SecClientError
from .config import Settings
from .models import (
    Capabilities,
    CollectionRequest,
    CollectionResponse,
    ContractCollectorRequest,
    ResolveResponse,
    WorkbenchCandidate,
)
from .service import CollectorService, capabilities

settings = Settings()
service = CollectorService(settings)

app = FastAPI(
    title="SEC Filings Scraper",
    version=__version__,
    description="Normalized SEC research evidence with fair-access controls.",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/capabilities", response_model=Capabilities)
async def get_capabilities() -> Capabilities:
    return capabilities(settings)


@app.get("/v1/resolve", response_model=ResolveResponse)
async def resolve(
    q: str | None = Query(default=None, min_length=1, max_length=200),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    fixture_mode: bool = False,
    fixture_scenario: str = "normal",
) -> ResolveResponse:
    if q is not None and query is not None:
        raise HTTPException(status_code=422, detail="Provide q or query, not both.")
    term = q if q is not None else query
    if term is None:
        raise HTTPException(status_code=422, detail="A non-empty q query parameter is required.")
    try:
        resolution = await service.resolve(term, fixture_mode, fixture_scenario)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SecClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    entities = (
        [resolution.entity]
        if resolution.status == "resolved" and resolution.entity is not None
        else resolution.candidates
    )
    candidates = [
        WorkbenchCandidate(
            company_id=item.company_id or f"sec:{item.cik}:{item.ticker}",
            name=item.name,
            ticker=item.ticker,
            cik=item.cik,
            exchange=item.exchange,
        )
        for item in entities
    ]
    return ResolveResponse(
        query=term,
        status=resolution.status,
        requires_selection=resolution.status == "ambiguous",
        candidates=candidates,
    )


@app.post("/v1/collect", response_model=CollectionResponse)
async def collect(
    request: ContractCollectorRequest | CollectionRequest,
) -> CollectionResponse:
    try:
        if isinstance(request, ContractCollectorRequest):
            return await service.collect(
                request.to_legacy(), requested_run_id=str(request.request_id)
            )
        return await service.collect(request)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
