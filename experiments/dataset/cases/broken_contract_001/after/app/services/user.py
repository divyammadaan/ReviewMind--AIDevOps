"""User-facing operations."""

from typing import Any, Optional

from app import db
from app.utils import is_valid_email


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    """Look up a user.

    Returns None when the user does not exist -- callers must check.
    """
    return db.fetch_user(user_id)


def get_display_name(user_id: int) -> str:
    """Human-readable name for a user, or 'unknown' when absent."""
    user = get_user(user_id)
    if user is None:
        return "unknown"
    return user["name"]


def update_email(user_id: int, new_email: str) -> bool:
    """Change a user's email. Returns False when the user or email is invalid."""
    if not is_valid_email(new_email):
        raise ValueError("invalid email")
    user = get_user(user_id)
    if user is None:
        raise LookupError("no such user")
    user["email"] = new_email
    return True


def is_admin(user_id: int) -> bool:
    user = get_user(user_id)
    if user is None:
        return False
    return user.get("role") == "admin"
