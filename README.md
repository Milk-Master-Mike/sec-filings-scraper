# SEC Filings Scraper

> **Archived:** This service now lives in [`market-research-platform`](https://github.com/Milk-Master-Mike/market-research-platform/tree/main/services/sec-filings). Its full Git history was preserved in the monorepo.

A focused SEC collector for the Market Research Workbench. It resolves U.S.
issuer tickers and company names, lists recent filings, and extracts dated
shares-outstanding and issuer-reported public-float facts with complete
provenance. It is useful by itself through HTTP or the command line.

This project provides research evidence, not investment advice, expected-return
predictions, trade instructions, or a current tradable-float estimate.

## What it collects

- SEC ticker, company name, and zero-padded CIK identity
- recent submission metadata and direct EDGAR filing links
- `EntityCommonStockSharesOutstanding` facts in shares
- `EntityPublicFloat` facts in USD
- retrieval time, effective date, units, parser version, confidence, and warnings
- partial failures, so one unavailable SEC dataset does not discard other data

`EntityPublicFloat` is an issuer-reported, dated cover-page measure generally
based on the market value held by non-affiliates. It is **not** current tradable
free float. The API repeats this distinction on every returned public-float
record and at response level.

## Quick start: deterministic fixtures

Fixture mode performs no HTTP requests and always returns identical output:

```console
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[test]"
sec-filings-scraper collect EXMPL --fixture
pytest
```

On macOS or Linux, activate with `source .venv/bin/activate`.

Run the API:

```console
sec-filings-scraper serve --port 8080
curl http://127.0.0.1:8080/health
curl -X POST http://127.0.0.1:8080/v1/collect \
  -H "Content-Type: application/json" \
  -d '{"query":"EXMPL","fixture_mode":true}'
```

Or use Docker Compose:

```console
docker compose up --build
```

The standalone Compose file binds only to loopback. When the workbench consumes
this image, it should omit the port mapping and call it over the internal Docker
network.

## Live SEC mode

The SEC asks automated clients to identify themselves. Set a descriptive
application name and a monitored email address before making a live request:

```console
copy .env.example .env.local
# Edit SEC_USER_AGENT, then:
sec-filings-scraper collect AAPL
```

Live collection refuses to start without a plausible identified user agent.
The client caches JSON responses, defaults to two concurrent requests and eight
requests per second, follows bounded timeouts, and never logs the configured
user agent. See the SEC's [API and fair-access guidance](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## HTTP interface

- `GET /v1/resolve?q=...` returns workbench-compatible company candidates with
  explicit `resolved`, `ambiguous`, or `not_found` status. `query=` remains a
  compatibility alias.

- `GET /health` — process health; it does not probe SEC and is safe for Docker
- `GET /v1/capabilities` — datasets, sources, fixtures, and active rate limits
- `POST /v1/collect` — resolve and collect normalized evidence

Resolver candidates contain `company_id`, `name`, `ticker`, zero-padded `cik`,
and `exchange`. An ambiguous response returns every candidate and sets
`requires_selection` to `true`; it never silently chooses a security. Fixture
resolution can be exercised offline with `fixture_mode=true` and an optional
`fixture_scenario` query parameter.

Example request:

```json
{
  "query": "Example Technology Corporation",
  "requested_datasets": ["identity", "filings", "shares_outstanding", "public_float"],
  "as_of": "2026-01-15T12:00:00Z",
  "fixture_mode": true,
  "fixture_scenario": "normal",
  "source_settings": {}
}
```

If a company name matches more than one security, `resolution.status` is
`ambiguous` and `resolution.candidates` contains explicit choices. No filing or
fact collection occurs until the caller submits a ticker or a `resolved_entity`.

Fixture scenarios cover `normal`, `ambiguous`, `changed_layout`,
`missing_fields`, `malformed`, `blocked`, `stale`, `corrected`, `rate_limited`,
and `partial_failure` behavior. All fixture organizations and values are
sanitized examples.

### Published 0.1.0 request envelope

`POST /v1/collect` also accepts the exact strict `market-data-contracts` 0.1.0
`CollectorRequest` envelope. The structured query and shared dataset names are
translated at the HTTP boundary; the CLI and legacy string-query request remain
unchanged.

```json
{
  "request_id": "11111111-1111-4111-8111-111111111111",
  "contract_version": "0.1.0",
  "query": {"kind": "ticker", "value": "EXMPL"},
  "resolved_entity": null,
  "requested_datasets": ["company_identity", "filings", "shares_and_float"],
  "as_of": "2026-01-15T12:00:00Z",
  "source_settings": {"fixture_mode": true, "fixture_scenario": "normal"}
}
```

The adapter accepts `company_identity`, `filings`, `shares_and_float`,
`shares`, `shares_outstanding`, and `public_float`, rejecting unknown datasets.
It also applies the contract's recursive secret-like-key rejection to
`source_settings`. Timestamps must be timezone-aware and extra envelope fields
are rejected.

The response intentionally retains the collector's documented 0.1.x
extensions: top-level `collector_version`, string `query`, `resolution`, grouped
`records`, and `warnings`, plus `completed_at`, `fixture_mode`, and the richer
resolution statuses on `run`. These fields are stable collector extensions and
are not claimed to be a byte-for-byte `market-data-contracts.CollectorResponse`.
All returned provenance has a non-null effective date and units, an aware
retrieval timestamp, and a semantic-version parser identifier.

## Contracts and versioning

The current `0.1.x` release contains a strict Pydantic compatibility boundary
for `market-data-contracts >=0.1.0,<0.2.0`, declared in `pyproject.toml`. It will
switch to importing that package after its first public release. Extra request
fields are rejected, while unknown SEC response fields are ignored intentionally
to tolerate additive source changes.

Operational HTTP cache files live under `SEC_CACHE_DIR` (default `.cache/`) and
are separate from user research storage. This service does not store watchlists,
notes, runs, provider keys, or raw filing content.

## Source and security boundaries

Only the reviewed SEC JSON sources in `source-acceptance.yaml` are used. The
collector does not bypass access controls, scrape search-result HTML, execute
filing content, or redistribute raw filings. Do not commit `.env.local`; it is
ignored by Git and excluded from Docker builds. Please see `SECURITY.md` for
private vulnerability reporting.
