"""Where the student records are kept."""

students = {
    1: {"name": "Aarav", "marks": [80, 90, 70]},
    2: {"name": "Diya",  "marks": [60, 75, 85]},
    3: {"name": "Rohan", "marks": [95, 88, 92]},
}


def find_student(student_id):
    """Find a student by their id.

    Returns the student if found.
    Returns None if there is no student with that id.
    """
    return students.get(student_id)


def all_students():
    """Return every student."""
    return list(students.values())
