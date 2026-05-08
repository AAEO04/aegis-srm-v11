"""
AEGIS-SRM — Digital Thread (Layer 7)
Versioned, traceable simulation records for certification readiness.
Each run is immutable once committed. Config freeze + signed outputs placeholder.
"""
from __future__ import annotations
import json
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ThreadEntry:
    run_id: str
    timestamp: float
    parameter_snapshot: dict
    vv_summary: dict
    outputs: dict
    software_version: str = "0.1.0"
    hash: str = field(default="", init=False)
    frozen: bool = False

    def __post_init__(self):
        self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = json.dumps({
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "parameters": self.parameter_snapshot,
            "outputs": self.outputs,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def freeze(self):
        """Lock this entry — no further modifications permitted."""
        self.frozen = True

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "software_version": self.software_version,
            "hash": self.hash,
            "frozen": self.frozen,
            "parameter_snapshot": self.parameter_snapshot,
            "vv_summary": self.vv_summary,
            "outputs": self.outputs,
        }


class DigitalThread:
    """
    Append-only log of simulation runs.
    Persists to JSONL (one record per line) for auditability.
    """

    def __init__(self, log_path: Optional[Path] = None):
        self._entries: list[ThreadEntry] = []
        self._log_path = log_path

    def commit(
        self,
        run_id: str,
        parameter_snapshot: dict,
        vv_summary: dict,
        outputs: dict,
        freeze: bool = False,
    ) -> ThreadEntry:
        entry = ThreadEntry(
            run_id=run_id,
            timestamp=time.time(),
            parameter_snapshot=parameter_snapshot,
            vv_summary=vv_summary,
            outputs=outputs,
        )
        if freeze:
            entry.freeze()
        self._entries.append(entry)

        if self._log_path:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")

        return entry

    def get(self, run_id: str) -> Optional[ThreadEntry]:
        return next((e for e in self._entries if e.run_id == run_id), None)

    def lineage(self) -> list[dict]:
        """Full ordered history — for certification audit."""
        return [e.to_dict() for e in self._entries]

    def verify_integrity(self) -> list[str]:
        """Re-hash all entries and report any tampering."""
        corrupted = []
        for e in self._entries:
            expected = e._compute_hash()
            if e.hash != expected:
                corrupted.append(e.run_id)
        return corrupted
