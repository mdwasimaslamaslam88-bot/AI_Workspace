import json
import os
from datetime import datetime, timezone

import pytest

from app.connectors.credentials import (
    ConnectorCredentialBox,
    ConnectorCredentialError,
    OAuth2Credential,
    decode_oauth2_credential,
    encode_oauth2_credential,
)


def test_connector_credentials_are_authenticated_encrypted_and_owner_only(tmp_path):
    root = tmp_path / "connector-state"
    box = ConnectorCredentialBox(root)
    secret = "private-connector-token-123456"

    ciphertext = box.encrypt(secret)

    assert secret not in ciphertext
    assert box.decrypt(ciphertext) == secret
    assert os.stat(root).st_mode & 0o077 == 0
    assert os.stat(box.key_path).st_mode & 0o077 == 0
    changed = ciphertext[:-1] + ("A" if ciphertext[-1] != "A" else "B")
    with pytest.raises(ConnectorCredentialError):
        box.decrypt(changed)


def test_connector_credential_root_rejects_links_and_open_permissions(tmp_path):
    open_root = tmp_path / "open"
    open_root.mkdir(mode=0o755)
    open_root.chmod(0o755)
    with pytest.raises(ConnectorCredentialError, match="owner-only"):
        ConnectorCredentialBox(open_root)

    link = tmp_path / "link"
    link.symlink_to(open_root, target_is_directory=True)
    with pytest.raises(ConnectorCredentialError, match="link"):
        ConnectorCredentialBox(link)


def test_oauth_refresh_envelope_is_strict_and_remains_inside_encryption(tmp_path):
    expiry = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    oauth2 = OAuth2Credential(
        access_token="access-token-000000000000",
        refresh_token="refresh-token-0000000000",
        client_id="owner-client",
        client_secret="client-secret-0000000000",
        token_origin="https://identity.example.test",
        token_path="/oauth/token",
        expires_at=expiry,
    )
    envelope = encode_oauth2_credential(oauth2)
    assert decode_oauth2_credential(envelope) == oauth2

    box = ConnectorCredentialBox(tmp_path / "oauth-state")
    ciphertext = box.encrypt(envelope)
    assert oauth2.access_token not in ciphertext
    assert oauth2.refresh_token not in ciphertext
    assert oauth2.client_secret not in ciphertext
    assert decode_oauth2_credential(box.decrypt(ciphertext)) == oauth2

    with pytest.raises(ConnectorCredentialError, match="envelope"):
        decode_oauth2_credential('{"version":1}')

    legacy = json.dumps({
        "access_token": "legacy-access-token-000000",
        "client_id": "owner-client",
        "client_secret": "client-secret-0000000000",
        "expires_at": expiry.isoformat(),
        "refresh_token": "refresh-token-0000000000",
        "token_path": "/oauth/token",
        "version": 1,
    })
    decoded_legacy = decode_oauth2_credential(legacy)
    assert decoded_legacy is not None
    assert decoded_legacy.token_origin is None

    with pytest.raises(ValueError, match="requires refresh"):
        encode_oauth2_credential(
            OAuth2Credential(
                access_token="access-token-000000000000",
                refresh_token=None,
                client_id=None,
                client_secret=None,
                token_path=None,
                expires_at=None,
                token_origin="https://identity.example.test",
            )
        )
