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
- Database models and schema setup: approved, committed, and pushed;
  20 verification checks passed.
- Authentication and role-based access: approved, committed, and pushed.
- Milestone 3, Admin dashboard and examination management: approved, committed,
  and pushed.
- Milestone 4, Examiner dashboard and slot management: implemented and tested;
  awaiting review and approval before commit or the next milestone.
- Student booking and result-viewing screens belong to Milestone 5 and are not
  implemented yet.

## Database setup

Use Python 3.10 or newer. In PowerShell, from the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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
| `examinations` | Course examinations, timelines, and automatic status |
| `examination_rubrics` | Criteria, maximum marks, and percentage weightages |
| `examiner_assignments` | Examiners assigned to each examination |
| `examination_slots` | Assigned examiner, date, times, capacity, and status |
| `bookings` | Student bookings, cancellations, and completion history |
| `evaluations` | Evaluating examiner, booking, remarks, and submission time |
| `evaluation_marks` | Awarded marks for each evaluation criterion |
| `admin_changes` | Reasons and old/new IDs for rescheduling and reassignment |

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

## Run the portal

After installing requirements and running `init_db.py` as described above:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000 in your browser. Stop the server with Ctrl+C.
This is Flask's local development server with debug mode disabled.
Bootstrap styling loads from a CDN and needs an internet connection;
registration and login do not use JavaScript.

The first startup creates `.session-secret`, a local signing key excluded from
Git. Keep it private. Keeping this file preserves the signing key across
restarts; deleting it causes existing sessions to become invalid on restart.
The browser session stores the user ID and a form-protection token, not passwords.

## Authentication milestone

- Students and Examiners can register. Admin registration is forbidden, including
  attempts to submit an Admin role directly to the server.
- All roles use the same login page. Passwords are hashed with Werkzeug.
- Examiner registration creates a Pending profile. Only Approved, active
  Examiners may log in. Admin can now approve them from Examiners.
- Each role has its own protected dashboard. Admin and Examiner management are
  implemented; Student management belongs to the next milestone.
- Account activity and Examiner approval are checked on every request.
- Logout clears the session and requires a POST form submission. All POST
  forms require a session-bound CSRF token to prevent unwanted form submissions.
- Forms validate names, email, password length and confirmation, account type,
  and Examiner department. Duplicate emails are rejected case-insensitively.

Verification on 10 November 2026 used Flask's test client and a temporary SQLite
database, without changing real accounts. All 59 functional checks passed:
registration and validation, password hashing, all three login redirects, the
full role-access matrix, blocked Examiner statuses, deactivation during an active
session, logout, CSRF checks, HTML escaping, session cookie settings, database
integrity, and the missing-database setup message. HTML templates were rendered
by these checks; browser appearance has not been visually reviewed.

To try it yourself, register a Student, log in, and confirm that
`/student/dashboard` opens while `/admin/dashboard` returns Access denied.
Log out, then log in with the Admin credentials you chose during database setup.
Registering an Examiner should succeed, but login remains blocked until an
Admin approves the account from the Examiners page.

## Milestone 3 database update

Stop the server and run the initializer once before using the updated portal:

```powershell
.\.venv\Scripts\python.exe init_db.py
.\.venv\Scripts\python.exe app.py
```

The initializer adds the new `admin_changes` table without deleting existing
tables or resetting accounts/passwords. Existing scheduling flags from the previous implementation are ignored; no activation or pause settings are used. The
additive update was also tested against a database containing existing users.

## Admin workflow

Log in as Admin and use the links at the top of the dashboard:

1. Courses and Examinations open as list pages. Use New course or New
   examination to open a form; Edit opens the same form with existing values.
   After saving an examination, open Manage > Slot creation and booking to
   configure the four timeline timestamps in Asia/Kolkata time. Without a
   configured timeline the examination remains Draft. Manage also has separate
   Assigned Examiners, Evaluation Rubrics, and Edit slots options.
2. Add rubric criteria. Criterion maximum marks must total the examination
   maximum; percentage weightages should total 100 before Examiners create slots.
   Each criterion's maximum is its share of the total marks; weightage describes
   its intended emphasis. Do not apply weightages a second time to raw marks.
3. Approve registered Examiners, or add an Examiner with an initial password and activate the account. Assign an active, approved Examiner to the examination.
4. No manual phase controls are required. Slot creation and Student booking
   follow their configured periods automatically. Starts are inclusive and ends
   are exclusive. Before slot creation the phase is Draft; between periods and
   after booking ends it is Closed. Later Examiner/Student submissions must
   check the current phase with the shared scheduled_phase helper, as well as
   account permissions, rubric readiness, assignments, and slot capacity.
5. View all users, examinations, slots, bookings, and evaluations, including
   inactive, cancelled, and completed records. Search users and examinations
   by name or ID; search bookings by Student/examination name or booking/Student ID.
6. For exceptional cases, reschedule a Booked, unevaluated booking to an
   upcoming slot in the same examination. Admin can override the booking
   deadline. Full slots, Student conflicts, and inactive/unapproved Examiners
   are rejected. Old/new slot IDs and the required reason are recorded.
7. Change an upcoming slot's Examiner if necessary. The selected Examiner
   must be active and approved with no overlapping slot. This applies to all
   bookings in that slot and automatically adds the new examination assignment.
   Completed, cancelled, and evaluated slots are protected. The change is logged.
8. Completion is automatic after booking ends when no Booked records remain
   and all completed bookings have evaluations. An examination with no bookings
   or only cancelled bookings also becomes Completed. Each Student receives their
   own completed, valid evaluation directly; no Admin publication is required.


Unreferenced courses and examinations can be removed. Deletion of historical
records is rejected by foreign keys, and failed deletion rolls back related
changes. Use course Inactive or examination Closed when history must remain.
Rubrics are editable before slots exist. Once slots exist, only
the examination name can be edited; course, type, duration, marks, and timelines
are protected. Unused Examiner assignments can be removed; assignments with
slots remain until those slots are reassigned.

## Admin milestone verification

Run the repeatable test suite:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

All 32 tests passed on 11 November 2026. Tests use temporary databases and cover:

- All Admin pages rendering and dashboard counts.
- Admin-only GET/POST access, CSRF protection, and authentication regression.
- Course and examination create/edit/delete, validation, and historical protection.
- Rubric create/edit/delete, total limits, and configuration freezing.
- Examiner creation, approval/deactivation, assignments, and live
  session revocation after Admin deactivation.
- Automatic phase boundaries, UTC conversion, request-time transitions, Admin
  removal of manual controls, completion, and direct result eligibility.
- Name/ID searches and evaluation records with HTML escaping.
- Rescheduling, capacity updates, and change history; rejection of full, past,
  cancelled, cross-examination, conflicting, and evaluated booking changes.
- Examiner reassignment, overlap detection, and preservation of evaluations.
- Existing-database upgrades, foreign keys, and SQLite integrity.

Test fixtures supply slots/bookings/evaluations because the Examiner and Student
creation screens belong to later milestones. Browser appearance has not been
visually reviewed; templates are rendered and checked through Flask's test client.

Issue resolved: the original schema had no history of Admin overrides. The new
`admin_changes` table records the Admin, reason, timestamp, and old/new IDs for
each reschedule or reassignment.

Schedule phases are recalculated on every server request using Asia/Kolkata
time. No background process or JavaScript is required. An already-open browser
page must be refreshed to show the new phase; later form submissions must check
the current phase again. Completed examinations are never automatically reopened.

Phase selection, schedule activation, pause, and resume controls have been removed.
Configured dates alone control slot creation and booking availability.

## Direct evaluation results

Admin has no publish/hide result controls. The shared get_student_result helper
in results.py returns only the requesting Student's completed evaluation with
valid marks for all rubric criteria. Other Students' pending bookings and the
examination-wide phase do not block an eligible result. Incomplete evaluations
remain pending. Examiner submission is implemented in Milestone 4. The Student
result screen will use this rule in Milestone 5 and is not built yet.
The old results_published database column is retained only for compatibility
with existing databases and has no effect on result visibility.

Examiner statuses are Pending, Approved, and Deactivated. Deactivated covers
both declined registrations and disabled accounts. Admin can approve/reactivate
them later. Initialization converts legacy Rejected profiles to Deactivated
and disables those accounts without deleting their history.

Courses, examinations, Examiners, and Students display each public attribute in
its own table column. Examiners and Students have separate navigation tabs.
Timeline fields are optional until configured under examination Manage.
Initialization upgrades older required-timeline tables while preserving IDs
and referenced records. Password hashes are never displayed.

Manage displays the course code and offers Edit slots instead of editing the
examination itself. Slot dates, times, and capacity can be edited with conflict,
capacity, and historical-evaluation protection. Examination Edit remains on the
Examinations list. Add Examiner opens a separate form; new Admin-added accounts
start Deactivated. Activate and Deactivate control access.

## Milestone 4 Examiner workflow

This milestone follows the supplied Word assignment. The wireframe image is not
used as the UI specification. No schema migration or extra dependencies are
needed for this milestone.

1. Register an Examiner and activate the account from Admin > Examiners.
   Alternatively, Admin can add and then activate an Examiner.
2. From Admin, complete examination rubrics and assign that Examiner. Configure
   the slot creation and booking timelines under Manage.
3. Log in as the Examiner. The dashboard shows assigned examinations, owned
   slots, non-cancelled Student bookings, and pending evaluations. Each displayed
   attribute has its own table column. Profile allows name, phone, and department
   updates; it cannot change account approval, role, or email.
4. During the slot creation period, choose New slot for an assigned examination.
   Enter a date, start/end times, and positive whole-number capacity. The slot
   must start after Student booking closes, fit at least the examination duration,
   and not overlap any other non-cancelled slot for this Examiner. All slot times
   use Asia/Kolkata. Rubric marks and weightages must have valid totals.
5. Edit or delete an upcoming slot before Student booking starts. Slots with
   non-cancelled bookings require Admin assistance. Deletion retains a Cancelled
   slot if booking or Admin history references it; otherwise it removes the row.
   These rules are checked on every submission, including forms opened earlier.
6. Open Students for an owned slot. Bookings show their status and evaluation
   link. After the slot ends, enter marks for every criterion and optional remarks.
   Each score must be finite and between zero and that criterion's maximum.
   Submit once: the evaluation, marks, and booking completion are saved together.
7. Submitted evaluations are read-only. When no Booked records remain, the slot
   becomes Completed. The overall examination follows the existing automatic
   completion rules. Another Student's pending evaluation does not delay the
   submitted Student's result eligibility.

The Student results page and Student booking form remain Milestone 5 work.
Testing this milestone uses temporary booking fixtures instead of adding
unrequested booking tools to the Examiner interface. No real Student records
or evaluation records were created during testing.

## Milestone 4 verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

All 53 tests passed on 11 November 2026: 21 Examiner tests plus the 32 existing
Admin and scheduling tests. Coverage includes profile validation, dashboard
counts, creation-period boundaries, the booking-start edit deadline, slot
overlaps, assignment and ownership restrictions, deactivation, CSRF, rubric
readiness, invalid/missing marks, duplicate submissions, read-only evaluations,
slot and examination completion, and immediate Student-specific result
eligibility. A deliberately failed mark insert verifies that partial evaluations
and booking completion are rolled back together.

Issue resolved during implementation: an SQL string quote error prevented module
import on the first test run. It was corrected before rerunning the full suite.
Templates were rendered through Flask's test client; browser appearance has not
been visually reviewed.
