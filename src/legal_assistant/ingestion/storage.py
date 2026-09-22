from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .models import ArtifactKind, RawArtifact


class RawStore:
    def __init__(self, dataset_root: Path) -> None:
        self.dataset_root = dataset_root

    def persist_bytes(
        self,
        *,
        kind: ArtifactKind,
        source_id: str,
        source_url: str,
        fetched_at: datetime,
        payload: bytes,
        media_type: str,
        http_status: int,
        suffix: str,
        source_artifact_id: str | None = None,
    ) -> tuple[RawArtifact, bool]:
        digest = sha256(payload).hexdigest()
        relative_path = Path("artifacts") / f"{digest}{suffix}"
        target = self.dataset_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        deduplicated = target.exists()
        if not deduplicated:
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
        artifact = RawArtifact.from_bytes(
            artifact_id=f"sha256:{digest}",
            kind=kind,
            source_id=source_id,
            source_url=source_url,
            fetched_at=fetched_at,
            payload=payload,
            relative_path=relative_path.as_posix(),
            media_type=media_type,
            http_status=http_status,
            source_artifact_id=source_artifact_id,
        )
        return artifact, deduplicated