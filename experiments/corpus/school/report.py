"""Printing report cards."""

from database import find_student
from marks import average_marks, has_passed


def student_name(student_id):
    """Return the student's name, or 'Unknown' if they do not exist."""
    student = find_student(student_id)
    if student is None:
        return "Unknown"
    return student["name"]


def report_card(student_id):
    """Build a one-line report card for a student."""
    student = find_student(student_id)
    if student is None:
        return "No such student"

    name = student["name"]
    average = average_marks(student_id)
    result = "PASS" if has_passed(student_id) else "FAIL"
    return name + " scored " + str(average) + "% and got " + result
