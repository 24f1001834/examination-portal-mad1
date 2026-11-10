-- CREATE IF NOT EXISTS makes initialization safe to run again.
-- RESTRICT preserves records referenced by booking and evaluation history.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    role TEXT NOT NULL CHECK (role IN ('Admin', 'Examiner', 'Student')),
    phone TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- At most one pre-existing superuser. Students and examiners can be many.
CREATE UNIQUE INDEX IF NOT EXISTS one_admin ON users(role) WHERE role = 'Admin';

CREATE TABLE IF NOT EXISTS examiner_profiles (
    examiner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
    department TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Deactivated'))
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Inactive'))
);

CREATE TABLE IF NOT EXISTS examinations (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    exam_type TEXT NOT NULL
        CHECK (exam_type IN ('Viva', 'Practical', 'Project Demo', 'Assessment')),
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    maximum_marks REAL NOT NULL CHECK (maximum_marks > 0),
    -- Store local dates and times as YYYY-MM-DD HH:MM:SS consistently.
    slot_creation_start TEXT NOT NULL,
    slot_creation_end TEXT NOT NULL,
    booking_start TEXT NOT NULL,
    booking_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Draft'
        CHECK (status IN ('Draft', 'Slot Creation', 'Booking Open', 'Closed', 'Completed')),
    results_published INTEGER NOT NULL DEFAULT 0 CHECK (results_published IN (0, 1)),
    CHECK (slot_creation_start < slot_creation_end),
    CHECK (slot_creation_end <= booking_start),
    CHECK (booking_start < booking_end)
);

CREATE TABLE IF NOT EXISTS examination_rubrics (
    id INTEGER PRIMARY KEY,
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE RESTRICT,
    criterion_name TEXT NOT NULL,
    maximum_marks REAL NOT NULL CHECK (maximum_marks > 0),
    weightage REAL NOT NULL CHECK (weightage > 0 AND weightage <= 100),
    description TEXT,
    UNIQUE (examination_id, criterion_name)
);

-- An examination can be assigned to several examiners.
CREATE TABLE IF NOT EXISTS examiner_assignments (
    examination_id INTEGER NOT NULL REFERENCES examinations(id) ON DELETE RESTRICT,
    examiner_id INTEGER NOT NULL REFERENCES examiner_profiles(examiner_id) ON DELETE RESTRICT,
    PRIMARY KEY (examination_id, examiner_id)
);

CREATE TABLE IF NOT EXISTS examination_slots (
    id INTEGER PRIMARY KEY,
    examination_id INTEGER NOT NULL,
    examiner_id INTEGER NOT NULL,
    slot_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    maximum_capacity INTEGER NOT NULL CHECK (maximum_capacity > 0),
    status TEXT NOT NULL DEFAULT 'Available'
        CHECK (status IN ('Available', 'Full', 'Cancelled', 'Completed')),
    CHECK (start_time < end_time),
    FOREIGN KEY (examination_id, examiner_id)
        REFERENCES examiner_assignments(examination_id, examiner_id) ON DELETE RESTRICT,
    UNIQUE (id, examination_id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    slot_id INTEGER NOT NULL,
    examination_id INTEGER NOT NULL,
    booking_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'Booked'
        CHECK (status IN ('Booked', 'Cancelled', 'Completed')),
    completed_at TEXT,
    FOREIGN KEY (slot_id, examination_id)
        REFERENCES examination_slots(id, examination_id) ON DELETE RESTRICT
);

-- Cancelled rows remain in history; students may rebook after cancellation.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_exam
    ON bookings(student_id, examination_id) WHERE status != 'Cancelled';

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    booking_id INTEGER NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE RESTRICT,
    examiner_id INTEGER NOT NULL REFERENCES examiner_profiles(examiner_id) ON DELETE RESTRICT,
    remarks TEXT,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evaluation_marks (
    evaluation_id INTEGER NOT NULL REFERENCES evaluations(id) ON DELETE RESTRICT,
    rubric_id INTEGER NOT NULL REFERENCES examination_rubrics(id) ON DELETE RESTRICT,
    awarded_marks REAL NOT NULL CHECK (awarded_marks >= 0),
    PRIMARY KEY (evaluation_id, rubric_id)
);

-- Available seats will be calculated from capacity minus Booked/Completed rows.
-- This avoids keeping a second seat count that can become inconsistent.
-- Time windows, user roles, capacity, rubric totals, and evaluation ownership
-- also need application checks in the later functionality milestones.
