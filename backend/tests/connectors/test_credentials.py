import os

import pytest

from app.connectors.credentials import ConnectorCredentialBox, ConnectorCredentialError


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
