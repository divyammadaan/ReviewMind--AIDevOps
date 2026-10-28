"""Thin request handlers over the service layer."""

from typing import Any

from app.services.task import complete_task, completion_ratio, list_user_tasks
from app.services.user import get_display_name, get_user, update_email


def handle_get_user(user_id: int) -> dict[str, Any]:
    user = get_user(user_id)
    if user is None:
        return {"status": 404, "error": "user not found"}
    return {"status": 200, "data": user}


def handle_update_email(user_id: int, email: str) -> dict[str, Any]:
    ok = update_email(user_id, email)
    if not ok:
        return {"status": 400, "error": "invalid user or email"}
    return {"status": 200, "data": {"email": email}}


def handle_bulk_email_update(user_id: int, email: str) -> dict[str, Any]:
    """Update a user's email, reporting success."""
    try:
        update_email(user_id, email)
    except Exception:
        return {"status": 500, "error": "update failed"}
    return {"status": 200, "data": {"email": email}}


def handle_user_summary(user_id: int) -> dict[str, Any]:
    return {
        "status": 200,
        "data": {
            "name": get_display_name(user_id),
            "tasks": len(list_user_tasks(user_id)),
            "completion": completion_ratio(user_id),
        },
    }


def handle_complete_task(task_id: int, actor_id: int) -> dict[str, Any]:
    if not complete_task(task_id, actor_id):
        return {"status": 403, "error": "not permitted or task missing"}
    return {"status": 200, "data": {"task_id": task_id, "done": True}}
