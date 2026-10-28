"""A tiny Student Records microservice.

Run it:
    uvicorn service:app --port 9000

Then visit:
    http://localhost:9000/student/1        a student that exists
    http://localhost:9000/student/99       a student that does NOT exist
    http://localhost:9000/report/1         a full report card

This exists so a demonstration can show the SERVICE BREAKING in a browser,
rather than only discussing a diff. A 500 error on screen is more convincing
than an argument about code.
"""

from fastapi import FastAPI

from database import find_student
from marks import average_marks, has_passed
from report import report_card, student_name

app = FastAPI(title="Student Records")


@app.get("/")
def index():
    return {
        "service": "Student Records",
        "try": ["/student/1", "/student/99", "/report/1", "/average/1"],
    }


@app.get("/student/{student_id}")
def get_student(student_id: int):
    """Return a student's record, or a clean 404-style message."""
    student = find_student(student_id)
    if student is None:
        return {"status": 404, "error": "no such student"}
    return {"status": 200, "data": student}


@app.get("/average/{student_id}")
def get_average(student_id: int):
    """Return the student's average as a percentage."""
    return {"status": 200, "average_percent": average_marks(student_id)}


@app.get("/report/{student_id}")
def get_report(student_id: int):
    """Return a one-line report card."""
    return {
        "status": 200,
        "name": student_name(student_id),
        "report": report_card(student_id),
        "passed": has_passed(student_id),
    }
