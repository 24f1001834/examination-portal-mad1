"""Scheduled examination availability, shared by all role workflows."""

from datetime import datetime, timedelta, timezone

LOCAL_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def scheduled_phase(exam, now=None):
    """Starts are inclusive; ends are exclusive. Completed examinations stay completed."""
    if exam['status'] == 'Completed':
        return 'Completed'
    if not all(exam[field] for field in ('slot_creation_start', 'slot_creation_end', 'booking_start', 'booking_end')):
        return 'Draft'
    if now is None:
        now = datetime.now(LOCAL_TIMEZONE)
    current = now.astimezone(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    if current < exam['slot_creation_start']:
        return 'Draft'
    if exam['slot_creation_start'] <= current < exam['slot_creation_end']:
        return 'Slot Creation'
    if exam['booking_start'] <= current < exam['booking_end']:
        return 'Booking Open'
    return 'Closed'


def refresh_phases(connection):
    """Refresh stored display statuses whenever a request reaches the server."""
    now = datetime.now(LOCAL_TIMEZONE)
    with connection:
        for exam in connection.execute('SELECT * FROM examinations').fetchall():
            phase = scheduled_phase(exam, now)
            # Booking closing is not completion: require finished, evaluated bookings.
            if phase == 'Closed' and exam['booking_end'] and now.strftime('%Y-%m-%d %H:%M:%S') >= exam['booking_end']:
                unfinished = connection.execute(
                    """SELECT 1 FROM bookings b LEFT JOIN evaluations v ON v.booking_id=b.id
                       WHERE b.examination_id=? AND (b.status='Booked' OR
                       (b.status='Completed' AND v.id IS NULL))""", (exam['id'],),
                ).fetchone()
                if not unfinished:
                    phase = 'Completed'
            if phase != exam['status']:
                connection.execute('UPDATE examinations SET status=? WHERE id=?', (phase, exam['id']))
