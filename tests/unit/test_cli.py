import pytest

from legal_assistant.cli import main


def test_cli_requires_public_source_confirmation_for_crawl() -> None:
    with pytest.raises(SystemExit) as result:
        main(["crawl", "--seed", "seed.jsonl", "--dataset-version", "pilot-v1", "--source-base-url", "https://example.test"])

    assert result.value.code == 2