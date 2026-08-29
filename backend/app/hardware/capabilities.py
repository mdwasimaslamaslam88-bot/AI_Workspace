from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Callable

from app.hardware.planner import HardwareInventory, detect_hardware


MAX_HARDWARE_STATE_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class HardwareCapabilityState:
    inventory: HardwareInventory
    fingerprint: str
    previous_fingerprint: str | None
    pending_fingerprint: str | None = None
    upgrade_detected: bool = False
    cache_invalidated: bool = False
    restart_required: bool = False


class HardwareCapabilityService:
    """Own hardware discovery and safe startup/change state transitions."""

    def __init__(
        self,
        state_path: Path | None,
        *,
        detector: Callable[[], HardwareInventory] = detect_hardware,
    ) -> None:
        self.state_path = state_path
        self.detector = detector
        self._state: HardwareCapabilityState | None = None

    @property
    def state(self) -> HardwareCapabilityState:
        if self._state is None:
            raise RuntimeError("hardware capability service has not started")
        return self._state

    def startup(self) -> HardwareCapabilityState:
        inventory = self.detector()
        previous = self._read_previous_fingerprint()
        fingerprint = inventory.fingerprint
        changed = previous is not None and previous != fingerprint
        self._state = HardwareCapabilityState(
            inventory=inventory,
            fingerprint=fingerprint,
            previous_fingerprint=previous,
            upgrade_detected=changed,
            cache_invalidated=changed,
        )
        return self._state

    def confirm_active(self) -> HardwareCapabilityState:
        current = self.state
        self._persist_fingerprint(current.fingerprint)
        return current

    def mark_validation_failed(self) -> HardwareCapabilityState:
        current = self.state
        self._state = HardwareCapabilityState(
            inventory=current.inventory,
            fingerprint=current.fingerprint,
            previous_fingerprint=current.previous_fingerprint,
            pending_fingerprint=current.pending_fingerprint,
            upgrade_detected=current.upgrade_detected,
            cache_invalidated=current.cache_invalidated,
            restart_required=True,
        )
        return self._state

    def refresh(self) -> HardwareCapabilityState:
        current = self.state
        detected = self.detector()
        pending = detected.fingerprint
        if pending == current.fingerprint:
            self._state = HardwareCapabilityState(
                inventory=current.inventory,
                fingerprint=current.fingerprint,
                previous_fingerprint=current.previous_fingerprint,
                upgrade_detected=current.upgrade_detected,
                cache_invalidated=current.cache_invalidated,
            )
            return self._state

        # Runtime adapters may own live GPU allocations.  Do not mutate active
        # admission or routing in-process; the next normal startup validates the
        # new runtime before activation and retains current fallbacks on failure.
        self._state = HardwareCapabilityState(
            inventory=current.inventory,
            fingerprint=current.fingerprint,
            previous_fingerprint=current.previous_fingerprint,
            pending_fingerprint=pending,
            upgrade_detected=True,
            cache_invalidated=True,
            restart_required=True,
        )
        return self._state

    def _read_previous_fingerprint(self) -> str | None:
        path = self.state_path
        if path is None:
            return None
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_size > MAX_HARDWARE_STATE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None
        fingerprint = payload.get("fingerprint") if isinstance(payload, dict) else None
        if (
            isinstance(fingerprint, str)
            and len(fingerprint) == 64
            and all(character in "0123456789abcdef" for character in fingerprint)
        ):
            return fingerprint
        return None

    def _persist_fingerprint(self, fingerprint: str) -> None:
        path = self.state_path
        if path is None:
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() and path.is_symlink():
            raise RuntimeError("hardware state path must not be a symbolic link")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(
                    {"schema_version": 1, "fingerprint": fingerprint},
                    target,
                    separators=(",", ":"),
                )
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
