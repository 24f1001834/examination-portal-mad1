"""Admin management pages. Authentication and CSRF checks live in app.py."""

import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash


def register_admin_routes(app, get_db, role_required):
    admin_only = role_required("Admin")

    def local_now():
        return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")

    def row(table, record_id):
        # Table names below are fixed by our routes, never taken from form input.
        record = get_db().execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if record is None:
            abort(404)
        return record

    def text(name, maximum=200, required=True):
        value = request.form.get(name, "").strip()
        if (required and not value) or len(value) > maximum:
            raise ValueError(f"{name.replace('_', ' ').capitalize()}: enter {'1' if required else '0'} to {maximum} characters.")
        return value

    def number(name, integer=False):
        try:
            value = int(request.form.get(name, "")) if integer else float(request.form.get(name, ""))
        except ValueError:
            raise ValueError(f"{name.replace('_', ' ').capitalize()} must be a positive number.")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name.replace('_', ' ').capitalize()} must be a positive number.")
        return value

    def choice(name, options):
        value = request.form.get(name, "")
        if value not in options:
            raise ValueError(f"Choose a valid {name.replace('_', ' ')}.")
        return value

    def fail(error):
        if isinstance(error, sqlite3.IntegrityError):
            flash("Cannot save: a duplicate value or a referenced historical record prevents this change.", "danger")
        else:
            flash(str(error), "danger")

    def approved_examiner(examiner_id):
        examiner = get_db().execute(
            """SELECT u.id FROM users u JOIN examiner_profiles p ON p.examiner_id=u.id
               WHERE u.id=? AND u.role='Examiner' AND u.is_active=1 AND p.status='Approved'""",
            (examiner_id,),
        ).fetchone()
        if examiner is None:
            raise ValueError("Choose an active, approved Examiner.")

    def eligible_examiners():
        return get_db().execute(
            """SELECT u.id,u.name FROM users u JOIN examiner_profiles p ON p.examiner_id=u.id
               WHERE u.role='Examiner' AND u.is_active=1 AND p.status='Approved' ORDER BY u.name"""
        ).fetchall()

    def rubric_totals(exam_id):
        return get_db().execute(
            "SELECT coalesce(sum(maximum_marks),0),coalesce(sum(weightage),0) FROM examination_rubrics WHERE examination_id=?",
            (exam_id,),
        ).fetchone()

    @app.route("/admin/dashboard")
    @admin_only
    def admin_dashboard():
        db = get_db()
        counts = {}
        for label, table in [("Courses", "courses"), ("Examinations", "examinations"),
                             ("Slots", "examination_slots"), ("Bookings", "bookings")]:
            counts[label] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for role in ("Examiner", "Student"):
            counts[role + "s"] = db.execute("SELECT count(*) FROM users WHERE role=?", (role,)).fetchone()[0]
        return render_template("admin/dashboard.html", counts=counts)

    @app.route("/admin/courses", methods=["GET", "POST"])
    @app.route("/admin/courses/new", methods=["GET", "POST"], endpoint="admin_new_course")
    @app.route("/admin/courses/<int:course_id>/edit", methods=["GET", "POST"])
    @admin_only
    def admin_courses(course_id=None):
        db = get_db()
        course = row("courses", course_id) if course_id is not None else None
        if request.method == "POST":
            try:
                values = (text("code", 30), text("name", 150), text("description", 2000, False),
                          choice("status", ("Active", "Inactive")))
                with db:
                    if course:
                        db.execute("UPDATE courses SET code=?,name=?,description=?,status=? WHERE id=?", (*values, course_id))
                    else:
                        db.execute("INSERT INTO courses(code,name,description,status) VALUES(?,?,?,?)", values)
                flash("Course saved.", "success")
                return redirect(url_for("admin_courses"))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        return render_template("admin/courses.html", show_form=course is not None or request.path.endswith("/new") or request.method == "POST", course=course, courses=db.execute("SELECT * FROM courses ORDER BY id DESC").fetchall())

    @app.post("/admin/courses/<int:course_id>/delete")
    @admin_only
    def admin_delete_course(course_id):
        row("courses", course_id)
        db = get_db()
        try:
            with db:
                db.execute("DELETE FROM courses WHERE id=?", (course_id,))
            flash("Course removed.", "success")
        except sqlite3.IntegrityError as error:
            fail(error)
        return redirect(url_for("admin_courses"))

    @app.route("/admin/examinations", methods=["GET", "POST"])
    @app.route("/admin/examinations/new", methods=["GET", "POST"], endpoint="admin_new_examination")
    @app.route("/admin/examinations/<int:exam_id>/edit", methods=["GET", "POST"])
    @admin_only
    def admin_examinations(exam_id=None):
        db = get_db()
        exam = row("examinations", exam_id) if exam_id is not None else None
        if request.method == "POST":
            try:
                course_id = number("course_id", True)
                row("courses", course_id)
                name = text("name", 150)
                exam_type = choice("exam_type", ("Viva", "Practical", "Project Demo", "Assessment"))
                duration = number("duration_minutes", True)
                maximum = number("maximum_marks")
                fields = ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end")
                dates = [exam[field] if exam else None for field in fields]
                # Avoid changing the meaning or timing of existing slots/results.
                if exam and db.execute("SELECT 1 FROM examination_slots WHERE examination_id=?", (exam_id,)).fetchone():
                    original = [exam[field] for field in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end")]
                    if (course_id != exam["course_id"] or duration != exam["duration_minutes"] or
                        maximum != exam["maximum_marks"] or exam_type != exam["exam_type"] or
                        original != dates):
                        raise ValueError("Slots already exist. Only the examination name can be edited; preserve its course, type, marks, duration, and timelines.")
                existing_marks = rubric_totals(exam_id)[0] if exam else 0
                if maximum + 0.001 < existing_marks:
                    raise ValueError("Maximum marks cannot be below the existing rubric total.")
                values = (course_id, name, exam_type, duration, maximum, *dates)
                with db:
                    if exam:
                        db.execute("""UPDATE examinations SET course_id=?,name=?,exam_type=?,duration_minutes=?,maximum_marks=?,
                            slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=? WHERE id=?""", (*values, exam_id))
                    else:
                        db.execute("""INSERT INTO examinations(course_id,name,exam_type,duration_minutes,maximum_marks,
                            slot_creation_start,slot_creation_end,booking_start,booking_end) VALUES(?,?,?,?,?,?,?,?,?)""", values)
                flash("Examination saved.", "success")
                return redirect(url_for("admin_examinations"))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        query = request.args.get("q", "").strip()
        exams = db.execute(
            """SELECT e.*,c.code FROM examinations e JOIN courses c ON c.id=e.course_id
               WHERE e.name LIKE ? OR CAST(e.id AS TEXT)=? ORDER BY e.id DESC""", (f"%{query}%", query),
        ).fetchall()
        return render_template("admin/examinations.html", show_form=exam is not None or request.path.endswith("/new") or request.method == "POST", exam=exam, exams=exams, courses=db.execute("SELECT * FROM courses ORDER BY code").fetchall(), q=query)

    @app.post("/admin/examinations/<int:exam_id>/delete")
    @admin_only
    def admin_delete_examination(exam_id):
        row("examinations", exam_id)
        db = get_db()
        try:
            with db:
                db.execute("DELETE FROM examination_rubrics WHERE examination_id=?", (exam_id,))
                db.execute("DELETE FROM examiner_assignments WHERE examination_id=?", (exam_id,))
                db.execute("DELETE FROM examinations WHERE id=?", (exam_id,))
            flash("Examination removed.", "success")
        except sqlite3.IntegrityError as error:
            fail(error)
        return redirect(url_for("admin_examinations"))

    @app.route("/admin/examinations/<int:exam_id>", methods=["GET", "POST"])
    @admin_only
    def admin_examination(exam_id):
        db = get_db()
        exam = row("examinations", exam_id)
        if request.method == "POST":
            try:
                with db:
                    action = choice("action", ("assign", "unassign", "rubric", "delete_rubric", "timeline"))
                    if exam["status"] == "Completed":
                        raise ValueError("Completed examination configuration is protected.")
                    if action == "timeline":
                        dates = []
                        for field in ("slot_creation_start", "slot_creation_end", "booking_start", "booking_end"):
                            try:
                                dates.append(datetime.strptime(request.form.get(field, ""), "%Y-%m-%dT%H:%M"))
                            except ValueError:
                                raise ValueError("Enter all four valid timeline dates and times.")
                        if not dates[0] < dates[1] <= dates[2] < dates[3]:
                            raise ValueError("Timelines must follow: slot creation start < end <= booking start < end.")
                        db.execute("UPDATE examinations SET slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=? WHERE id=?",
                                   (*[d.strftime("%Y-%m-%d %H:%M:%S") for d in dates], exam_id))
                    elif action in ("assign", "unassign"):
                        examiner_id = number("examiner_id", True)
                        if action == "assign":
                            approved_examiner(examiner_id)
                            db.execute("INSERT INTO examiner_assignments VALUES(?,?)", (exam_id, examiner_id))
                        else:
                            db.execute("DELETE FROM examiner_assignments WHERE examination_id=? AND examiner_id=?", (exam_id, examiner_id))
                    elif action in ("rubric", "delete_rubric"):
                        if db.execute("SELECT 1 FROM examination_slots WHERE examination_id=?", (exam_id,)).fetchone():
                            raise ValueError("Rubrics can change only before any slots exist.")
                        rubric_id = request.form.get("rubric_id", "")
                        if rubric_id:
                            rubric = row("examination_rubrics", rubric_id)
                            if rubric["examination_id"] != exam_id:
                                abort(404)
                        if action == "delete_rubric":
                            db.execute("DELETE FROM examination_rubrics WHERE id=? AND examination_id=?", (rubric_id, exam_id))
                        else:
                            name = text("criterion_name", 150)
                            marks, weight = number("maximum_marks"), number("weightage")
                            totals = db.execute("SELECT coalesce(sum(maximum_marks),0),coalesce(sum(weightage),0) FROM examination_rubrics WHERE examination_id=? AND id!=?", (exam_id, rubric_id or -1)).fetchone()
                            if totals[0] + marks > exam["maximum_marks"] + 0.001 or totals[1] + weight > 100.001:
                                raise ValueError("Rubric totals cannot exceed examination maximum marks or 100% weightage.")
                            values = (name, marks, weight, text("description", 2000, False))
                            if rubric_id:
                                db.execute("UPDATE examination_rubrics SET criterion_name=?,maximum_marks=?,weightage=?,description=? WHERE id=?", (*values, rubric_id))
                            else:
                                db.execute("INSERT INTO examination_rubrics(criterion_name,maximum_marks,weightage,description,examination_id) VALUES(?,?,?,?,?)", (*values, exam_id))
                flash("Examination updated.", "success")
                return redirect(url_for("admin_examination", exam_id=exam_id, section=request.args.get("section", "")))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        section = request.args.get("section", "")
        if section not in ("", "examiners", "rubrics", "timeline", "slots"):
            abort(404)
        return render_template("admin/examination.html", section=section, exam=row("examinations", exam_id),
            course=row("courses", exam["course_id"]),
            slots=db.execute("SELECT s.*,u.name AS examiner_name FROM examination_slots s JOIN users u ON u.id=s.examiner_id WHERE s.examination_id=? ORDER BY s.slot_date,s.start_time", (exam_id,)).fetchall(),
            rubrics=db.execute("SELECT * FROM examination_rubrics WHERE examination_id=? ORDER BY id", (exam_id,)).fetchall(),
            assignments=db.execute("SELECT u.id,u.name,p.status FROM examiner_assignments a JOIN users u ON u.id=a.examiner_id JOIN examiner_profiles p ON p.examiner_id=u.id WHERE a.examination_id=?", (exam_id,)).fetchall(),
            examiners=eligible_examiners(), totals=rubric_totals(exam_id))

    @app.route("/admin/users", methods=["GET", "POST"])
    @app.route("/admin/examiners", methods=["GET", "POST"])
    @app.route("/admin/examiners/new", methods=["GET", "POST"], endpoint="admin_new_examiner")
    @app.get("/admin/students")
    @admin_only
    def admin_users():
        db = get_db()
        if request.method == "POST":
            try:
                with db:
                    action = choice("action", ("add", "status"))
                    if action == "add":
                        name, department = text("name", 100), text("department", 100)
                        email, phone = text("email", 254).lower(), text("phone", 30, False)
                        password = request.form.get("password", "")
                        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                            raise ValueError("Enter a valid email address.")
                        if not 8 <= len(password) <= 128:
                            raise ValueError("Password must be 8 to 128 characters.")
                        cursor = db.execute("INSERT INTO users(email,password_hash,name,role,phone) VALUES(?,?,?,'Examiner',?)", (email, generate_password_hash(password), name, phone))
                        db.execute("INSERT INTO examiner_profiles VALUES(?,?,'Deactivated')", (cursor.lastrowid, department))
                        db.execute("UPDATE users SET is_active=0 WHERE id=?", (cursor.lastrowid,))
                    else:
                        user_id = number("user_id", True)
                        user = row("users", user_id)
                        if user["role"] != "Examiner":
                            raise ValueError("Approval controls apply only to Examiners.")
                        status = choice("status", ("Approved", "Deactivated"))
                        db.execute("UPDATE examiner_profiles SET status=? WHERE examiner_id=?", (status, user_id))
                        db.execute("UPDATE users SET is_active=? WHERE id=?", (int(status == "Approved"), user_id))
                flash("Examiner saved.", "success")
                return redirect("/admin/examiners")
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        query, role = request.args.get("q", "").strip(), ("Student" if request.path.endswith("/students") else request.args.get("role", "Examiner"))
        if role not in ("", "Student", "Examiner"):
            abort(400)
        users = db.execute(
            """SELECT u.id,u.name,u.email,u.phone,u.role,u.is_active,u.created_at,p.department,p.status FROM users u
               LEFT JOIN examiner_profiles p ON p.examiner_id=u.id WHERE u.role IN ('Student','Examiner')
               AND (?='' OR u.role=?) AND (u.name LIKE ? OR CAST(u.id AS TEXT)=?) ORDER BY u.id DESC""",
            (role, role, f"%{query}%", query),
        ).fetchall()
        return render_template("admin/users.html", users=users, q=query, role=role,
                               show_form=request.path.endswith("/new") or (request.method == "POST" and request.form.get("action") == "add"))

    @app.route("/admin/slots/<int:slot_id>/edit", methods=["GET", "POST"])
    @admin_only
    def admin_edit_slot(slot_id):
        db = get_db()
        slot = row("examination_slots", slot_id)
        if request.method == "POST":
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    slot = row("examination_slots", slot_id)
                    exam = row("examinations", slot["examination_id"])
                    if exam["status"] == "Completed" or slot["status"] in ("Completed", "Cancelled") or db.execute(
                        "SELECT 1 FROM bookings b LEFT JOIN evaluations v ON v.booking_id=b.id WHERE b.slot_id=? AND (b.status='Completed' OR v.id IS NOT NULL)", (slot_id,),
                    ).fetchone():
                        raise ValueError("Completed, cancelled, or evaluated slots cannot be edited.")
                    try:
                        date = datetime.strptime(request.form.get("slot_date", ""), "%Y-%m-%d").strftime("%Y-%m-%d")
                        start = datetime.strptime(request.form.get("start_time", ""), "%H:%M").strftime("%H:%M:%S")
                        end = datetime.strptime(request.form.get("end_time", ""), "%H:%M").strftime("%H:%M:%S")
                    except ValueError:
                        raise ValueError("Enter a valid date and start/end time.")
                    if start >= end or date + " " + start <= local_now() or slot["slot_date"] + " " + slot["start_time"] <= local_now():
                        raise ValueError("Edit only upcoming slots; end time must follow start time.")
                    capacity = number("maximum_capacity", True)
                    occupied = db.execute("SELECT count(*) FROM bookings WHERE slot_id=? AND status!='Cancelled'", (slot_id,)).fetchone()[0]
                    if capacity < occupied:
                        raise ValueError("Capacity cannot be below existing bookings.")
                    if db.execute("SELECT 1 FROM examination_slots WHERE id!=? AND examiner_id=? AND slot_date=? AND status!='Cancelled' AND start_time<? AND end_time>?",
                                  (slot_id, slot["examiner_id"], date, end, start)).fetchone():
                        raise ValueError("Examiner has an overlapping slot.")
                    if db.execute("""SELECT 1 FROM bookings mine JOIN bookings other ON other.student_id=mine.student_id
                        JOIN examination_slots s ON s.id=other.slot_id WHERE mine.slot_id=? AND mine.status='Booked'
                        AND other.slot_id!=? AND other.status='Booked' AND s.slot_date=? AND s.start_time<? AND s.end_time>?""",
                                  (slot_id, slot_id, date, end, start)).fetchone():
                        raise ValueError("A booked Student has an overlapping examination.")
                    db.execute("UPDATE examination_slots SET slot_date=?,start_time=?,end_time=?,maximum_capacity=?,status=? WHERE id=?",
                               (date, start, end, capacity, "Full" if occupied >= capacity else "Available", slot_id))
                flash("Slot updated. Existing bookings were preserved.", "success")
                return redirect(url_for("admin_examination", exam_id=slot["examination_id"], section="slots"))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        return render_template("admin/slot_form.html", slot=slot)

    @app.get("/admin/slots")
    @admin_only
    def admin_slots():
        slots = get_db().execute(
            """SELECT s.*,e.name AS exam_name,u.name AS examiner_name,
               (SELECT count(*) FROM bookings b WHERE b.slot_id=s.id AND b.status!='Cancelled') AS occupied
               FROM examination_slots s JOIN examinations e ON e.id=s.examination_id
               JOIN users u ON u.id=s.examiner_id ORDER BY s.slot_date DESC,s.start_time"""
        ).fetchall()
        return render_template("admin/slots.html", slots=slots)

    @app.route("/admin/slots/<int:slot_id>/reassign", methods=["GET", "POST"])
    @admin_only
    def admin_reassign(slot_id):
        db = get_db()
        slot = row("examination_slots", slot_id)
        if request.method == "POST":
            try:
                with db:
                    # Take the write lock before reading capacity/ownership state.
                    db.execute("BEGIN IMMEDIATE")
                    slot = row("examination_slots", slot_id)
                    examiner_id, reason = number("examiner_id", True), text("reason", 1000)
                    approved_examiner(examiner_id)
                    if examiner_id == slot["examiner_id"]:
                        raise ValueError("Choose a different Examiner.")
                    exam = row("examinations", slot["examination_id"])
                    if slot["slot_date"] + " " + slot["start_time"] <= local_now():
                        raise ValueError("Only upcoming slots can be reassigned.")
                    if slot["status"] in ("Cancelled", "Completed") or exam["status"] == "Completed" or db.execute(
                        "SELECT 1 FROM bookings b LEFT JOIN evaluations v ON v.booking_id=b.id WHERE b.slot_id=? AND (b.status='Completed' OR v.id IS NOT NULL)", (slot_id,),
                    ).fetchone():
                        raise ValueError("Completed, cancelled, or evaluated slots cannot be reassigned.")
                    if db.execute(
                        "SELECT 1 FROM examination_slots WHERE examiner_id=? AND slot_date=? AND id!=? AND status!='Cancelled' AND start_time<? AND end_time>?",
                        (examiner_id, slot["slot_date"], slot_id, slot["end_time"], slot["start_time"]),
                    ).fetchone():
                        raise ValueError("This Examiner has an overlapping slot.")
                    db.execute("INSERT OR IGNORE INTO examiner_assignments VALUES(?,?)", (slot["examination_id"], examiner_id))
                    db.execute("UPDATE examination_slots SET examiner_id=? WHERE id=?", (examiner_id, slot_id))
                    db.execute("INSERT INTO admin_changes(admin_id,slot_id,change_type,old_value,new_value,reason) VALUES(?,?,'Reassign',?,?,?)",
                               (g.user["id"], slot_id, slot["examiner_id"], examiner_id, reason))
                flash("Examiner reassigned. Existing bookings were preserved.", "success")
                return redirect(url_for("admin_slots"))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        return render_template("admin/reassign.html", slot=slot, examiners=eligible_examiners())

    @app.get("/admin/bookings")
    @admin_only
    def admin_bookings():
        query = request.args.get("q", "").strip()
        bookings = get_db().execute(
            """SELECT b.*,u.name AS student_name,e.name AS exam_name,s.slot_date,s.start_time,
               x.name AS examiner_name FROM bookings b JOIN users u ON u.id=b.student_id
               JOIN examinations e ON e.id=b.examination_id JOIN examination_slots s ON s.id=b.slot_id
               JOIN users x ON x.id=s.examiner_id WHERE u.name LIKE ? OR e.name LIKE ?
               OR CAST(b.id AS TEXT)=? OR CAST(u.id AS TEXT)=? ORDER BY b.id DESC""",
            (f"%{query}%", f"%{query}%", query, query),
        ).fetchall()
        return render_template("admin/bookings.html", bookings=bookings, q=query)

    @app.route("/admin/bookings/<int:booking_id>/reschedule", methods=["GET", "POST"])
    @admin_only
    def admin_reschedule(booking_id):
        db = get_db()
        booking = row("bookings", booking_id)
        if request.method == "POST":
            try:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    booking = row("bookings", booking_id)
                    target_id, reason = number("slot_id", True), text("reason", 1000)
                    target = row("examination_slots", target_id)
                    exam = row("examinations", booking["examination_id"])
                    if booking["status"] != "Booked" or exam["status"] == "Completed" or db.execute("SELECT 1 FROM evaluations WHERE booking_id=?", (booking_id,)).fetchone():
                        raise ValueError("Only unevaluated Booked records in unfinished examinations can be rescheduled.")
                    if target_id == booking["slot_id"] or target["examination_id"] != booking["examination_id"]:
                        raise ValueError("Choose a different slot in the same examination.")
                    approved_examiner(target["examiner_id"])
                    if target["status"] not in ("Available", "Full") or target["slot_date"] + " " + target["start_time"] <= local_now():
                        raise ValueError("Choose an upcoming available slot.")
                    occupied = db.execute("SELECT count(*) FROM bookings WHERE slot_id=? AND status!='Cancelled'", (target_id,)).fetchone()[0]
                    if occupied >= target["maximum_capacity"]:
                        raise ValueError("The target slot is full.")
                    if db.execute(
                        """SELECT 1 FROM bookings b JOIN examination_slots s ON s.id=b.slot_id
                           WHERE b.student_id=? AND b.id!=? AND b.status='Booked' AND s.slot_date=?
                           AND s.start_time<? AND s.end_time>?""",
                        (booking["student_id"], booking_id, target["slot_date"], target["end_time"], target["start_time"]),
                    ).fetchone():
                        raise ValueError("The Student has an overlapping booked examination.")
                    db.execute("UPDATE bookings SET slot_id=? WHERE id=?", (target_id, booking_id))
                    for slot_id in (booking["slot_id"], target_id):
                        db.execute("""UPDATE examination_slots SET status=CASE WHEN
                            (SELECT count(*) FROM bookings WHERE slot_id=? AND status!='Cancelled') >= maximum_capacity
                            THEN 'Full' ELSE 'Available' END WHERE id=? AND status IN ('Available','Full')""", (slot_id, slot_id))
                    db.execute("INSERT INTO admin_changes(admin_id,booking_id,slot_id,change_type,old_value,new_value,reason) VALUES(?,?,?,'Reschedule',?,?,?)",
                               (g.user["id"], booking_id, target_id, booking["slot_id"], target_id, reason))
                flash("Booking rescheduled. Old and new slot details recorded in change history.", "success")
                return redirect(url_for("admin_bookings"))
            except (ValueError, sqlite3.IntegrityError) as error:
                fail(error)
        slots = db.execute("SELECT s.*,u.name AS examiner_name FROM examination_slots s JOIN users u ON u.id=s.examiner_id WHERE s.examination_id=? ORDER BY s.slot_date,s.start_time", (booking["examination_id"],)).fetchall()
        return render_template("admin/reschedule.html", booking=booking, slots=slots)

    @app.get("/admin/history")
    @admin_only
    def admin_history():
        db = get_db()
        changes = db.execute("SELECT a.*,u.name AS admin_name FROM admin_changes a JOIN users u ON u.id=a.admin_id ORDER BY a.id DESC").fetchall()
        evaluations = db.execute(
            """SELECT v.*,b.student_id,u.name AS student_name,e.name AS exam_name,x.name AS examiner_name,
               (SELECT sum(m.awarded_marks) FROM evaluation_marks m WHERE m.evaluation_id=v.id) AS total_marks
               FROM evaluations v JOIN bookings b ON b.id=v.booking_id JOIN users u ON u.id=b.student_id
               JOIN examinations e ON e.id=b.examination_id JOIN users x ON x.id=v.examiner_id ORDER BY v.id DESC"""
        ).fetchall()
        marks = db.execute("SELECT m.*,r.criterion_name,r.maximum_marks,r.weightage FROM evaluation_marks m JOIN examination_rubrics r ON r.id=m.rubric_id ORDER BY m.evaluation_id,r.id").fetchall()
        return render_template("admin/history.html", changes=changes, evaluations=evaluations, marks=marks)
