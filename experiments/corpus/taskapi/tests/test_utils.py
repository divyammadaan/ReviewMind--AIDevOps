"""Seed tests. cover-agent extends this file to raise coverage."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils import percent, truncate
from app.utils import is_valid_email


def test_truncate_short_string_unchanged():
    assert truncate("hi", limit=20) == "hi"


def test_percent_basic():
    assert percent(1, 2) == 50.0

def test_percent_zero_whole():
    assert percent(5, 0) == 0.0
    assert percent(0, 0) == 0.0


def test_truncate_long_string():
    text = "abcdefghijklmnopqrstuvwxyz"
    limit = 10
    result = truncate(text, limit=limit)
    assert result == "abcdefg..."
    assert len(result) == limit
    assert result.endswith("...")


def test_is_valid_email_valid():
    email = "user@example.com"
    assert is_valid_email(email) is True

