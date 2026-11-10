# Examination Management Portal (MAD-I)

A web-based Examination Management Portal
developed using Flask, SQLite, Jinja2 and Bootstrap.

The system supports three roles:
- Admin
- Examiner
- Student

## Current progress

- Milestone 0: initial setup committed; collaborator invitation sent. Making the
  GitHub repository private is still pending.
- Database models and schema setup: implemented; 20 verification checks passed.
  Awaiting review and approval before commit or the next milestone.
- Authentication, dashboards, and booking workflows are not implemented yet.

## Database setup

Use Python 3.10 or newer. In PowerShell, from the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install Flask
.\.venv\Scripts\python.exe init_db.py
```

Flask includes Werkzeug, which hashes passwords. No additional database package
is needed: Python includes SQLite support. If your Python command is `python`
instead of `py`, use `python -m venv .venv` for the first command.

On the first run, enter an Admin email and a password of at least eight
characters. The password is hidden during entry and stored only as a hash.
There is no default password and no Admin registration page.

The script creates `examination_portal.db` programmatically. Running it again
preserves the existing Admin and other records. It does not recreate missing
columns in an older schema; later schema changes will need explicit migration.
The generated database is excluded from Git.

## Database structure

| Table | Purpose |
| --- | --- |
| `users` | Names, contact details, password hashes, roles, and active status |
| `examiner_profiles` | Examiner department and approval status |
| `courses` | Course details and active status |
| `examinations` | Course examinations, timelines, status, and result publication |
| `examination_rubrics` | Criteria, maximum marks, and percentage weightages |
| `examiner_assignments` | Examiners assigned to each examination |
| `examination_slots` | Assigned examiner, date, times, capacity, and status |
| `bookings` | Student bookings, cancellations, and completion history |
| `evaluations` | Evaluating examiner, booking, remarks, and submission time |
| `evaluation_marks` | Awarded marks for each evaluation criterion |

Foreign keys protect relationships. Referenced historical records cannot be
deleted; later management screens must deactivate them or reject deletion.
Cancelled bookings remain as separate history rows. A student can rebook after
cancelling, but cannot have two non-cancelled bookings for the same examination.
Available seats will be calculated from slot capacity and non-cancelled bookings
rather than stored separately.

Examination timelines and slot dates/times use local time (Asia/Kolkata). Store
timestamps as `YYYY-MM-DD HH:MM:SS`, dates as `YYYY-MM-DD`, and times as `HH:MM:SS`.
SQLite's automatic record timestamps use UTC. Slot creation ends before student
booking begins, matching the rule that examiners edit slots before booking starts.

Later milestones must enforce user roles, time windows, capacity, assignment
approval, evaluation ownership, rubric totals, and maximum awarded marks in
the application. The schema alone does not implement these workflows.

## Database verification

Verified on 10 November 2026 using Python 3.14 and Flask 3.1.3. Twenty checks
passed against a temporary SQLite database, which was removed after testing:

- Fresh initialization, all ten tables, and foreign-key enforcement.
- Verifiable Admin password hash, exactly one Admin, case-insensitive unique
  emails, and allowed user roles.
- Course relationships, examiner assignments, positive slot capacity, and
  matching booking examination/slot relationships.
- Duplicate active booking rejection and rebooking after cancellation while
  preserving the cancelled record.
- Evaluation storage, non-negative marks, and protection of referenced history.
- SQLite integrity and foreign-key checks.
- Repeated initialization preserves Admin credentials, bookings, and marks.

These checks verify database behavior, not the later web workflows. Run the
initializer above to create your own local database and choose Admin credentials.

Setup issue resolved: the sandbox could not access installed Python, and the
installed interpreter did not have Flask. A project-local `.venv` was created
and Flask installed there. The virtual environment is excluded from Git.
