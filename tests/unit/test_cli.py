import pytest

from legal_assistant.cli import build_parser, main


def test_cli_requires_public_source_confirmation_for_crawl() -> None:
    with pytest.raises(SystemExit) as result:
        main(["crawl", "--seed", "seed.jsonl", "--dataset-version", "pilot-v1", "--source-base-url", "https://example.test"])

    assert result.value.code == 2


def test_cli_accepts_hybrid_source_profile_arguments() -> None:
    args = build_parser().parse_args(
        [
            "discover",
            "--seed-out", "seed.jsonl",
            "--source-mode", "hybrid",
            "--api-list-url", "https://api.example.test/documents",
            "--api-detail-url-template", "https://api.example.test/doc/{doc_id}",
            "--document-url-template", "https://www.example.test/doc/{doc_id}",
            "--confirm-public-source-access",
        ]
    )

    assert args.source_mode == "hybrid"
    assert args.request_delay_seconds == 1.5