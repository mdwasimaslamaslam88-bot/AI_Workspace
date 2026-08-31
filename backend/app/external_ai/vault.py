from __future__ import annotations

from dataclasses import replace
import base64
import json
import os
from pathlib import Path
import stat
import threading

from app.ai.routing import ModelTask
from app.core.secret_box import KEY_BYTES, SecretBoxError, XChaCha20Poly1305Box
from app.external_ai.contracts import (
    ExternalAIState,
    ExternalModelPolicy,
    ExternalProviderConfig,
    ExternalProviderKind,
    ExternalProviderRecord,
    MAX_EXTERNAL_PROVIDERS,
)
from app.external_ai.evidence import ExternalEvidenceStore, ExternalVerificationEvidence


_MAGIC = b"WSEAI1\x00"
_ADDITIONAL_DATA = b"work-station-external-ai-v1"
_MAX_VAULT_BYTES = 1_048_576


class ProviderVaultError(RuntimeError):
    """Encrypted provider state is missing, unsafe, or corrupted."""


class EncryptedProviderVault:
    """Owner-only XChaCha20-Poly1305 vault; plaintext never reaches logs."""

    def __init__(self, state_root: Path) -> None:
        root = Path(state_root)
        if not root.is_absolute():
            raise ValueError("external AI state root must be absolute")
        if root.exists() and root.is_symlink():
            raise ProviderVaultError("external AI state root must not be a link")
        self.root = root.resolve(strict=False)
        self.key_path = self.root / "provider-vault.key"
        self.vault_path = self.root / "provider-vault.enc"
        self._lock = threading.RLock()
        try:
            self._aead = XChaCha20Poly1305Box(
                magic=_MAGIC,
                additional_data=_ADDITIONAL_DATA,
            )
        except SecretBoxError as exc:
            raise ProviderVaultError(str(exc)) from exc
        self._initialize_root()
        self.evidence = ExternalEvidenceStore(self.root)
        self._key = self._load_or_create_key()

    def register_verification_evidence(self, evidence: ExternalVerificationEvidence) -> str:
        return self.evidence.register(evidence)

    def _initialize_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.root.stat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ProviderVaultError("external AI state root must be owner-only")

    def _load_or_create_key(self) -> bytes:
        try:
            metadata = self.key_path.lstat()
        except FileNotFoundError:
            if self.vault_path.exists():
                raise ProviderVaultError("provider vault key is missing")
            key = os.urandom(KEY_BYTES)
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as target:
                target.write(key)
                target.flush()
                os.fsync(target.fileno())
            self._fsync_root()
            return key
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != KEY_BYTES
        ):
            raise ProviderVaultError("provider vault key is unsafe")
        key = self.key_path.read_bytes()
        if len(key) != KEY_BYTES:
            raise ProviderVaultError("provider vault key is invalid")
        return key

    def snapshot(self) -> ExternalAIState:
        with self._lock:
            return self._read_state()

    def set_global_enabled(self, enabled: bool) -> ExternalAIState:
        if not isinstance(enabled, bool):
            raise TypeError("external AI enabled state must be boolean")
        with self._lock:
            state = replace(self._read_state(), global_enabled=enabled)
            self._write_state(state)
            return state

    def upsert_provider(
        self,
        config: ExternalProviderConfig,
        *,
        api_key: str | None = None,
    ) -> ExternalAIState:
        if not isinstance(config, ExternalProviderConfig):
            raise TypeError("external provider config is invalid")
        for model in config.models:
            if model.verified:
                self.evidence.verify(config.kind, model)
        with self._lock:
            state = self._read_state()
            providers = {
                provider.config.provider_id: provider
                for provider in state.providers
            }
            previous = providers.get(config.provider_id)
            key = api_key if api_key is not None else (
                previous.api_key if previous is not None else None
            )
            if key is None:
                raise ValueError("external provider API key is required")
            providers[config.provider_id] = ExternalProviderRecord(
                config=config,
                api_key=key,
                spent_micros=(previous.spent_micros if previous is not None else 0),
            )
            if len(providers) > MAX_EXTERNAL_PROVIDERS:
                raise ValueError("external provider limit reached")
            updated = replace(
                state,
                providers=tuple(
                    providers[provider_id]
                    for provider_id in sorted(providers)
                ),
            )
            self._write_state(updated)
            return updated

    def delete_provider(self, provider_id: str) -> ExternalAIState:
        with self._lock:
            state = self._read_state()
            updated = replace(
                state,
                providers=tuple(
                    provider
                    for provider in state.providers
                    if provider.config.provider_id != provider_id
                ),
            )
            if len(updated.providers) == len(state.providers):
                raise KeyError("external provider not found")
            self._write_state(updated)
            return updated

    def record_usage(
        self,
        provider_id: str,
        *,
        cost_micros: int,
        token_count: int,
    ) -> ExternalProviderRecord:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (cost_micros, token_count)
        ):
            raise ValueError("external provider usage is invalid")
        with self._lock:
            state = self._read_state()
            updated_provider = None
            providers = []
            for provider in state.providers:
                if provider.config.provider_id != provider_id:
                    providers.append(provider)
                    continue
                quota = provider.config.quota_remaining_tokens
                config = replace(
                    provider.config,
                    quota_remaining_tokens=(
                        None if quota is None else max(0, quota - token_count)
                    ),
                )
                updated_provider = replace(
                    provider,
                    config=config,
                    spent_micros=provider.spent_micros + cost_micros,
                )
                providers.append(updated_provider)
            if updated_provider is None:
                raise KeyError("external provider not found")
            self._write_state(replace(state, providers=tuple(providers)))
            return updated_provider

    def _read_state(self) -> ExternalAIState:
        try:
            metadata = self.vault_path.lstat()
        except FileNotFoundError:
            return ExternalAIState()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= _MAX_VAULT_BYTES
        ):
            raise ProviderVaultError("provider vault file is unsafe")
        try:
            plaintext = self._aead.decrypt(self.vault_path.read_bytes(), self._key)
            payload = json.loads(plaintext.decode("utf-8"))
            return self._decode_state(payload)
        except (ProviderVaultError, SecretBoxError) as exc:
            raise ProviderVaultError(str(exc)) from exc
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise ProviderVaultError("provider vault payload is invalid") from exc

    def _write_state(self, state: ExternalAIState) -> None:
        plaintext = json.dumps(
            self._encode_state(state),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = self._aead.encrypt(plaintext, self._key)
        if len(encrypted) > _MAX_VAULT_BYTES:
            raise ProviderVaultError("provider vault exceeds its bound")
        temporary = self.root / f".provider-vault.{os.getpid()}.{threading.get_ident()}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(encrypted)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.vault_path)
            self._fsync_root()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _fsync_root(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _encode_state(state: ExternalAIState) -> dict:
        return {
            "format_version": 1,
            "global_enabled": state.global_enabled,
            "providers": [
                {
                    "api_key": base64.b64encode(
                        provider.api_key.encode("utf-8")
                    ).decode("ascii"),
                    "config": {
                        "provider_id": provider.config.provider_id,
                        "kind": provider.config.kind.value,
                        "enabled": provider.config.enabled,
                        "free_tier": provider.config.free_tier,
                        "priority": provider.config.priority,
                        "timeout_seconds": provider.config.timeout_seconds,
                        "rate_limit_requests_per_minute": (
                            provider.config.rate_limit_requests_per_minute
                        ),
                        "spending_limit_micros": (
                            provider.config.spending_limit_micros
                        ),
                        "quota_remaining_tokens": (
                            provider.config.quota_remaining_tokens
                        ),
                        "models": [
                            {
                                "model_id": model.model_id,
                                "tasks": sorted(task.value for task in model.tasks),
                                "verified": model.verified,
                                "verification_evidence_sha256": (
                                    model.verification_evidence_sha256
                                ),
                                "measured_quality": model.measured_quality,
                                "measured_latency_ms": model.measured_latency_ms,
                                "stability_rate": model.stability_rate,
                                "context_window": model.context_window,
                                "input_cost_micros_per_million_tokens": (
                                    model.input_cost_micros_per_million_tokens
                                ),
                                "output_cost_micros_per_million_tokens": (
                                    model.output_cost_micros_per_million_tokens
                                ),
                            }
                            for model in provider.config.models
                        ],
                    },
                    "spent_micros": provider.spent_micros,
                }
                for provider in state.providers
            ],
        }

    @staticmethod
    def _decode_state(payload: object) -> ExternalAIState:
        if not isinstance(payload, dict) or set(payload) != {
            "format_version",
            "global_enabled",
            "providers",
        } or payload["format_version"] != 1:
            raise ValueError("unsupported provider vault payload")
        raw_providers = payload["providers"]
        if not isinstance(raw_providers, list):
            raise TypeError("provider vault providers are invalid")
        providers = []
        for item in raw_providers:
            if not isinstance(item, dict) or set(item) != {
                "api_key",
                "config",
                "spent_micros",
            }:
                raise ValueError("provider vault record is invalid")
            raw_config = item["config"]
            if not isinstance(raw_config, dict):
                raise TypeError("provider vault config is invalid")
            models = tuple(
                ExternalModelPolicy(
                    model_id=model["model_id"],
                    tasks=frozenset(ModelTask(task) for task in model["tasks"]),
                    verified=model["verified"],
                    verification_evidence_sha256=model[
                        "verification_evidence_sha256"
                    ],
                    measured_quality=model["measured_quality"],
                    measured_latency_ms=model["measured_latency_ms"],
                    stability_rate=model["stability_rate"],
                    context_window=model["context_window"],
                    input_cost_micros_per_million_tokens=model[
                        "input_cost_micros_per_million_tokens"
                    ],
                    output_cost_micros_per_million_tokens=model[
                        "output_cost_micros_per_million_tokens"
                    ],
                )
                for model in raw_config["models"]
            )
            config = ExternalProviderConfig(
                provider_id=raw_config["provider_id"],
                kind=ExternalProviderKind(raw_config["kind"]),
                enabled=raw_config["enabled"],
                free_tier=raw_config["free_tier"],
                priority=raw_config["priority"],
                timeout_seconds=raw_config["timeout_seconds"],
                rate_limit_requests_per_minute=raw_config[
                    "rate_limit_requests_per_minute"
                ],
                spending_limit_micros=raw_config["spending_limit_micros"],
                quota_remaining_tokens=raw_config["quota_remaining_tokens"],
                models=models,
            )
            try:
                api_key = base64.b64decode(
                    item["api_key"], validate=True
                ).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ValueError("provider vault key encoding is invalid") from exc
            providers.append(
                ExternalProviderRecord(
                    config=config,
                    api_key=api_key,
                    spent_micros=item["spent_micros"],
                )
            )
        return ExternalAIState(
            global_enabled=payload["global_enabled"],
            providers=tuple(providers),
        )
