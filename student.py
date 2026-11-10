"""Student examinations, bookings, personal history, and direct results."""

import sqlite3
from datetime import datetime

from flask import abort, flash, g, redirect, render_template, request, url_for

from phases import LOCAL_TIMEZONE, scheduled_phase
from results import get_student_result


def local_now():
    return datetime.now(LOCAL_TIMEZONE)


def register_student_routes(app, get_db, role_required):
    student_only = role_required('Student')

    def exam_record(exam_id):
        exam = get_db().execute(
            'SELECT e.*,c.code AS course_code,c.status AS course_status FROM examinations e JOIN courses c ON c.id=e.course_id WHERE e.id=?',
            (exam_id,),
        ).fetchone()
        if exam is None:
            abort(404)
        return exam

    def owned_booking(booking_id):
        booking = get_db().execute('SELECT * FROM bookings WHERE id=? AND student_id=?',
                                   (booking_id, g.user['id'])).fetchone()
        if booking is None:
            abort(404)
        return booking

    def personal_bookings():
        return get_db().execute(
            """SELECT b.*,e.name AS exam_name,c.code AS course_code,s.slot_date,s.start_time,s.end_time,
               u.name AS examiner_name,e.booking_end FROM bookings b
               JOIN examinations e ON e.id=b.examination_id JOIN courses c ON c.id=e.course_id
               JOIN examination_slots s ON s.id=b.slot_id JOIN users u ON u.id=s.examiner_id
               WHERE b.student_id=? ORDER BY s.slot_date DESC,s.start_time DESC,b.id DESC""", (g.user['id'],),
        ).fetchall()

    def update_capacity(slot_id):
        get_db().execute(
            """UPDATE examination_slots SET status=CASE WHEN
               (SELECT count(*) FROM bookings WHERE slot_id=? AND status!='Cancelled')>=maximum_capacity
               THEN 'Full' ELSE 'Available' END WHERE id=? AND status IN ('Available','Full')""", (slot_id, slot_id),
        )

    @app.get('/student/dashboard')
    @student_only
    def student_dashboard():
        db = get_db()
        bookings = personal_bookings()
        now = local_now().strftime('%Y-%m-%d %H:%M:%S')
        upcoming = [b for b in bookings if b['status']=='Booked' and b['slot_date']+' '+b['end_time']>now]
        available = db.execute(
            """SELECT e.*,c.code AS course_code FROM examinations e JOIN courses c ON c.id=e.course_id
               WHERE c.status='Active' AND e.status!='Draft' ORDER BY e.id DESC"""
        ).fetchall()
        available = [e for e in available if scheduled_phase(e, local_now())=='Booking Open']
        result_ids = [b['id'] for b in bookings if get_student_result(db,b['id'],g.user['id']) is not None]
        return render_template('student/dashboard.html', bookings=bookings, upcoming=upcoming,
                               available=available, result_ids=result_ids)

    @app.route('/student/profile', methods=['GET','POST'])
    @student_only
    def student_profile():
        if request.method=='POST':
            name, phone = request.form.get('name','').strip(), request.form.get('phone','').strip()
            if not name or len(name)>100 or len(phone)>30:
                flash('Enter a name (1–100 characters) and optional phone (up to 30).','danger')
            else:
                db=get_db()
                with db:
                    db.execute('UPDATE users SET name=?,phone=? WHERE id=?',(name,phone,g.user['id']))
                flash('Profile updated.','success')
                return redirect(url_for('student_profile'))
        return render_template('student/profile.html')

    @app.get('/student/examinations')
    @student_only
    def student_examinations():
        q=request.args.get('q','').strip()
        course=request.args.get('course','')
        kind=request.args.get('type','')
        if kind not in ('','Viva','Practical','Project Demo','Assessment'):
            abort(400)
        exams=get_db().execute(
            """SELECT e.*,c.code AS course_code FROM examinations e JOIN courses c ON c.id=e.course_id
               WHERE c.status='Active' AND e.status!='Draft'
               AND (e.name LIKE ? OR c.code LIKE ? OR CAST(e.id AS TEXT)=?)
               AND (?='' OR CAST(e.course_id AS TEXT)=?) AND (?='' OR e.exam_type=?) ORDER BY e.id DESC""",
            (f'%{q}%',f'%{q}%',q,course,course,kind,kind),
        ).fetchall()
        courses=get_db().execute("SELECT id,code FROM courses WHERE status='Active' ORDER BY code").fetchall()
        return render_template('student/examinations.html',exams=exams,courses=courses,q=q,course=course,kind=kind)

    @app.get('/student/examinations/<int:exam_id>')
    @student_only
    def student_examination(exam_id):
        exam=exam_record(exam_id)
        if exam['status']=='Draft' or exam['course_status']!='Active':
            abort(404)
        slots=get_db().execute(
            """SELECT s.*,u.name AS examiner_name,
               s.maximum_capacity-(SELECT count(*) FROM bookings b WHERE b.slot_id=s.id AND b.status!='Cancelled') AS seats,
               u.is_active,p.status AS examiner_status FROM examination_slots s
               JOIN users u ON u.id=s.examiner_id JOIN examiner_profiles p ON p.examiner_id=u.id
               WHERE s.examination_id=? ORDER BY s.slot_date,s.start_time""",(exam_id,),
        ).fetchall()
        existing=get_db().execute("SELECT id FROM bookings WHERE student_id=? AND examination_id=? AND status!='Cancelled'",
                                   (g.user['id'],exam_id)).fetchone()
        return render_template('student/examination.html',exam=exam,slots=slots,existing=existing,
                               booking_open=scheduled_phase(exam,local_now())=='Booking Open',
                               now=local_now().strftime('%Y-%m-%d %H:%M:%S'))

    @app.post('/student/slots/<int:slot_id>/book')
    @student_only
    def student_book(slot_id):
        db=get_db()
        exam_id=None
        exam=None
        try:
            with db:
                # Serialize capacity checks and writes, including competing Students.
                db.execute('BEGIN IMMEDIATE')
                slot=db.execute('SELECT * FROM examination_slots WHERE id=?',(slot_id,)).fetchone()
                if slot is None:
                    abort(404)
                exam_id=slot['examination_id']
                exam=exam_record(exam_id)
                if exam['course_status']!='Active' or scheduled_phase(exam,local_now())!='Booking Open':
                    raise ValueError('Student booking is closed for this examination.')
                if slot['status'] not in ('Available','Full') or slot['slot_date']+' '+slot['start_time']<=local_now().strftime('%Y-%m-%d %H:%M:%S'):
                    raise ValueError('This slot is not available for booking.')
                examiner=db.execute(
                    "SELECT 1 FROM users u JOIN examiner_profiles p ON p.examiner_id=u.id WHERE u.id=? AND u.is_active=1 AND p.status='Approved'",
                    (slot['examiner_id'],),
                ).fetchone()
                if examiner is None:
                    raise ValueError('The Examiner is inactive. Choose another slot.')
                if db.execute("SELECT 1 FROM bookings WHERE student_id=? AND examination_id=? AND status!='Cancelled'",
                              (g.user['id'],exam_id)).fetchone():
                    raise ValueError('You already have a booking for this examination.')
                occupied=db.execute("SELECT count(*) FROM bookings WHERE slot_id=? AND status!='Cancelled'",(slot_id,)).fetchone()[0]
                if occupied>=slot['maximum_capacity']:
                    raise ValueError('This slot is full.')
                if db.execute(
                    """SELECT 1 FROM bookings b JOIN examination_slots s ON s.id=b.slot_id
                       WHERE b.student_id=? AND b.status='Booked' AND s.slot_date=? AND s.start_time<? AND s.end_time>?""",
                    (g.user['id'],slot['slot_date'],slot['end_time'],slot['start_time']),
                ).fetchone():
                    raise ValueError('This slot overlaps one of your booked examinations.')
                db.execute('INSERT INTO bookings(student_id,slot_id,examination_id) VALUES(?,?,?)',(g.user['id'],slot_id,exam_id))
                update_capacity(slot_id)
            flash('Slot booked.','success')
        except (ValueError,sqlite3.IntegrityError,sqlite3.OperationalError) as error:
            flash(str(error) if isinstance(error,ValueError) else 'Booking was not saved. Reload and check your bookings before trying again.','danger')
        visible=exam is not None and exam['course_status']=='Active' and exam['status']!='Draft'
        return redirect(url_for('student_examination',exam_id=exam_id) if visible else url_for('student_examinations'))

    @app.post('/student/bookings/<int:booking_id>/cancel')
    @student_only
    def student_cancel(booking_id):
        db=get_db()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                booking=owned_booking(booking_id)
                exam=exam_record(booking['examination_id'])
                if booking['status']!='Booked' or db.execute('SELECT 1 FROM evaluations WHERE booking_id=?',(booking_id,)).fetchone():
                    raise ValueError('Only pending bookings can be cancelled.')
                if not exam['booking_end'] or local_now().strftime('%Y-%m-%d %H:%M:%S')>=exam['booking_end']:
                    raise ValueError('The cancellation deadline has passed.')
                db.execute("UPDATE bookings SET status='Cancelled' WHERE id=?",(booking_id,))
                update_capacity(booking['slot_id'])
            flash('Booking cancelled.','success')
        except (ValueError,sqlite3.OperationalError) as error:
            flash(str(error) if isinstance(error,ValueError) else 'Cancellation was not saved. Reload and try again.','danger')
        return redirect(url_for('student_bookings'))

    @app.get('/student/bookings')
    @student_only
    def student_bookings():
        bookings=personal_bookings()
        result_ids=[b['id'] for b in bookings if get_student_result(get_db(),b['id'],g.user['id']) is not None]
        return render_template('student/bookings.html',bookings=bookings,result_ids=result_ids,
                               now=local_now().strftime('%Y-%m-%d %H:%M:%S'))

    @app.get('/student/bookings/<int:booking_id>/result')
    @student_only
    def student_result(booking_id):
        booking=owned_booking(booking_id)
        result=get_student_result(get_db(),booking_id,g.user['id'])
        return render_template('student/result.html',booking=booking,exam=exam_record(booking['examination_id']),result=result)
