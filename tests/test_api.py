import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from sec_filings_scraper.api import app
from sec_filings_scraper.models import ContractCollectorRequest, ContractSourceEvidence

client = TestClient(app)
CONTRACT_FIXTURE = Path(__file__).parent / "fixtures" / "collector_request_contract_0_1_0.json"


def fixture_request(query: str = "EXMPL", scenario: str = "normal") -> dict:
    return {"query": query, "fixture_mode": True, "fixture_scenario": scenario}


def test_health_and_capabilities() -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    response = client.get("/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "0.1.0"
    assert "public_float" in body["datasets"]
    assert body["limits"]["requests_per_second"] <= 10


def test_resolve_supports_workbench_candidate_shape_and_explicit_ambiguity() -> None:
    resolved = client.get(
        "/v1/resolve",
        params={"q": "EXMPL", "fixture_mode": True},
    )
    assert resolved.status_code == 200
    assert resolved.json() == {
        "query": "EXMPL",
        "status": "resolved",
        "requires_selection": False,
        "candidates": [
            {
                "company_id": "sec:0001234567:EXMPL",
                "name": "Example Technology Corporation",
                "ticker": "EXMPL",
                "cik": "0001234567",
                "exchange": "Example Exchange",
            }
        ],
    }

    ambiguous = client.get(
        "/v1/resolve",
        params={
            "query": "Alpha Example Holdings",
            "fixture_mode": True,
            "fixture_scenario": "ambiguous",
        },
    )
    assert ambiguous.status_code == 200
    assert ambiguous.json()["status"] == "ambiguous"
    assert ambiguous.json()["requires_selection"] is True
    assert {item["ticker"] for item in ambiguous.json()["candidates"]} == {"ALPH", "ALPB"}


def test_workbench_candidate_can_be_submitted_through_legacy_request() -> None:
    candidate = client.get(
        "/v1/resolve", params={"q": "EXMPL", "fixture_mode": True}
    ).json()["candidates"][0]
    response = client.post(
        "/v1/collect",
        json={
            "query": "EXMPL",
            "resolved_entity": candidate,
            "requested_datasets": ["filings", "shares", "public_float"],
            "fixture_mode": True,
            "source_settings": {"mode": "fixture"},
        },
    )
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "complete"
    assert {item["field"] for item in response.json()["records"]["evidence"]} == {
        "shares_outstanding",
        "public_float",
    }


def test_exact_contract_request_fixture_is_strictly_adapted() -> None:
    payload = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    validated = ContractCollectorRequest.model_validate(payload)
    assert validated.query.value == "EXMPL"
    response = client.post("/v1/collect", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["run_id"] == payload["request_id"]
    assert body["run"]["fixture_mode"] is True
    assert body["run"]["status"] == "complete"

    invalid = {**payload, "unexpected": True}
    assert client.post("/v1/collect", json=invalid).status_code == 422
    secret = {**payload, "source_settings": {"provider": {"api_key": "nope"}}}
    assert client.post("/v1/collect", json=secret).status_code == 422


def test_normal_fixture_is_deterministic_and_provenanced() -> None:
    first = client.post("/v1/collect", json=fixture_request())
    second = client.post("/v1/collect", json=fixture_request())
    assert first.status_code == 200
    assert first.json() == second.json()

    body = first.json()
    assert body["run"]["status"] == "complete"
    assert body["resolution"]["entity"]["cik"] == "0001234567"
    assert len(body["records"]["filings"]) == 2
    evidence = {item["field"]: item for item in body["records"]["evidence"]}
    assert evidence["shares_outstanding"]["value"] == 1000000
    assert evidence["shares_outstanding"]["provenance"]["units"] == "shares"
    assert evidence["public_float"]["value"] == 7500000
    assert evidence["public_float"]["provenance"]["units"] == "USD"
    assert "not current tradable free float" in " ".join(
        evidence["public_float"]["provenance"]["warnings"]
    )
    for item in body["records"]["evidence"]:
        provenance = item["provenance"]
        assert provenance["source_url"].startswith("https://data.sec.gov/")
        assert provenance["retrieved_at"]
        assert provenance["effective_date"]
        assert provenance["parser_version"]
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", provenance["parser_version"])
        ContractSourceEvidence.model_validate(
            {
                "evidence_id": item["evidence_id"],
                "source_name": "SEC companyfacts",
                **provenance,
            }
        )
    for record in body["records"]["companies"] + body["records"]["filings"]:
        assert record["provenance"]["effective_date"]
        assert record["provenance"]["units"]


def test_ambiguous_company_name_requires_selection() -> None:
    response = client.post(
        "/v1/collect", json=fixture_request("Alpha Example Holdings", "ambiguous")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "ambiguous"
    assert body["resolution"]["entity"] is None
    assert [item["ticker"] for item in body["resolution"]["candidates"]] == [
        "ALPB",
        "ALPH",
    ]
    assert body["records"]["evidence"] == []


def test_missing_fact_is_partial_without_losing_other_records() -> None:
    response = client.post(
        "/v1/collect", json=fixture_request(scenario="missing_fields")
    )
    body = response.json()
    assert body["run"]["status"] == "partial"
    assert len(body["records"]["filings"]) == 2
    assert [item["field"] for item in body["records"]["evidence"]] == [
        "shares_outstanding"
    ]
    assert body["partial_failures"][0]["dataset"] == "public_float"
    assert body["partial_failures"][0]["error_code"] == "missing_fact"


def test_rate_limit_reports_each_source_failure() -> None:
    response = client.post(
        "/v1/collect", json=fixture_request(scenario="rate_limited")
    )
    body = response.json()
    assert body["run"]["status"] == "partial"
    assert body["records"]["companies"]
    assert {failure["source"] for failure in body["partial_failures"]} == {
        "sec-submissions",
        "sec-companyfacts",
    }
    assert all(failure["retryable"] for failure in body["partial_failures"])


def test_malformed_sources_become_partial_failures() -> None:
    response = client.post("/v1/collect", json=fixture_request(scenario="malformed"))
    body = response.json()
    assert body["run"]["status"] == "partial"
    assert {failure["error_code"] for failure in body["partial_failures"]} == {
        "parse_error"
    }


def test_stale_and_corrected_facts_are_explained() -> None:
    stale = client.post("/v1/collect", json=fixture_request(scenario="stale")).json()
    assert all(
        item["provenance"]["confidence"] < 1 for item in stale["records"]["evidence"]
    )
    assert all(
        any("stale" in warning.lower() for warning in item["provenance"]["warnings"])
        for item in stale["records"]["evidence"]
    )

    corrected = client.post(
        "/v1/collect", json=fixture_request(scenario="corrected")
    ).json()
    public_float = next(
        item for item in corrected["records"]["evidence"] if item["field"] == "public_float"
    )
    assert public_float["value"] == 7654321
    assert any("amended" in warning for warning in public_float["provenance"]["warnings"])


def test_unknown_scenario_is_validation_error() -> None:
    response = client.post(
        "/v1/collect", json=fixture_request(scenario="does-not-exist")
    )
    assert response.status_code == 422


def test_requested_datasets_are_respected() -> None:
    request = fixture_request()
    request["requested_datasets"] = ["identity", "filings"]
    body = client.post("/v1/collect", json=request).json()
    assert body["run"]["status"] == "complete"
    assert body["records"]["filings"]
    assert body["records"]["evidence"] == []
