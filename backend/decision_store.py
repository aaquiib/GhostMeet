"""
Owns persistence for DecisionRecords: an abstract DecisionStore
interface (get_recent, create, update_status), and JSONLDecisionStore
— an interim implementation backed by the same decisions_log.jsonl
file Phase 3's default callback already wrote to, with an in-memory
index for fast lookups. Phase 5 re-implements this same interface
against SQLite; nothing outside this file should ever touch the JSONL
file directly.
"""

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

from decision_detector import DecisionRecord

logger = logging.getLogger("ghost.decision_store")

_DEFAULT_LOG_PATH = Path(__file__).parent / "decisions_log.jsonl"


class DecisionStore(ABC):
    @abstractmethod
    async def get_recent(self, meeting_id: str, limit: int) -> list[DecisionRecord]:
        """Most recent decisions for a meeting, newest first."""

    @abstractmethod
    async def create(self, decision: DecisionRecord) -> None:
        """Persist a newly detected decision."""

    # NEVER call update_status with status="approved"/"rejected" except
    # from slack_webhook.py's button handler, after verifying the Slack
    # request signature. That is the only legitimate trigger for either
    # status — this is a structural rule, not just policy.
    @abstractmethod
    async def update_status(
        self, decision_id: uuid.UUID, status: str, approved_by: str | None
    ) -> None:
        """Update a decision's status (e.g. "approved", "rejected",
        "denied_by_policy"). approved_by is logged for audit purposes;
        this interim store doesn't have a field for it on the record
        itself (Phase 5's real schema will)."""


class JSONLDecisionStore(DecisionStore):
    """decisions_log.jsonl is treated as an append-only event log: each
    write appends the full current record, and the LATEST line for a
    given id wins when rebuilding the in-memory index. This gives
    correct current-state semantics (including status updates) without
    needing in-place file edits."""

    def __init__(self, path: Path = _DEFAULT_LOG_PATH) -> None:
        self._path = path
        self._index: dict[uuid.UUID, DecisionRecord] = {}
        self._lock = asyncio.Lock()
        self._load_index()

    def _load_index(self) -> None:
        if not self._path.exists():
            return
        with self._path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = DecisionRecord.model_validate_json(line)
                except (ValidationError, json.JSONDecodeError):
                    logger.warning("skipping corrupt line in %s", self._path)
                    continue
                self._index[record.id] = record

    def _append(self, record: DecisionRecord) -> None:
        with self._path.open("a") as f:
            f.write(record.model_dump_json() + "\n")

    async def get_recent(self, meeting_id: str, limit: int) -> list[DecisionRecord]:
        async with self._lock:
            matching = [r for r in self._index.values() if r.meeting_id == meeting_id]
        matching.sort(key=lambda r: r.timestamp, reverse=True)
        return matching[:limit]

    async def create(self, decision: DecisionRecord) -> None:
        async with self._lock:
            self._index[decision.id] = decision
            self._append(decision)

    async def update_status(
        self, decision_id: uuid.UUID, status: str, approved_by: str | None
    ) -> None:
        async with self._lock:
            record = self._index.get(decision_id)
            if record is None:
                logger.warning("update_status: unknown decision_id %s", decision_id)
                return
            updated = record.model_copy(update={"status": status})
            self._index[decision_id] = updated
            self._append(updated)
        logger.info(
            "decision %s status -> %s (approved_by=%s)", decision_id, status, approved_by
        )


decision_store = JSONLDecisionStore()
