from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShadowObservation:
    generation: int
    head_sha: str
    domain: str
    legacy_projection: dict[str, Any]
    successor_projection: dict[str, Any]
    observed_at: str
    input_digest: str
    parity: bool


class ShadowRecorder:
    """Append-only local shadow evidence. It deliberately has no network/publisher dependency."""

    def __init__(self, directory: Path):
        self.directory = directory

    def observe(
        self,
        *,
        generation: int,
        head_sha: str,
        domain: str,
        inputs: dict[str, Any],
        legacy: Callable[[dict[str, Any]], dict[str, Any]],
        successor: Callable[[dict[str, Any]], dict[str, Any]],
        observed_at: str,
    ) -> ShadowObservation:
        if generation < 1 or not head_sha.strip() or not domain.strip() or not observed_at.strip():
            raise ValueError("invalid shadow observation identity")
        canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        legacy_projection = legacy(inputs)
        successor_projection = successor(inputs)
        record = ShadowObservation(
            generation=generation,
            head_sha=head_sha,
            domain=domain,
            legacy_projection=legacy_projection,
            successor_projection=successor_projection,
            observed_at=observed_at,
            input_digest=digest,
            parity=legacy_projection == successor_projection,
        )
        self._write(record)
        return record

    def _write(self, record: ShadowObservation) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(
            f"{record.generation}:{record.head_sha}:{record.domain}:{record.input_digest}".encode()
        ).hexdigest()
        target = self.directory / f"{key}.json"
        encoded = json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n"
        if target.exists():
            if target.read_text() != encoded:
                raise ValueError("shadow observation identity collision")
            return
        fd, tmp_name = tempfile.mkstemp(prefix=".shadow.", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
