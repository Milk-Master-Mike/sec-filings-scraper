import json
from datetime import UTC, datetime
from pathlib import Path

from sec_filings_scraper.models import ResolvedEntity
from sec_filings_scraper.parser import parse_company_facts, resolve_company

FIXTURES = (
    Path(__file__).parents[1]
    / "src"
    / "sec_filings_scraper"
    / "fixture_data"
)


def test_changed_layout_unknown_entries_are_ignored() -> None:
    payload = json.loads((FIXTURES / "company_tickers.json").read_text(encoding="utf-8"))
    payload["metadata"] = {"future": "field"}
    resolution = resolve_company(payload, "EXMPL")
    assert resolution.status == "resolved"
    assert resolution.entity is not None
    assert resolution.entity.name == "Example Technology Corporation"


def test_unknown_company_is_not_found() -> None:
    payload = json.loads((FIXTURES / "company_tickers.json").read_text(encoding="utf-8"))
    assert resolve_company(payload, "Nobody Incorporated").status == "not_found"


def test_historical_run_excludes_facts_filed_after_as_of() -> None:
    payload = json.loads(
        (FIXTURES / "companyfacts_CIK0001234567.json").read_text(encoding="utf-8")
    )
    facts = payload["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"][
        "shares"
    ]
    facts.append(
        {
            "end": "2026-06-30",
            "filed": "2026-08-01",
            "form": "10-Q",
            "val": 999999999,
        }
    )
    entity = ResolvedEntity(
        cik="0001234567", ticker="EXMPL", name="Example Technology Corporation"
    )
    evidence, _ = parse_company_facts(
        payload,
        entity,
        datetime(2026, 8, 2, tzinfo=UTC),
        datetime(2026, 1, 15, tzinfo=UTC),
    )
    shares = next(item for item in evidence if item.field == "shares_outstanding")
    assert shares.value != 999999999
