from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    removed_staging: int = 0
    removed_deleted: int = 0
    quarantined_unknown: int = 0
    missing_active: tuple[str, ...] = ()
    unexpected_nodes: tuple[str, ...] = ()


class StagedAssetWrite(Protocol):
    @property
    def byte_size(self) -> int: ...

    @property
    def content_sha256(self) -> str: ...

    def write(self, chunk: bytes) -> None: ...

    def finalize(self) -> str: ...

    def abort(self) -> None: ...


class AssetStorage(Protocol):
    def begin_write(self, asset_id: UUID) -> StagedAssetWrite: ...

    def open_read(self, storage_key: str) -> BinaryIO: ...

    def delete(self, storage_key: str) -> bool: ...

    def reconcile(
        self,
        *,
        active_keys: set[str] | None,
        deleted_keys: set[str],
    ) -> ReconciliationReport: ...
