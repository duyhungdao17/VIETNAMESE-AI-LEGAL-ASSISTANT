from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import CrawlRecord


class GatewaySyncState:
    """Mutable resume state; raw crawler records remain append-only JSONL."""

    def __init__(self, dataset_root: Path) -> None:
        dataset_root.mkdir(parents=True, exist_ok=True)
        self.dataset_root = dataset_root
        self.connection = sqlite3.connect(dataset_root / "sync-state.sqlite3")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS documents (source_id TEXT PRIMARY KEY, seen INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS pending (source_id TEXT PRIMARY KEY)"
        )
        self.connection.commit()
        self._reconcile_record_log()


    def _reconcile_record_log(self) -> None:
        record_log = self.dataset_root / "records.jsonl"
        if not record_log.exists():
            return
        for line in record_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = CrawlRecord.model_validate_json(line)
                self.connection.execute(
                    "INSERT INTO documents(source_id, seen, completed) VALUES (?, 1, 1) ON CONFLICT(source_id) DO UPDATE SET seen = 1, completed = 1",
                    (record.source_id,),
                )
                self.connection.execute("DELETE FROM pending WHERE source_id = ?", (record.source_id,))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def has_seen(self, source_id: str) -> bool:
        return self._exists("SELECT 1 FROM documents WHERE source_id = ? AND seen = 1", source_id)

    def is_completed(self, source_id: str) -> bool:
        return self._exists("SELECT 1 FROM documents WHERE source_id = ? AND completed = 1", source_id)

    def mark_seen(self, source_id: str) -> None:
        self.connection.execute(
            "INSERT INTO documents(source_id, seen) VALUES (?, 1) ON CONFLICT(source_id) DO UPDATE SET seen = 1",
            (source_id,),
        )
        self.connection.commit()

    def mark_completed(self, source_id: str) -> None:
        self.connection.execute(
            "INSERT INTO documents(source_id, seen, completed) VALUES (?, 1, 1) ON CONFLICT(source_id) DO UPDATE SET seen = 1, completed = 1",
            (source_id,),
        )
        self.connection.execute("DELETE FROM pending WHERE source_id = ?", (source_id,))
        self.connection.commit()

    def mark_pending(self, source_id: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO pending(source_id) VALUES (?)", (source_id,))
        self.connection.commit()

    def pending_ids(self) -> list[str]:
        return [row[0] for row in self.connection.execute("SELECT source_id FROM pending ORDER BY source_id")]

    def counts(self) -> tuple[int, int, int]:
        seen = self.connection.execute("SELECT COUNT(*) FROM documents WHERE seen = 1").fetchone()[0]
        completed = self.connection.execute("SELECT COUNT(*) FROM documents WHERE completed = 1").fetchone()[0]
        pending = self.connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
        return int(seen), int(completed), int(pending)

    def append_record(self, record: CrawlRecord) -> None:
        with (self.dataset_root / "records.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")

    def _exists(self, query: str, source_id: str) -> bool:
        return self.connection.execute(query, (source_id,)).fetchone() is not None