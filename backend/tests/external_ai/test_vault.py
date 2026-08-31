from dataclasses import replace
import os
import stat

import pytest

from app.ai.routing import ModelTask
from app.external_ai.contracts import (
    ExternalModelPolicy,
    ExternalProviderConfig,
    ExternalProviderKind,
)
from app.external_ai.vault import EncryptedProviderVault, ProviderVaultError
from app.external_ai.evidence import ExternalEvidenceError, ExternalVerificationEvidence


API_KEY = "test-provider-key-that-is-long-enough"


def _config(**values):
    return ExternalProviderConfig(
        provider_id="openai-primary",
        kind=ExternalProviderKind.OPENAI,
        enabled=True,
        models=(
            ExternalModelPolicy(
                model_id="verified-model",
                tasks=frozenset({ModelTask.REASONING}),
                verified=True,
                verification_evidence_sha256="a" * 64,
                measured_quality=96.5,
                measured_latency_ms=10,
                stability_rate=1,
                context_window=32_768,
                input_cost_micros_per_million_tokens=2_000_000,
                output_cost_micros_per_million_tokens=8_000_000,
            ),
        ),
        **values,
    )


def _admit(
    vault: EncryptedProviderVault,
    config: ExternalProviderConfig,
) -> ExternalProviderConfig:
    models = []
    for model in config.models:
        digest = vault.register_verification_evidence(
            ExternalVerificationEvidence(
                provider_kind=config.kind,
                model_id=model.model_id,
                tasks=model.tasks,
                benchmark_artifact_sha256="f" * 64,
                complete_category=True,
                passed=True,
                measured_quality=model.measured_quality,
                measured_latency_ms=model.measured_latency_ms,
                stability_rate=model.stability_rate,
                context_window=model.context_window,
                input_cost_micros_per_million_tokens=model.input_cost_micros_per_million_tokens,
                output_cost_micros_per_million_tokens=model.output_cost_micros_per_million_tokens,
            )
        )
        models.append(replace(model, verification_evidence_sha256=digest))
    return replace(config, models=tuple(models))


def test_provider_vault_encrypts_keys_and_uses_owner_only_files(tmp_path):
    root = tmp_path / "external-ai"
    vault = EncryptedProviderVault(root)
    vault.upsert_provider(_admit(vault, _config()), api_key=API_KEY)
    vault.set_global_enabled(True)

    ciphertext = vault.vault_path.read_bytes()
    assert API_KEY.encode() not in ciphertext
    assert b"verified-model" not in ciphertext
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(vault.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(vault.vault_path.stat().st_mode) == 0o600

    reopened = EncryptedProviderVault(root).snapshot()
    assert reopened.global_enabled is True
    assert reopened.providers[0].api_key == API_KEY
    assert reopened.providers[0].config.models[0].verified is True


def test_provider_vault_authentication_detects_tampering(tmp_path):
    vault = EncryptedProviderVault(tmp_path / "external-ai")
    vault.upsert_provider(_admit(vault, _config()), api_key=API_KEY)
    encrypted = bytearray(vault.vault_path.read_bytes())
    encrypted[-1] ^= 1
    vault.vault_path.write_bytes(encrypted)

    with pytest.raises(ProviderVaultError, match="authentication"):
        vault.snapshot()


def test_provider_vault_preserves_key_on_policy_update_and_tracks_usage(tmp_path):
    vault = EncryptedProviderVault(tmp_path / "external-ai")
    vault.upsert_provider(_admit(vault, _config()), api_key=API_KEY)
    vault.upsert_provider(_admit(vault, _config(priority=2)))
    updated = vault.record_usage(
        "openai-primary",
        cost_micros=11,
        token_count=7,
    )

    assert updated.api_key == API_KEY
    assert updated.config.priority == 2
    assert updated.spent_micros == 11


def test_provider_vault_rejects_links_and_permissive_roots(tmp_path):
    permissive = tmp_path / "permissive"
    permissive.mkdir(mode=0o755)
    with pytest.raises(ProviderVaultError, match="owner-only"):
        EncryptedProviderVault(permissive)

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ProviderVaultError, match="link"):
        EncryptedProviderVault(linked)


def test_unknown_external_model_never_production_admits():
    with pytest.raises(ValueError, match="require evidence"):
        ExternalModelPolicy(
            model_id="unverified",
            tasks=frozenset({ModelTask.REASONING}),
            verified=True,
        )


def test_verified_external_model_requires_registered_matching_category_evidence(tmp_path):
    vault = EncryptedProviderVault(tmp_path / "external-ai")
    with pytest.raises(ExternalEvidenceError, match="not registered"):
        vault.upsert_provider(_config(), api_key=API_KEY)

    admitted = _admit(vault, _config())
    mismatched_model = replace(admitted.models[0], measured_quality=1)
    with pytest.raises(ExternalEvidenceError, match="does not match"):
        vault.upsert_provider(
            replace(admitted, models=(mismatched_model,)),
            api_key=API_KEY,
        )


def test_existing_evidence_must_remain_owner_only(tmp_path):
    vault = EncryptedProviderVault(tmp_path / "external-ai")
    config = _config()
    admitted = _admit(vault, config)
    digest = admitted.models[0].verification_evidence_sha256
    assert digest is not None
    path = vault.evidence.root / f"{digest}.json"
    path.chmod(0o644)

    with pytest.raises(ExternalEvidenceError, match="unsafe file"):
        _admit(vault, config)
