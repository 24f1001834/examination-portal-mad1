"""Examiner profile, assigned examinations, slots, and rubric evaluations."""

import math
import sqlite3
from datetime import datetime, timezone

from flask import abort, flash, g, redirect, render_template, request, url_for

from phases import LOCAL_TIMEZONE, scheduled_phase


def local_now():
    return datetime.now(LOCAL_TIMEZONE)


def register_examiner_routes(app, get_db, role_required):
    examiner_only = role_required('Examiner')

    def assigned_exam(exam_id):
        exam = get_db().execute(
            """SELECT e.*,c.code AS course_code FROM examinations e
               JOIN courses c ON c.id=e.course_id JOIN examiner_assignments a ON a.examination_id=e.id
               WHERE e.id=? AND a.examiner_id=?""", (exam_id, g.user['id']),
        ).fetchone()
        if exam is None:
            abort(404)
        return exam

    def owned_slot(slot_id):
        slot = get_db().execute('SELECT * FROM examination_slots WHERE id=? AND examiner_id=?',
                                (slot_id, g.user['id'])).fetchone()
        if slot is None:
            abort(404)
        assigned_exam(slot['examination_id'])
        return slot

    def rubrics_for(exam):
        rubrics = get_db().execute('SELECT * FROM examination_rubrics WHERE examination_id=? ORDER BY id',
                                   (exam['id'],)).fetchall()
        if not rubrics or not math.isclose(sum(r['maximum_marks'] for r in rubrics), exam['maximum_marks'], abs_tol=0.001) or not math.isclose(sum(r['weightage'] for r in rubrics), 100, abs_tol=0.001):
            raise ValueError('Admin must complete the examination rubrics first.')
        return rubrics

    def can_edit(slot, exam):
        now = local_now().strftime('%Y-%m-%d %H:%M:%S')
        if not exam['booking_start'] or now >= exam['booking_start']:
            raise ValueError('Slots can be edited or removed only before Student booking starts.')
        if exam['status'] == 'Completed' or slot['status'] in ('Cancelled', 'Completed') or slot['slot_date'] + ' ' + slot['start_time'] <= now:
            raise ValueError('Only upcoming, unfinished slots can be changed.')
        if get_db().execute(
            "SELECT 1 FROM bookings WHERE slot_id=? AND status!='Cancelled'", (slot['id'],),
        ).fetchone():
            raise ValueError('This slot has bookings. Contact Admin for changes.')

    @app.get('/examiner/dashboard')
    @examiner_only
    def examiner_dashboard():
        db = get_db()
        exams = db.execute(
            """SELECT e.*,c.code AS course_code FROM examinations e JOIN courses c ON c.id=e.course_id
               JOIN examiner_assignments a ON a.examination_id=e.id WHERE a.examiner_id=? ORDER BY e.id DESC""",
            (g.user['id'],),
        ).fetchall()
        slots = db.execute(
            """SELECT s.*,e.name AS exam_name,c.code AS course_code,
               (SELECT count(*) FROM bookings b WHERE b.slot_id=s.id AND b.status!='Cancelled') AS booked
               FROM examination_slots s JOIN examinations e ON e.id=s.examination_id
               JOIN courses c ON c.id=e.course_id WHERE s.examiner_id=? ORDER BY s.slot_date,s.start_time""",
            (g.user['id'],),
        ).fetchall()
        pending = db.execute(
            """SELECT count(*) FROM bookings b JOIN examination_slots s ON s.id=b.slot_id
               LEFT JOIN evaluations v ON v.booking_id=b.id
               WHERE s.examiner_id=? AND b.status='Booked' AND v.id IS NULL AND s.status!='Cancelled'""",
            (g.user['id'],),
        ).fetchone()[0]
        return render_template('examiner/dashboard.html', exams=exams, slots=slots,
                               booked=sum(s['booked'] for s in slots), pending=pending)

    @app.route('/examiner/profile', methods=['GET', 'POST'])
    @examiner_only
    def examiner_profile():
        db = get_db()
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            department = request.form.get('department', '').strip()
            if not name or len(name) > 100 or len(phone) > 30 or not department or len(department) > 100:
                flash('Enter a name (1–100 characters), department (1–100), and optional phone (up to 30).', 'danger')
            else:
                with db:
                    db.execute('UPDATE users SET name=?,phone=? WHERE id=?', (name, phone, g.user['id']))
                    db.execute('UPDATE examiner_profiles SET department=? WHERE examiner_id=?', (department, g.user['id']))
                flash('Profile updated.', 'success')
                return redirect(url_for('examiner_profile'))
        profile = db.execute('SELECT department FROM examiner_profiles WHERE examiner_id=?', (g.user['id'],)).fetchone()
        return render_template('examiner/profile.html', profile=profile)

    @app.route('/examiner/examinations/<int:exam_id>/slots/new', methods=['GET', 'POST'])
    @app.route('/examiner/slots/<int:slot_id>/edit', methods=['GET', 'POST'])
    @examiner_only
    def examiner_slot_form(exam_id=None, slot_id=None):
        db = get_db()
        slot = owned_slot(slot_id) if slot_id is not None else None
        exam = assigned_exam(slot['examination_id'] if slot else exam_id)
        if request.method == 'POST':
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    # Re-read ownership and timeline after taking the write lock.
                    slot = owned_slot(slot_id) if slot_id is not None else None
                    exam = assigned_exam(exam['id'])
                    if slot:
                        can_edit(slot, exam)
                    elif scheduled_phase(exam, local_now()) != 'Slot Creation':
                        raise ValueError('Slots can be created only during the slot creation period.')
                    rubrics_for(exam)
                    try:
                        date = datetime.strptime(request.form.get('slot_date', ''), '%Y-%m-%d').strftime('%Y-%m-%d')
                        start = datetime.strptime(request.form.get('start_time', ''), '%H:%M').strftime('%H:%M:%S')
                        end = datetime.strptime(request.form.get('end_time', ''), '%H:%M').strftime('%H:%M:%S')
                        capacity = int(request.form.get('maximum_capacity', ''))
                    except ValueError:
                        raise ValueError('Enter a valid date, times, and whole-number capacity.')
                    begins = datetime.strptime(date + ' ' + start, '%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
                    ends = datetime.strptime(date + ' ' + end, '%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
                    if capacity < 1:
                        raise ValueError('Capacity must be a positive whole number.')
                    if begins <= local_now() or not exam['booking_end'] or date + ' ' + start <= exam['booking_end']:
                        raise ValueError('The slot must start in the future, after Student booking ends.')
                    if (ends - begins).total_seconds() < exam['duration_minutes'] * 60:
                        raise ValueError('Slot length must be at least the examination duration.')
                    if db.execute(
                        "SELECT 1 FROM examination_slots WHERE examiner_id=? AND id!=? AND slot_date=? AND status!='Cancelled' AND start_time<? AND end_time>?",
                        (g.user['id'], slot_id or -1, date, end, start),
                    ).fetchone():
                        raise ValueError('This slot overlaps one of your existing slots.')
                    values = (date, start, end, capacity)
                    if slot:
                        db.execute('UPDATE examination_slots SET slot_date=?,start_time=?,end_time=?,maximum_capacity=? WHERE id=?', (*values, slot_id))
                    else:
                        db.execute('INSERT INTO examination_slots(slot_date,start_time,end_time,maximum_capacity,examination_id,examiner_id) VALUES(?,?,?,?,?,?)',
                                   (*values, exam['id'], g.user['id']))
                flash('Slot saved.', 'success')
                return redirect(url_for('examiner_dashboard'))
            except (ValueError, sqlite3.IntegrityError, OverflowError) as error:
                flash(str(error) if not isinstance(error, sqlite3.IntegrityError) else 'Slot could not be saved. Reload and try again.', 'danger')
        return render_template('examiner/slot_form.html', exam=exam, slot=slot)

    @app.post('/examiner/slots/<int:slot_id>/delete')
    @examiner_only
    def examiner_delete_slot(slot_id):
        db = get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                slot = owned_slot(slot_id)
                can_edit(slot, assigned_exam(slot['examination_id']))
                # Preserve any cancelled bookings and Admin history referencing this slot.
                referenced = db.execute('SELECT 1 FROM bookings WHERE slot_id=?', (slot_id,)).fetchone() or db.execute(
                    "SELECT 1 FROM admin_changes WHERE slot_id=? OR (change_type='Reschedule' AND (old_value=? OR new_value=?))",
                    (slot_id, slot_id, slot_id),
                ).fetchone()
                if referenced:
                    db.execute("UPDATE examination_slots SET status='Cancelled' WHERE id=?", (slot_id,))
                else:
                    db.execute('DELETE FROM examination_slots WHERE id=?', (slot_id,))
            flash('Slot removed.', 'success')
        except ValueError as error:
            flash(str(error), 'danger')
        return redirect(url_for('examiner_dashboard'))

    @app.get('/examiner/slots/<int:slot_id>/students')
    @examiner_only
    def examiner_students(slot_id):
        slot = owned_slot(slot_id)
        bookings = get_db().execute(
            """SELECT b.*,u.name,u.email,v.id AS evaluation_id FROM bookings b JOIN users u ON u.id=b.student_id
               LEFT JOIN evaluations v ON v.booking_id=b.id WHERE b.slot_id=? ORDER BY b.id""", (slot_id,),
        ).fetchall()
        return render_template('examiner/students.html', slot=slot, exam=assigned_exam(slot['examination_id']), bookings=bookings)

    def owned_booking(booking_id):
        booking = get_db().execute('SELECT b.*,u.name AS student_name FROM bookings b JOIN users u ON u.id=b.student_id WHERE b.id=?',
                                   (booking_id,)).fetchone()
        if booking is None:
            abort(404)
        slot = owned_slot(booking['slot_id'])
        return booking, slot, assigned_exam(slot['examination_id'])

    @app.route('/examiner/bookings/<int:booking_id>/evaluate', methods=['GET', 'POST'])
    @examiner_only
    def examiner_evaluate(booking_id):
        db = get_db()
        booking, slot, exam = owned_booking(booking_id)
        if request.method == 'POST':
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    booking, slot, exam = owned_booking(booking_id)
                    if booking['status'] != 'Booked' or slot['status'] in ('Cancelled', 'Completed') or exam['status'] == 'Completed':
                        raise ValueError('Only pending bookings in unfinished slots can be evaluated.')
                    if db.execute('SELECT 1 FROM evaluations WHERE booking_id=?', (booking_id,)).fetchone():
                        raise ValueError('This evaluation has already been submitted.')
                    rubrics = rubrics_for(exam)
                    marks = []
                    for rubric in rubrics:
                        try:
                            score = float(request.form.get(f'marks_{rubric["id"]}', ''))
                        except ValueError:
                            raise ValueError('Enter marks for every criterion.')
                        if not math.isfinite(score) or not 0 <= score <= rubric['maximum_marks']:
                            raise ValueError(f'Marks for {rubric["criterion_name"]} must be between 0 and {rubric["maximum_marks"]}.')
                        marks.append((rubric['id'], score))
                    remarks = request.form.get('remarks', '').strip()
                    if len(remarks) > 2000:
                        raise ValueError('Remarks must be at most 2000 characters.')
                    cursor = db.execute('INSERT INTO evaluations(booking_id,examiner_id,remarks) VALUES(?,?,?)',
                                        (booking_id, g.user['id'], remarks))
                    db.executemany('INSERT INTO evaluation_marks(evaluation_id,rubric_id,awarded_marks) VALUES(?,?,?)',
                                   [(cursor.lastrowid, rid, score) for rid, score in marks])
                    db.execute("UPDATE bookings SET status='Completed',completed_at=? WHERE id=?",
                               (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), booking_id))
                    if not db.execute("SELECT 1 FROM bookings WHERE slot_id=? AND status='Booked'", (slot['id'],)).fetchone():
                        db.execute("UPDATE examination_slots SET status='Completed' WHERE id=?", (slot['id'],))
                flash('Evaluation submitted.', 'success')
                return redirect(url_for('examiner_students', slot_id=slot['id']))
            except (ValueError, sqlite3.IntegrityError) as error:
                flash(str(error) if isinstance(error, ValueError) else 'Evaluation was not saved. Reload to check whether it was already submitted.', 'danger')
        evaluation = db.execute('SELECT * FROM evaluations WHERE booking_id=?', (booking_id,)).fetchone()
        rubrics = db.execute('SELECT * FROM examination_rubrics WHERE examination_id=? ORDER BY id', (exam['id'],)).fetchall()
        scores = {m['rubric_id']: m['awarded_marks'] for m in db.execute(
            'SELECT m.* FROM evaluation_marks m JOIN evaluations v ON v.id=m.evaluation_id WHERE v.booking_id=?', (booking_id,),
        )}
        return render_template('examiner/evaluate.html', booking=booking, slot=slot, exam=exam,
                               rubrics=rubrics, evaluation=evaluation, scores=scores)
