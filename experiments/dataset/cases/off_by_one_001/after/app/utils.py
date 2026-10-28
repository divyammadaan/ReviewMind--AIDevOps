"""Assorted helpers."""

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def truncate(text: str, limit: int = 20) -> str:
    """Shorten text to `limit` characters, appending an ellipsis when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 2] + "..."


def percent(part: int, whole: int) -> float:
    """Return part/whole as a percentage. Returns 0.0 when whole is zero."""
    if whole == 0:
        return 0.0
    return (part / whole) * 100.0
