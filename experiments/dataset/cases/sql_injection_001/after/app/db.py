"""Minimal in-memory data layer for the task service."""

from typing import Any, Optional

_USERS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "ada", "email": "ada@example.com", "role": "admin"},
    2: {"id": 2, "name": "linus", "email": "linus@example.com", "role": "user"},
}

_TASKS: dict[int, dict[str, Any]] = {
    10: {"id": 10, "owner_id": 1, "title": "write report", "done": False, "priority": 2},
    11: {"id": 11, "owner_id": 2, "title": "review PR", "done": True,
         "priority": 1},
}


def fetch_user(user_id: int) -> Optional[dict[str, Any]]:
    """Return the user record, or None when no such user exists.

    Callers MUST handle the None case.
    """
    return _USERS.get(user_id)


def fetch_task(task_id: int) -> Optional[dict[str, Any]]:
    """Return the task record, or None when no such task exists."""
    return _TASKS.get(task_id)


def search_tasks(term: str) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect("tasks.db")
    query = "SELECT * FROM tasks WHERE title LIKE '%" + term + "%'"
    return conn.execute(query).fetchall()


def all_tasks() -> list[dict[str, Any]]:
    return list(_TASKS.values())


def save_task(task: dict[str, Any]) -> None:
    _TASKS[task["id"]] = task
