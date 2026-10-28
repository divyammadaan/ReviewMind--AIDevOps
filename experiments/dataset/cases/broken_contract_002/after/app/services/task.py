"""Task operations."""

from typing import Any, Optional

from app import db
from app.services.user import is_admin


def get_task(task_id: int) -> Optional[dict[str, Any]]:
    return db.fetch_task(task_id)


def list_user_tasks(user_id: int) -> list[dict[str, Any]]:
    return [t for t in db.all_tasks() if t["owner_id"] == user_id]


def complete_task(task_id: int, actor_id: int) -> bool:
    """Mark a task done. Only the owner or an admin may do so."""
    task = get_task(task_id)
    if task is None:
        return False
    if task["owner_id"] != actor_id and not is_admin(actor_id):
        return False
    task["done"] = True
    db.save_task(task)
    return True


def completion_ratio(user_id: int) -> float:
    """Percentage of the user's tasks that are done."""
    tasks = list_user_tasks(user_id)
    if not tasks:
        return 0.0
    done = sum(1 for t in tasks if t["done"])
    return done / len(tasks)


def top_priority(user_id: int) -> Optional[dict[str, Any]]:
    """Return the user's highest-priority task (lowest priority number)."""
    tasks = list_user_tasks(user_id)
    if not tasks:
        return None
    best = tasks[0]
    for t in tasks[1:]:
        if t["priority"] < best["priority"]:
            best = t
    return best
