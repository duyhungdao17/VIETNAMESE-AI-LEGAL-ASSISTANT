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

def test_cli_accepts_gateway_sync_pilot_arguments() -> None:
    args = build_parser().parse_args(
        [
            "sync-vbpl",
            "--api-base-url", "https://example.test/api",
            "--dataset-version", "vbpl-gateway-pilot-v1",
            "--max-pages", "100",
            "--confirm-public-source-access",
        ]
    )

    assert args.page_size == 10
    assert args.max_pages == 100
    assert args.all_pages is False

def test_gateway_manifest_uses_config_hash_and_requires_resume(tmp_path) -> None:
    from legal_assistant import cli

    prepare = getattr(cli, "prepare_gateway_manifest")
    root = tmp_path / "raw" / "vbpl-gateway-pilot-v1"
    first = prepare(root, "vbpl-gateway-pilot-v1", "https://example.test/api", page_size=10, resume=False)
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(first.model_dump_json(), encoding="utf-8")

    with pytest.raises(cli.DatasetCompatibilityError):
        prepare(root, "vbpl-gateway-pilot-v1", "https://example.test/api", page_size=10, resume=False)

    resumed = prepare(root, "vbpl-gateway-pilot-v1", "https://example.test/api", page_size=10, resume=True)
    assert resumed.seed_sha256 == first.seed_sha256