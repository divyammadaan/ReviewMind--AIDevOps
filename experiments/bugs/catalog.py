"""Catalogue of seeded defects for the ReviewMind evaluation.

Each entry is a single, surgical edit to one file of the clean corpus. Applying it
produces exactly one known defect, which gives us exact ground truth for both recall
and precision.

Design rules, fixed BEFORE any arm was run (see docs/RESEARCH_DESIGN.md §7.6):
  * `find` must match exactly once in the target file, or injection fails loudly.
    We never want a silently mis-injected case polluting the dataset.
  * `requires_cross_file_context` records whether the defect can be recognised from
    the diff alone. This flag is what lets us test H3 (retrieval should help
    selectively), so it is assigned from the defect's nature, not from results.
  * The catalogue deliberately includes classes we expect static analysis to WIN on.
    A dataset chosen to flatter ReviewMind would make the comparison worthless.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SeededDefect:
    case_id: str
    defect_class: str
    target_file: str
    find: str
    replace: str
    description: str
    why_wrong: str
    requires_cross_file_context: bool
    static_analysis_expected: bool
    # "deletion" -> defect created by REMOVING correct code.
    # "addition" -> defect created by ADDING new wrong code only.
    #
    # This distinction turned out to be experimentally critical. In the first
    # live run, Arm B (diff-only) scored 4/4 on the cross-file cases it should
    # not have been able to judge. Inspecting its reasoning showed why: for
    # deletion-mode defects the REMOVED lines appear in the diff as `-` lines,
    # and those lines state the very contract that makes the change wrong
    # (e.g. `- if user is None: return "unknown"`). The diff leaked the answer,
    # so retrieval had nothing left to contribute and RQ2 could not be tested.
    #
    # Addition-mode defects contain no removed lines, so the hazard is only
    # discoverable from another file. They are the valid test bed for RQ2.
    injection_mode: str = "deletion"
    tags: tuple[str, ...] = field(default=())


CATALOG: list[SeededDefect] = [
    # ---------------------------------------------------------------- #
    # Class 1: hardcoded_secret -- static analysis SHOULD win here.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="hardcoded_secret_001",
        defect_class="hardcoded_secret",
        target_file="app/db.py",
        find='from typing import Any, Optional',
        replace=(
            'from typing import Any, Optional\n'
            '\n'
            '# connection credentials\n'
            'DB_PASSWORD = "sup3rs3cret-prod-password"\n'
            'API_TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"'
        ),
        description="Production credentials hardcoded as module constants.",
        why_wrong=(
            "Secrets in source are committed to version control, readable by anyone "
            "with repo access, and cannot be rotated without a code change."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=True,
        tags=("security",),
    ),

    # ---------------------------------------------------------------- #
    # Class 2: bare_except -- ruff E722 should catch this.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="bare_except_001",
        defect_class="bare_except",
        target_file="app/utils.py",
        find=(
            'def is_valid_email(value: str) -> bool:\n'
            '    return bool(_EMAIL_RE.match(value))'
        ),
        replace=(
            'def is_valid_email(value: str) -> bool:\n'
            '    try:\n'
            '        return bool(_EMAIL_RE.match(value))\n'
            '    except:\n'
            '        pass'
        ),
        description="Bare `except:` that swallows all exceptions and returns None implicitly.",
        why_wrong=(
            "A bare except catches KeyboardInterrupt and SystemExit, hiding real "
            "failures. The function is annotated `-> bool` but returns None on the "
            "exception path, so callers doing `if is_valid_email(x)` silently treat "
            "errors as invalid input."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=True,
        tags=("error_handling", "type_contract"),
    ),

    # ---------------------------------------------------------------- #
    # Class 3: sql_injection -- bandit should partially catch this.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="sql_injection_001",
        defect_class="sql_injection",
        target_file="app/db.py",
        find='def all_tasks() -> list[dict[str, Any]]:\n    return list(_TASKS.values())',
        replace=(
            'def search_tasks(term: str) -> list[dict[str, Any]]:\n'
            '    import sqlite3\n'
            '\n'
            '    conn = sqlite3.connect("tasks.db")\n'
            '    query = "SELECT * FROM tasks WHERE title LIKE \'%" + term + "%\'"\n'
            '    return conn.execute(query).fetchall()\n'
            '\n'
            '\n'
            'def all_tasks() -> list[dict[str, Any]]:\n'
            '    return list(_TASKS.values())'
        ),
        description="SQL query built by string concatenation with caller-supplied input.",
        why_wrong=(
            "An attacker controlling `term` can terminate the literal and append "
            "arbitrary SQL. Parameterised queries are required."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=True,
        tags=("security",),
    ),

    # ---------------------------------------------------------------- #
    # Class 4: off_by_one -- static analysis should NOT catch this.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="off_by_one_001",
        defect_class="off_by_one",
        target_file="app/utils.py",
        find='    return text[: limit - 3] + "..."',
        replace='    return text[: limit - 2] + "..."',
        description="truncate() returns a string one character longer than `limit`.",
        why_wrong=(
            "Slicing to limit-2 and appending a 3-character ellipsis yields "
            "limit+1 characters, violating the function's documented contract that "
            "output is at most `limit` characters. Breaks fixed-width callers."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=False,
        tags=("logic",),
    ),

    SeededDefect(
        case_id="off_by_one_002",
        defect_class="off_by_one",
        target_file="app/services/task.py",
        find='    best = tasks[0]\n    for t in tasks[1:]:',
        replace='    best = tasks[0]\n    for t in tasks[2:]:',
        description="top_priority() skips the second task when scanning for the minimum.",
        why_wrong=(
            "Starting the scan at index 2 never compares tasks[1], so if that task "
            "has the highest priority it is silently never returned."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=False,
        tags=("logic",),
    ),

    # ---------------------------------------------------------------- #
    # Class 5: missing_error_handling
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="missing_error_handling_001",
        defect_class="missing_error_handling",
        target_file="app/utils.py",
        find=(
            'def percent(part: int, whole: int) -> float:\n'
            '    """Return part/whole as a percentage. Returns 0.0 when whole is zero."""\n'
            '    if whole == 0:\n'
            '        return 0.0\n'
            '    return (part / whole) * 100.0'
        ),
        replace=(
            'def percent(part: int, whole: int) -> float:\n'
            '    """Return part/whole as a percentage. Returns 0.0 when whole is zero."""\n'
            '    return (part / whole) * 100.0'
        ),
        description="Zero-division guard removed from percent(), contradicting its own docstring.",
        why_wrong=(
            "percent(x, 0) now raises ZeroDivisionError. The docstring still promises "
            "0.0, so callers written against the documented behaviour will crash."
        ),
        requires_cross_file_context=False,
        static_analysis_expected=False,
        tags=("error_handling", "logic"),
    ),

    # ---------------------------------------------------------------- #
    # Class 6: null_dereference -- REQUIRES cross-file context.
    # The diff looks locally reasonable; it is only wrong because of
    # db.fetch_user()'s contract, which lives in another file.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="null_deref_001",
        defect_class="null_dereference",
        target_file="app/services/user.py",
        find=(
            'def get_display_name(user_id: int) -> str:\n'
            '    """Human-readable name for a user, or \'unknown\' when absent."""\n'
            '    user = get_user(user_id)\n'
            '    if user is None:\n'
            '        return "unknown"\n'
            '    return user["name"]'
        ),
        replace=(
            'def get_display_name(user_id: int) -> str:\n'
            '    """Human-readable name for a user, or \'unknown\' when absent."""\n'
            '    user = get_user(user_id)\n'
            '    return user["name"]'
        ),
        description="None-check removed before dereferencing the result of get_user().",
        why_wrong=(
            "get_user() delegates to db.fetch_user(), which returns None for unknown "
            "ids. Indexing None raises TypeError. Recognising this requires knowing "
            "fetch_user's contract, which is defined in app/db.py -- outside the diff."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        tags=("logic", "error_handling", "cross_file"),
    ),

    SeededDefect(
        case_id="null_deref_002",
        defect_class="null_dereference",
        target_file="app/services/task.py",
        find=(
            'def complete_task(task_id: int, actor_id: int) -> bool:\n'
            '    """Mark a task done. Only the owner or an admin may do so."""\n'
            '    task = get_task(task_id)\n'
            '    if task is None:\n'
            '        return False\n'
            '    if task["owner_id"] != actor_id and not is_admin(actor_id):'
        ),
        replace=(
            'def complete_task(task_id: int, actor_id: int) -> bool:\n'
            '    """Mark a task done. Only the owner or an admin may do so."""\n'
            '    task = get_task(task_id)\n'
            '    if task["owner_id"] != actor_id and not is_admin(actor_id):'
        ),
        description="None-check removed before dereferencing get_task() result.",
        why_wrong=(
            "get_task() returns db.fetch_task(), which is Optional. A request for a "
            "non-existent task id now raises TypeError instead of returning False."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        tags=("logic", "error_handling", "cross_file"),
    ),

    # ---------------------------------------------------------------- #
    # Class 7: broken_contract -- REQUIRES cross-file context.
    # The edited function is self-consistent; the bug is that CALLERS
    # in other files rely on the old return convention.
    # ---------------------------------------------------------------- #
    SeededDefect(
        case_id="broken_contract_001",
        defect_class="broken_contract",
        target_file="app/services/user.py",
        find=(
            'def update_email(user_id: int, new_email: str) -> bool:\n'
            '    """Change a user\'s email. Returns False when the user or email is invalid."""\n'
            '    if not is_valid_email(new_email):\n'
            '        return False\n'
            '    user = get_user(user_id)\n'
            '    if user is None:\n'
            '        return False\n'
            '    user["email"] = new_email\n'
            '    return True'
        ),
        replace=(
            'def update_email(user_id: int, new_email: str) -> bool:\n'
            '    """Change a user\'s email. Returns False when the user or email is invalid."""\n'
            '    if not is_valid_email(new_email):\n'
            '        raise ValueError("invalid email")\n'
            '    user = get_user(user_id)\n'
            '    if user is None:\n'
            '        raise LookupError("no such user")\n'
            '    user["email"] = new_email\n'
            '    return True'
        ),
        description="update_email() switched from returning False to raising, breaking its caller.",
        why_wrong=(
            "app/api.py's handle_update_email() checks the boolean return to produce a "
            "400 response. It has no try/except, so invalid input now propagates an "
            "uncaught exception instead of a 400. The function's new behaviour is "
            "internally coherent -- the defect is only visible from the caller, in "
            "another file."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        tags=("api_contract", "cross_file"),
    ),

    SeededDefect(
        case_id="broken_contract_002",
        defect_class="broken_contract",
        target_file="app/services/task.py",
        find=(
            'def completion_ratio(user_id: int) -> float:\n'
            '    """Percentage of the user\'s tasks that are done."""\n'
            '    tasks = list_user_tasks(user_id)\n'
            '    if not tasks:\n'
            '        return 0.0\n'
            '    done = sum(1 for t in tasks if t["done"])\n'
            '    return (done / len(tasks)) * 100.0'
        ),
        replace=(
            'def completion_ratio(user_id: int) -> float:\n'
            '    """Percentage of the user\'s tasks that are done."""\n'
            '    tasks = list_user_tasks(user_id)\n'
            '    if not tasks:\n'
            '        return 0.0\n'
            '    done = sum(1 for t in tasks if t["done"])\n'
            '    return done / len(tasks)'
        ),
        description="completion_ratio() now returns a 0-1 fraction while still documented as a percentage.",
        why_wrong=(
            "The docstring and the function name promise a percentage, and "
            "app/api.py surfaces this value as `completion` in a user-facing summary. "
            "Returning 0.5 instead of 50.0 silently corrupts that output -- no "
            "exception, just wrong numbers."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        tags=("api_contract", "silent_corruption", "cross_file"),
    ),
]


# ===================================================================== #
# ADDITION-MODE DEFECTS -- the valid test bed for RQ2.
#
# Each adds a NEW function containing a defect. Nothing is deleted, so the diff
# contains no `-` line that could reveal the contract being violated. Detecting
# these requires knowing something defined in ANOTHER file.
# ===================================================================== #

ADDITIVE_CATALOG: list[SeededDefect] = [
    SeededDefect(
        case_id="add_null_deref_001",
        defect_class="null_dereference",
        target_file="app/api.py",
        find=(
            'def handle_complete_task(task_id: int, actor_id: int) -> dict[str, Any]:'
        ),
        replace=(
            'def handle_user_badge(user_id: int) -> dict[str, Any]:\n'
            '    """Short display badge for a user."""\n'
            '    user = get_user(user_id)\n'
            '    return {"status": 200, "data": {"badge": user["name"].upper()}}\n'
            '\n'
            '\n'
            'def handle_complete_task(task_id: int, actor_id: int) -> dict[str, Any]:'
        ),
        description="New handler dereferences get_user() without a None check.",
        why_wrong=(
            "get_user() -> db.fetch_user() returns None for unknown ids, so "
            "user[\"name\"] raises TypeError. Nothing in the added code hints at "
            "this; the Optional return is declared in app/db.py and app/services/"
            "user.py, both outside the diff."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        injection_mode="addition",
        tags=("logic", "crash", "cross_file", "additive"),
    ),

    SeededDefect(
        case_id="add_null_deref_002",
        defect_class="null_dereference",
        # Deliberately placed in task.py, NOT api.py. An earlier draft added this
        # function to api.py, where `get_task` is not imported -- ruff then
        # flagged F821 (undefined name), handing the static baseline a detection
        # for entirely the wrong reason and making the case unrealistic. Here
        # `get_task` is defined locally, so the only thing that makes the code
        # wrong is `db.fetch_task`'s Optional return, in app/db.py.
        target_file="app/services/task.py",
        find='def completion_ratio(user_id: int) -> float:',
        replace=(
            'def task_title(task_id: int) -> str:\n'
            '    """Return the title of a task."""\n'
            '    task = get_task(task_id)\n'
            '    return task["title"]\n'
            '\n'
            '\n'
            'def completion_ratio(user_id: int) -> float:'
        ),
        description="New function dereferences get_task() without a None check.",
        why_wrong=(
            "get_task() returns db.fetch_task(), which is Optional. An unknown "
            "task id makes task[\"title\"] raise TypeError. Nothing in the added "
            "code hints at this -- the Optional contract is declared in app/db.py, "
            "outside the diff."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        injection_mode="addition",
        tags=("logic", "crash", "cross_file", "additive"),
    ),

    SeededDefect(
        case_id="add_double_scale_001",
        defect_class="broken_contract",
        target_file="app/api.py",
        find='def handle_complete_task(task_id: int, actor_id: int) -> dict[str, Any]:',
        replace=(
            'def handle_completion_badge(user_id: int) -> dict[str, Any]:\n'
            '    """Completion percentage, formatted for display."""\n'
            '    ratio = completion_ratio(user_id)\n'
            '    return {"status": 200, "data": {"percent": f"{ratio * 100:.0f}%"}}\n'
            '\n'
            '\n'
            'def handle_complete_task(task_id: int, actor_id: int) -> dict[str, Any]:'
        ),
        description="New handler multiplies completion_ratio() by 100, double-scaling it.",
        why_wrong=(
            "completion_ratio() already returns a PERCENTAGE (0-100), as its "
            "docstring and its `* 100.0` state in app/services/task.py. "
            "Multiplying again yields 5000% for a half-done user. The added code "
            "is internally plausible -- only the callee's unit convention, "
            "defined in another file, reveals the error."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        injection_mode="addition",
        tags=("api_contract", "silent_corruption", "cross_file", "additive"),
    ),

    SeededDefect(
        case_id="add_ignored_return_001",
        defect_class="missing_error_handling",
        target_file="app/api.py",
        find='def handle_user_summary(user_id: int) -> dict[str, Any]:',
        replace=(
            'def handle_bulk_email_update(user_id: int, email: str) -> dict[str, Any]:\n'
            '    """Update a user\'s email, reporting success."""\n'
            '    try:\n'
            '        update_email(user_id, email)\n'
            '    except Exception:\n'
            '        return {"status": 500, "error": "update failed"}\n'
            '    return {"status": 200, "data": {"email": email}}\n'
            '\n'
            '\n'
            'def handle_user_summary(user_id: int) -> dict[str, Any]:'
        ),
        description="New handler ignores update_email()'s boolean failure return.",
        why_wrong=(
            "update_email() signals failure by RETURNING False, not by raising "
            "(see app/services/user.py). The try/except therefore never fires, "
            "and an invalid email or unknown user is reported to the client as "
            "status 200 success. The bug is the absence of a return-value check, "
            "which is only knowable from the callee's contract in another file."
        ),
        requires_cross_file_context=True,
        static_analysis_expected=False,
        injection_mode="addition",
        tags=("error_handling", "api_contract", "cross_file", "additive"),
    ),
]

# Full catalogue used by the dataset generator.
FULL_CATALOG: list[SeededDefect] = CATALOG + ADDITIVE_CATALOG


def catalog_summary() -> dict[str, int]:
    """Counts per defect class, for the dataset table in the report."""
    out: dict[str, int] = {}
    for d in FULL_CATALOG:
        out[d.defect_class] = out.get(d.defect_class, 0) + 1
    return out


if __name__ == "__main__":
    for label, cat in (("deletion-mode", CATALOG),
                       ("addition-mode", ADDITIVE_CATALOG),
                       ("FULL", FULL_CATALOG)):
        cf = sum(1 for d in cat if d.requires_cross_file_context)
        st = sum(1 for d in cat if d.static_analysis_expected)
        print(f"{label:14s} n={len(cat):<3} cross-file={cf:<3} static-expected={st}")
