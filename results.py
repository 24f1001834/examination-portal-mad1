"""Student result eligibility, independent of examination-wide status."""

import math


def get_student_result(connection, booking_id, student_id):
    """Return an owned, completed evaluation, or None while it is pending."""
    result = connection.execute(
        """SELECT v.*,b.examination_id,e.maximum_marks
           FROM bookings b JOIN evaluations v ON v.booking_id=b.id
           JOIN examinations e ON e.id=b.examination_id
           WHERE b.id=? AND b.student_id=? AND b.status='Completed'""",
        (booking_id, student_id),
    ).fetchone()
    if result is None:
        return None
    marks = connection.execute(
        """SELECT r.id,r.criterion_name,r.maximum_marks,r.weightage,m.awarded_marks
           FROM examination_rubrics r LEFT JOIN evaluation_marks m
           ON m.rubric_id=r.id AND m.evaluation_id=?
           WHERE r.examination_id=? ORDER BY r.id""",
        (result['id'], result['examination_id']),
    ).fetchall()
    if not marks or any(
        m['awarded_marks'] is None or not math.isfinite(m['awarded_marks'])
        or not 0 <= m['awarded_marks'] <= m['maximum_marks'] for m in marks
    ):
        return None
    if not math.isclose(sum(m['maximum_marks'] for m in marks), result['maximum_marks'], abs_tol=0.001):
        return None
    if not math.isclose(sum(m['weightage'] for m in marks), 100, abs_tol=0.001):
        return None
    return {
        'evaluation': result,
        'marks': marks,
        'total_marks': sum(m['awarded_marks'] for m in marks),
    }
