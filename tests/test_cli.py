import json

from sec_filings_scraper.cli import main


def test_fixture_cli(capsys) -> None:
    exit_code = main(["collect", "EXMPL", "--fixture"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["fixture_mode"] is True
    assert payload["records"]["filings"]

