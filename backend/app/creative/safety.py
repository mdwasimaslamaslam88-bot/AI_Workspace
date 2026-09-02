from __future__ import annotations

import re
import unicodedata


class CreativeSafetyError(ValueError):
    """Creative content is outside the fixed general-audience boundary."""


_DISALLOWED_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:porn|pornographic|erotic|fetish)\b",
        r"\b(?:explicit sexual|sex scene|sexual roleplay|nude|nudity)\b",
        r"\b(?:rape|non[- ]?consensual|sexual coercion)\b",
        r"\b(?:incest|bestiality|sexual exploitation|sexual grooming)\b",
        r"\b(?:child|minor|underage|teen)\b.{0,48}\b(?:sex|sexual|nude|erotic)\b",
        r"\b(?:sex|sexual|nude|erotic)\b.{0,48}\b(?:child|minor|underage|teen)\b",
    )
)


class CreativeSafetyPolicy:
    """A bounded defense-in-depth gate for the non-adult creative workspace."""

    @staticmethod
    def validate(value: str) -> None:
        if not isinstance(value, str):
            raise CreativeSafetyError("creative content is invalid")
        normalized = unicodedata.normalize("NFKC", value)
        if any(pattern.search(normalized) for pattern in _DISALLOWED_PATTERNS):
            raise CreativeSafetyError(
                "creative content requires an unavailable protected-experience gate"
            )
