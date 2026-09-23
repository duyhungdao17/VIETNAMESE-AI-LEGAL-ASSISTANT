from __future__ import annotations

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from .ingestion.crawler import CrawlService, SourceAccessBlocked, SourceScopeViolation
from .ingestion.discovery import DiscoveryService
from .ingestion.manifest import load_manifest, write_manifest
from .ingestion.models import DatasetManifest, SeedRecord, SourceMode, SourceProfile
from .ingestion.pdf_extract import PdfTextExtractor
from .ingestion.storage import RawStore


class DatasetCompatibilityError(RuntimeError):
    pass


def _load_seed(path: Path) -> list[SeedRecord]:
    return [SeedRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_seed(path: Path, records: list[SeedRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text("\n".join(record.model_dump_json() for record in records) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_manifest(dataset_root: Path, dataset_version: str, source_base_url: str, seed_bytes: bytes, *, resume: bool) -> DatasetManifest:
    manifest_path = dataset_root / "manifest.json"
    seed_sha256 = hashlib.sha256(seed_bytes).hexdigest()
    if not manifest_path.exists():
        if dataset_root.exists() and any(dataset_root.iterdir()):
            raise DatasetCompatibilityError("dataset directory contains data but no manifest")
        return DatasetManifest(dataset_version=dataset_version, schema_version="1", config_version="1", source_identifier="vbpl", source_base_url=source_base_url, seed_sha256=seed_sha256)
    if not resume:
        raise DatasetCompatibilityError("dataset version already exists; use a new version or --resume")
    manifest = load_manifest(dataset_root)
    if (manifest.dataset_version, manifest.schema_version, manifest.config_version, manifest.source_identifier, manifest.source_base_url, manifest.seed_sha256) != (dataset_version, "1", "1", "vbpl", source_base_url, seed_sha256):
        raise DatasetCompatibilityError("existing manifest is incompatible with this source, schema, config, or seed")
    return manifest


def _discovery_profile(args: argparse.Namespace) -> SourceProfile:
    return SourceProfile(
        source_identifier="vbpl",
        mode=SourceMode(args.source_mode),
        listing_url=args.listing_url,
        api_list_url=args.api_list_url,
        api_detail_url_template=args.api_detail_url_template,
        document_url_template=args.document_url_template,
        request_delay_seconds=args.request_delay_seconds,
    )


def _discover(args: argparse.Namespace) -> int:
    profile = _discovery_profile(args)
    with httpx.Client(headers={"User-Agent": "legal-assistant-research/0.1"}, timeout=30.0) as client:
        discovery = DiscoveryService(client, profile.source_identifier, request_delay_seconds=profile.request_delay_seconds)
        if profile.mode is SourceMode.HTML:
            records = discovery.discover(profile.listing_url, discovered_at=datetime.now(UTC))
        else:
            try:
                records = discovery.discover_api(
                    profile.api_list_url,
                    document_url_template=profile.document_url_template,
                    detail_url_template=profile.api_detail_url_template,
                    discovered_at=datetime.now(UTC),
                    limit=args.limit,
                )
            except SourceAccessBlocked:
                raise
            except (httpx.HTTPError, ValueError):
                if profile.mode is not SourceMode.HYBRID or profile.listing_url is None:
                    raise
                records = discovery.discover(profile.listing_url, discovered_at=datetime.now(UTC))
    _write_seed(Path(args.seed_out), records[: args.limit])
    return 0


def _crawl(args: argparse.Namespace) -> int:
    seed_path = Path(args.seed)
    dataset_root = Path(args.data_root) / args.dataset_version
    manifest = prepare_manifest(dataset_root, args.dataset_version, args.source_base_url, seed_path.read_bytes(), resume=args.resume)
    store = RawStore(dataset_root)
    completed = set(manifest.checkpoint.completed_source_ids)
    mode = SourceMode(args.source_mode)
    with httpx.Client(headers={"User-Agent": "legal-assistant-research/0.1"}, timeout=30.0) as client:
        crawler = CrawlService(client=client, store=store, source_identifier="vbpl", request_delay_seconds=args.request_delay_seconds)
        for seed in _load_seed(seed_path):
            if seed.source_id in completed:
                continue
            try:
                if mode is SourceMode.HTML:
                    record = crawler.crawl(seed)
                else:
                    if mode is SourceMode.API and seed.api_detail_url is None:
                        raise ValueError(f"API mode requires api_detail_url for {seed.source_id}")
                    record = crawler.crawl_api(seed)
                manifest.records.append(record)
                manifest.checkpoint.completed_source_ids.append(seed.source_id)
                completed.add(seed.source_id)
            except (SourceAccessBlocked, SourceScopeViolation) as error:
                manifest.checkpoint.stopped_reason = str(error)
                manifest.retrieval_errors.append(str(error))
                write_manifest(dataset_root, manifest)
                return 2
            except Exception as error:
                manifest.checkpoint.failed_source_ids.append(seed.source_id)
                manifest.retrieval_errors.append(f"{seed.source_id}:{type(error).__name__}:{error}")
            write_manifest(dataset_root, manifest)
    return 0


def _extract_pdf(args: argparse.Namespace) -> int:
    dataset_root = Path(args.data_root) / args.dataset_version
    manifest = load_manifest(dataset_root)
    extractor = PdfTextExtractor(RawStore(dataset_root))
    for record in manifest.records:
        extracted_ids = {entry.source_artifact_id for entry in record.pdf_extractions}
        for artifact in record.artifacts:
            if artifact.kind.value == "original_pdf" and artifact.artifact_id not in extracted_ids:
                result = extractor.extract(artifact)
                record.pdf_extractions.append(result)
                if result.text_artifact is not None:
                    record.artifacts.append(result.text_artifact)
    write_manifest(dataset_root, manifest)
    return 0


def _source_mode_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-mode", choices=[mode.value for mode in SourceMode], default=SourceMode.HYBRID.value)
    parser.add_argument("--request-delay-seconds", type=float, default=1.5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legal-assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover")
    discover.add_argument("--listing-url")
    discover.add_argument("--api-list-url")
    discover.add_argument("--api-detail-url-template")
    discover.add_argument("--document-url-template")
    discover.add_argument("--seed-out", required=True)
    discover.add_argument("--limit", type=int, default=50)
    _source_mode_arguments(discover)
    discover.add_argument("--confirm-public-source-access", action="store_true", required=True)
    crawl = commands.add_parser("crawl")
    crawl.add_argument("--seed", required=True)
    crawl.add_argument("--dataset-version", required=True)
    crawl.add_argument("--source-base-url", required=True)
    crawl.add_argument("--data-root", default="data/raw")
    crawl.add_argument("--resume", action="store_true")
    _source_mode_arguments(crawl)
    crawl.add_argument("--confirm-public-source-access", action="store_true", required=True)
    extract = commands.add_parser("extract-pdf")
    extract.add_argument("--dataset-version", required=True)
    extract.add_argument("--data-root", default="data/raw")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return _discover(args)
    if args.command == "crawl":
        return _crawl(args)
    return _extract_pdf(args)


if __name__ == "__main__":
    raise SystemExit(main())