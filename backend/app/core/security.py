import hashlib
import re
import secrets


ACCESS_TOKEN_BYTES = 32
ACCESS_TOKEN_LENGTH = 43
_ACCESS_TOKEN_PATTERN = re.compile(
    rf"^[A-Za-z0-9_-]{{{ACCESS_TOKEN_LENGTH}}}$"
)


def generate_access_token() -> str:
    """Return a high-entropy opaque credential suitable for bearer use."""

    return secrets.token_urlsafe(ACCESS_TOKEN_BYTES)


def digest_access_token(access_token: str) -> str:
    """Return the non-reversible digest persisted for credential lookup."""

    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def is_access_token_format_valid(access_token: str) -> bool:
    """Reject malformed credentials before performing a persistence lookup."""

    return _ACCESS_TOKEN_PATTERN.fullmatch(access_token) is not None


def is_user_provisioning_authorized(
    provisioning_token: str | None,
    expected_digest: str | None,
) -> bool:
    """Match an operator credential without retaining or exposing plaintext."""

    if expected_digest is None or provisioning_token is None:
        return False
    if not is_access_token_format_valid(provisioning_token):
        return False
    return secrets.compare_digest(
        digest_access_token(provisioning_token),
        expected_digest,
    )
