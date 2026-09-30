from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from .corpus.audit import audit_dataset
from .corpus.evaluation import GoldCase, evaluate_variants, export_annotation_candidates
from .corpus.pilot import build_pilot

from .ingestion.crawler import CrawlService, SourceAccessBlocked, SourceScopeViolation
from .ingestion.discovery import DiscoveryService
from .ingestion.gateway import GatewayProgress, VBPLGatewaySyncService
from .ingestion.gateway_state import GatewaySyncState
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




def prepare_gateway_manifest(
    dataset_root: Path,
    dataset_version: str,
    api_base_url: str,
    *,
    page_size: int,
    status_scope: str = "selected",
    resume: bool,
) -> DatasetManifest:
    """Create or validate a resumable manifest for the public VBPL gateway."""
    if status_scope not in {"selected", "all"}:
        raise DatasetCompatibilityError("status scope must be selected or all")
    source_base_url = api_base_url.rstrip("/")
    schema_version = "3" if status_scope == "all" else "2"
    configuration = {
        "gateway_route_version": "qtdc-public-v1",
        "page_size": page_size,
        "status_scope": status_scope,
        "selected_status_buckets": ["active", "partially_expired", "future"],
    }
    config_sha256 = hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_path = dataset_root / "manifest.json"
    expected = (dataset_version, schema_version, schema_version, "vbpl_gateway", source_base_url, config_sha256)
    if not manifest_path.exists():
        if dataset_root.exists() and any(dataset_root.iterdir()):
            raise DatasetCompatibilityError("dataset directory contains data but no manifest")
        return DatasetManifest(
            dataset_version=dataset_version,
            schema_version=schema_version,
            config_version=schema_version,
            source_identifier="vbpl_gateway",
            source_base_url=source_base_url,
            seed_sha256=config_sha256,
        )
    if not resume:
        raise DatasetCompatibilityError("dataset version already exists; use a new version or --resume")
    manifest = load_manifest(dataset_root)
    actual = (
        manifest.dataset_version,
        manifest.schema_version,
        manifest.config_version,
        manifest.source_identifier,
        manifest.source_base_url,
        manifest.seed_sha256,
    )
    if actual != expected:
        raise DatasetCompatibilityError("existing manifest is incompatible with this gateway configuration")
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




def _render_gateway_progress(progress: GatewayProgress) -> None:
    coverage = 0.0
    if progress.total_pages:
        coverage = progress.seen_count / max(progress.seen_count, progress.total_pages) * 100
    state = "complete" if progress.discovery_complete else "incomplete" if progress.discovery_incomplete else "running"
    print(
        f"\rpass {progress.pass_number}/{progress.max_discovery_passes} | page {progress.page_number}/{progress.total_pages} | seen {progress.seen_count} | completed {progress.completed_count} | pending {progress.pending_count} | {state}",
        end="",
        flush=True,
    )

def _sync_vbpl(args: argparse.Namespace) -> int:
    dataset_root = Path(args.data_root) / args.dataset_version
    manifest = prepare_gateway_manifest(
        dataset_root,
        args.dataset_version,
        args.api_base_url,
        page_size=args.page_size,
        status_scope=args.status_scope,
        resume=args.resume,
    )
    state = GatewaySyncState(dataset_root) if args.status_scope == "all" else None
    if state is not None:
        manifest.record_log_path = "records.jsonl"
    store = RawStore(dataset_root)
    try:
        with httpx.Client(headers={"User-Agent": "legal-assistant-research/0.1"}, timeout=30.0) as client:
            service = VBPLGatewaySyncService(
                client=client,
                store=store,
                api_base_url=args.api_base_url,
                request_delay_seconds=args.request_delay_seconds,
                checkpoint_writer=lambda current: write_manifest(dataset_root, current),
                state=state,
                status_scope=args.status_scope,
                progress_reporter=_render_gateway_progress,
            )
            try:
                service.sync(
                    manifest,
                    page_size=args.page_size,
                    max_pages=None if args.all_pages else args.max_pages,
                    max_discovery_passes=args.max_discovery_passes,
                )
            except (SourceAccessBlocked, SourceScopeViolation) as error:
                manifest.checkpoint.stopped_reason = str(error)
                manifest.retrieval_errors.append(str(error))
                return 2
    finally:
        if state is not None:
            state.close()
        write_manifest(dataset_root, manifest)
        print()
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
    sync = commands.add_parser("sync-vbpl")
    sync.add_argument("--api-base-url", required=True)
    sync.add_argument("--dataset-version", required=True)
    sync.add_argument("--data-root", default="data/raw")
    sync.add_argument("--page-size", type=int, default=10)
    sync.add_argument("--max-pages", type=int, default=100)
    sync.add_argument("--all-pages", action="store_true")
    sync.add_argument("--status-scope", choices=["selected", "all"], default="selected")
    sync.add_argument("--max-discovery-passes", type=int, default=1)
    sync.add_argument("--resume", action="store_true")
    sync.add_argument("--request-delay-seconds", type=float, default=1.5)
    sync.add_argument("--confirm-public-source-access", action="store_true", required=True)
    extract = commands.add_parser("extract-pdf")
    extract.add_argument("--dataset-version", required=True)
    extract.add_argument("--data-root", default="data/raw")
    audit = commands.add_parser('audit-corpus')
    audit.add_argument('--dataset-root', required=True)
    audit.add_argument('--report-out', required=True)
    pilot = commands.add_parser('build-pilot')
    pilot.add_argument('--dataset-root', required=True)
    pilot.add_argument('--output', required=True)
    pilot.add_argument('--limit', type=int, default=200)
    pilot.add_argument('--seed', default='pilot-v1')
    pilot.add_argument('--max-chars', type=int, default=1800)
    evaluate = commands.add_parser('evaluate-chunking')
    evaluate.add_argument('--pilot-dir', required=True)
    evaluate.add_argument('--gold-file', required=True)
    evaluate.add_argument('--split', default='test')
    evaluate.add_argument('--top-k', type=int, default=20)
    evaluate.add_argument('--report-out', required=True)
    candidates = commands.add_parser('prepare-gold-candidates')
    candidates.add_argument('--pilot-dir', required=True)
    candidates.add_argument('--output', required=True)
    candidates.add_argument('--per-document', type=int, default=3)
    return parser


def _write_new_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == 'audit-corpus':
        report = audit_dataset(Path(args.dataset_root))
        _write_new_json(Path(args.report_out), report)
        print(json.dumps(report['counts'], ensure_ascii=True))
        return 0
    if args.command == 'build-pilot':
        manifest = build_pilot(Path(args.dataset_root), Path(args.output), limit=args.limit, seed=args.seed, max_chars=args.max_chars)
        print(json.dumps({key: manifest[key] for key in ('document_count', 'chunk_count', 'discovery_chunk_count', 'parser_warning_count')}))
        return 0
    if args.command == 'evaluate-chunking':
        gold_path = Path(args.gold_file)
        gold = [GoldCase.model_validate_json(line) for line in gold_path.open(encoding='utf-8') if line.strip()]
        pilot_path = Path(args.pilot_dir)
        report = {
            'pilot_manifest_sha256': hashlib.sha256((pilot_path / 'pilot_manifest.json').read_bytes()).hexdigest(),
            'gold_sha256': hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            'split': args.split,
            'top_k': args.top_k,
            'retriever': 'bm25-local-v1',
            'variants': evaluate_variants(pilot_path, gold, top_k=args.top_k, split=args.split),
        }
        _write_new_json(Path(args.report_out), report)
        print(json.dumps({name: {'mrr': score['mrr'], 'recall_at_10': score.get('recall_at_10')} for name, score in report['variants'].items()}))
        return 0
    if args.command == 'prepare-gold-candidates':
        count = export_annotation_candidates(Path(args.pilot_dir), Path(args.output), per_document=args.per_document)
        print(json.dumps({'candidate_count': count}))
        return 0
    if args.command == "discover":
        return _discover(args)
    if args.command == "crawl":
        return _crawl(args)
    if args.command == "sync-vbpl":
        return _sync_vbpl(args)
    return _extract_pdf(args)


if __name__ == "__main__":
    raise SystemExit(main())
