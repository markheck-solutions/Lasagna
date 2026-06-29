"""Service ID parsing and de-duplication."""

from __future__ import annotations

import re
from dataclasses import dataclass

SERVICE_ID_PATTERN = re.compile(r"^(ICB|IC)-\d{6}$")
TOKEN_SPLIT_PATTERN = re.compile(r"[\s,;]+")


@dataclass(frozen=True)
class ParsedServiceInput:
    """One pasted input token and its normalized service ID state."""

    input_order: int
    input_text: str
    normalized_id: str
    is_valid: bool
    duplicate_of: int | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


def parse_service_id_text(raw_text: str) -> list[ParsedServiceInput]:
    """Parse pasted text into ordered service ID inputs."""
    tokens = [token.strip() for token in TOKEN_SPLIT_PATTERN.split(raw_text) if token.strip()]
    seen_valid: dict[str, int] = {}
    parsed: list[ParsedServiceInput] = []

    for input_order, token in enumerate(tokens, start=1):
        normalized = token.upper()
        is_valid = bool(SERVICE_ID_PATTERN.fullmatch(normalized))
        duplicate_of = seen_valid.get(normalized) if is_valid else None
        if is_valid and duplicate_of is None:
            seen_valid[normalized] = input_order
        parsed.append(
            ParsedServiceInput(
                input_order=input_order,
                input_text=token,
                normalized_id=normalized,
                is_valid=is_valid,
                duplicate_of=duplicate_of,
            )
        )

    return parsed


def unique_valid_service_ids(inputs: list[ParsedServiceInput]) -> list[str]:
    """Return first-seen valid normalized IDs."""
    return [item.normalized_id for item in inputs if item.is_valid and item.duplicate_of is None]
