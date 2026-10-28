"""Working out student marks."""

from database import find_student


def average_marks(student_id):
    """Return the student's average marks as a PERCENTAGE, from 0 to 100.

    For example, a student with marks 80, 90 and 70 gets 80.0
    Returns 0 if the student does not exist.
    """
    student = find_student(student_id)
    if student is None:
        return 0

    total = sum(student["marks"])
    count = len(student["marks"])
    return total / count


def highest_mark(student_id):
    """Return the student's best single mark, or 0 if they do not exist."""
    student = find_student(student_id)
    if student is None:
        return 0
    return max(student["marks"])


def has_passed(student_id):
    """A student passes if their average is 40 or more."""
    return average_marks(student_id) >= 40
