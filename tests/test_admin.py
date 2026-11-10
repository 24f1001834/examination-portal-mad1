"""Run with: python -m unittest discover -s tests -v"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app import create_app
from database import get_connection
from init_db import initialize_database
from werkzeug.security import check_password_hash, generate_password_hash


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        initialize_database('admin@example.test', 'Admin-password', self.path)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': self.path})
        self.client = self.app.test_client()
        password = generate_password_hash('User-password')
        for uid, role, name in [(2, 'Examiner', 'First Examiner'), (3, 'Examiner', 'Second Examiner'),
                                (4, 'Student', 'First Student'), (5, 'Student', 'Second Student'),
                                (6, 'Examiner', 'Pending Examiner')]:
            self.sql('INSERT INTO users(id,email,password_hash,name,role) VALUES(?,?,?,?,?)',
                     (uid, f'user{uid}@example.test', password, name, role))
            if role == 'Examiner':
                self.sql('INSERT INTO examiner_profiles VALUES(?,?,?)', (uid, 'Civil', 'Pending' if uid == 6 else 'Approved'))
        self.sql("INSERT INTO courses VALUES(1,'CE101','Civil','Description','Active')")
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        dates = [now - timedelta(days=2), now + timedelta(days=1), now + timedelta(days=2), now + timedelta(days=3)]
        self.exam_data = dict(course_id='1', name='Civil Viva', exam_type='Viva', duration_minutes='30', maximum_marks='100',
                              **dict(zip(['slot_creation_start', 'slot_creation_end', 'booking_start', 'booking_end'], [d.strftime('%Y-%m-%dT%H:%M') for d in dates])))
        self.login()
        self.post('/admin/examinations', self.exam_data)
        self.exam = self.sql('SELECT id FROM examinations')[0][0]
        self.detail = f'/admin/examinations/{self.exam}'
        self.post(self.detail+'?section=timeline', {'action':'timeline', **self.exam_data})

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, query, args=()):
        db = get_connection(self.path)
        try:
            with db:
                return db.execute(query, args).fetchall()
        finally:
            db.close()

    def post(self, route, data=None, follow=True):
        self.client.get('/login')
        with self.client.session_transaction() as session:
            token = session['csrf_token']
        return self.client.post(route, data={'csrf_token': token, **(data or {})}, follow_redirects=follow)

    def login(self, uid=1):
        self.post('/logout')
        email = 'admin@example.test' if uid == 1 else f'user{uid}@example.test'
        return self.post('/login', {'email': email, 'password': 'Admin-password' if uid == 1 else 'User-password'})

    def ready_exam(self):
        self.post(self.detail, {'action': 'rubric', 'criterion_name': 'Understanding', 'maximum_marks': '100', 'weightage': '100'})
        self.post(self.detail, {'action': 'assign', 'examiner_id': '2'})

    def slots(self):
        self.ready_exam()
        date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        for sid, start, end, capacity in [(1, '10:00:00', '10:30:00', 1), (2, '11:00:00', '11:30:00', 1), (3, '12:00:00', '12:30:00', 1)]:
            self.sql('INSERT INTO examination_slots(id,examination_id,examiner_id,slot_date,start_time,end_time,maximum_capacity) VALUES(?,?,2,?,?,?,?)',
                     (sid, self.exam, date, start, end, capacity))
        self.sql('INSERT INTO bookings(id,student_id,slot_id,examination_id) VALUES(1,4,1,?)', (self.exam,))
        self.sql("UPDATE examination_slots SET status='Full' WHERE id=1")

    def test_all_admin_pages_render(self):
        self.slots()
        for route in ['/admin/dashboard', '/admin/courses', '/admin/courses/1/edit', '/admin/examinations',
                      self.detail, self.detail + '/edit', '/admin/users', '/admin/slots', '/admin/bookings',
                      '/admin/history', '/admin/slots/1/reassign', '/admin/bookings/1/reschedule']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)
        for route in ['/admin/courses/new', '/admin/examinations/new', '/admin/examiners', '/admin/students',
                      self.detail+'?section=examiners', self.detail+'?section=rubrics', self.detail+'?section=timeline']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_list_and_form_navigation(self):
        courses = self.client.get('/admin/courses').data
        self.assertIn(b'New course', courses)
        self.assertNotIn(b'name="code"', courses)
        for title in [b'ID', b'Code', b'Name', b'Description']:
            self.assertIn(b'<th>'+title+b'</th>', courses)
        self.assertIn(b'name="code"', self.client.get('/admin/courses/new').data)
        self.assertIn(b'name="code"', self.client.get('/admin/courses/1/edit').data)
        exams = self.client.get('/admin/examinations').data
        self.assertIn(b'New examination', exams)
        self.assertNotIn(b'name="duration_minutes"', exams)
        self.assertIn(b'>Manage</a>', exams)
        self.assertNotIn(b'Delete unused', exams)
        form = self.client.get('/admin/examinations/new').data
        self.assertIn(b'name="duration_minutes"', form)
        self.assertNotIn(b'name="booking_start"', form)
        self.assertNotIn(b'name="booking_start"', self.client.get(self.detail).data)
        self.assertIn(b'name="booking_start"', self.client.get(self.detail+'?section=timeline').data)
        students = self.client.get('/admin/students').data
        self.assertIn(b'First Student', students)
        self.assertNotIn(b'First Examiner', students)
        self.assertNotIn(b'Add an approved Examiner', students)
        examiners = self.client.get('/admin/examiners').data
        self.assertIn(b'First Examiner', examiners)
        self.assertNotIn(b'First Student', examiners)

    def test_create_without_timeline_then_configure(self):
        basic = {k:v for k,v in self.exam_data.items() if k not in ('slot_creation_start','slot_creation_end','booking_start','booking_end')}
        self.post('/admin/examinations/new', basic)
        new_id = self.sql('SELECT max(id) FROM examinations')[0][0]
        self.client.get('/admin/examinations')
        exam = self.sql('SELECT * FROM examinations WHERE id=?', (new_id,))[0]
        self.assertIsNone(exam['booking_start'])
        self.assertEqual(exam['status'], 'Draft')
        url = f'/admin/examinations/{new_id}?section=timeline'
        invalid = {**self.exam_data, 'action':'timeline', 'booking_end':'bad'}
        self.assertIn(b'valid timeline', self.post(url, invalid).data)
        self.assertIsNone(self.sql('SELECT booking_start FROM examinations WHERE id=?', (new_id,))[0][0])
        self.post(url, {**self.exam_data, 'action':'timeline'})
        self.assertIsNotNone(self.sql('SELECT booking_start FROM examinations WHERE id=?', (new_id,))[0][0])

    def test_required_timeline_database_migration_preserves_history(self):
        self.slots()
        old_path = Path(self.temp.name) / 'old.db'
        old = get_connection(old_path)
        source = get_connection(self.path)
        schema = Path('schema.sql').read_text()
        for field in ('slot_creation_start','slot_creation_end','booking_start','booking_end'):
            schema = schema.replace(field+' TEXT,', field+' TEXT NOT NULL,')
        old.executescript(schema)
        tables = ['users','examiner_profiles','courses','examinations','examination_rubrics',
                  'examiner_assignments','examination_slots','bookings']
        before = {}
        with old:
            for table in tables:
                records = [tuple(r) for r in source.execute('SELECT * FROM '+table)]
                before[table] = records
                if records:
                    old.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in records[0])+')', records)
        old.close()
        source.close()
        initialize_database(database_path=old_path)
        upgraded = get_connection(old_path)
        try:
            for table in tables:
                self.assertEqual([tuple(r) for r in upgraded.execute('SELECT * FROM '+table)], before[table])
            self.assertFalse(upgraded.execute('PRAGMA foreign_key_check').fetchall())
            self.assertFalse(next(c for c in upgraded.execute('PRAGMA table_info(examinations)') if c['name']=='booking_end')['notnull'])
        finally:
            upgraded.close()

    def test_timeline_edit_allowed_with_existing_slots_and_bookings(self):
        self.slots()
        slots_before = [tuple(r) for r in self.sql('SELECT * FROM examination_slots ORDER BY id')]
        bookings_before = [tuple(r) for r in self.sql('SELECT * FROM bookings ORDER BY id')]
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        dates = [now-timedelta(days=4), now-timedelta(days=3), now-timedelta(days=2), now+timedelta(days=2)]
        fields = ['slot_creation_start','slot_creation_end','booking_start','booking_end']
        data = dict(zip(fields, [d.strftime('%Y-%m-%dT%H:%M') for d in dates]))
        url = self.detail+'?section=timeline'
        page = self.post(url, {'action':'timeline', **data})
        self.assertIn(b'Examination updated', page.data)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Booking Open')
        self.assertEqual([tuple(r) for r in self.sql('SELECT * FROM examination_slots ORDER BY id')], slots_before)
        self.assertEqual([tuple(r) for r in self.sql('SELECT * FROM bookings ORDER BY id')], bookings_before)
        saved = tuple(self.sql('SELECT slot_creation_start,slot_creation_end,booking_start,booking_end FROM examinations')[0])
        self.assertIn(b'Timelines must follow', self.post(url, {'action':'timeline', **data, 'booking_end':data['booking_start']}).data)
        self.assertEqual(tuple(self.sql('SELECT slot_creation_start,slot_creation_end,booking_start,booking_end FROM examinations')[0]), saved)

    def test_dashboard_counts(self):
        self.slots()
        page = self.client.get('/admin/dashboard').data
        for label in [b'Courses', b'Examinations', b'Examiners', b'Students', b'Slots', b'Bookings']:
            self.assertIn(label, page)
        # Check the view's context through template_rendered rather than HTML spacing.
        from flask import template_rendered
        contexts = []
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            self.client.get('/admin/dashboard')
        self.assertEqual(contexts[0]['counts'], {'Courses': 1, 'Examinations': 1, 'Slots': 3, 'Bookings': 1, 'Examiners': 3, 'Students': 2})

    def test_roles_and_csrf(self):
        self.slots()
        urls = ['/admin/dashboard', '/admin/courses', '/admin/examinations', self.detail, '/admin/users',
                '/admin/slots', '/admin/bookings', '/admin/history', '/admin/slots/1/reassign', '/admin/bookings/1/reschedule']
        for uid in [4, 2]:
            self.login(uid)
            for url in urls:
                with self.subTest(uid=uid, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            for url in ['/admin/courses', '/admin/courses/1/delete', '/admin/examinations', self.detail, self.detail+'/delete', '/admin/users', '/admin/slots/1/reassign', '/admin/bookings/1/reschedule']:
                self.assertEqual(self.post(url, {}, follow=False).status_code, 403)
        self.post('/logout')
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.login()
        self.assertEqual(self.client.post('/admin/courses', data={}).status_code, 400)
        self.assertEqual(self.client.get('/admin/courses/1/delete').status_code, 405)

    def test_course_create_edit_delete_and_protection(self):
        self.post('/admin/courses', {'code': 'CE102', 'name': 'New', 'status': 'Active'})
        cid = self.sql("SELECT id FROM courses WHERE code='CE102'")[0][0]
        self.post(f'/admin/courses/{cid}/edit', {'code': 'CE102', 'name': 'Changed', 'status': 'Inactive'})
        self.assertEqual(self.sql('SELECT name FROM courses WHERE id=?', (cid,))[0][0], 'Changed')
        self.assertIn(b'Cannot save', self.post('/admin/courses', {'code': 'ce101', 'name': 'Duplicate', 'status': 'Active'}).data)
        self.assertIn(b'Cannot save', self.post('/admin/courses/1/delete').data)
        self.post(f'/admin/courses/{cid}/delete')
        self.assertFalse(self.sql('SELECT id FROM courses WHERE id=?', (cid,)))

    def test_exam_validation_edit_delete(self):
        for changes in [{'duration_minutes': '0'}, {'maximum_marks': 'nan'}, {'maximum_marks': 'inf'},
                        {'exam_type': 'Invalid'}]:
            with self.subTest(changes=changes):
                self.post('/admin/examinations', {**self.exam_data, **changes})
                self.assertEqual(len(self.sql('SELECT id FROM examinations')), 1)
        self.post(self.detail+'/edit', {**self.exam_data, 'name': 'Updated'})
        self.assertEqual(self.sql('SELECT name FROM examinations')[0][0], 'Updated')
        self.post(self.detail+'/delete')
        self.assertFalse(self.sql('SELECT id FROM examinations'))

    def test_existing_slots_protect_exam_and_delete_is_atomic(self):
        self.slots()
        page = self.post(self.detail+'/edit', {**self.exam_data, 'maximum_marks': '200'})
        self.assertIn(b'Slots already exist', page.data)
        self.assertEqual(self.sql('SELECT maximum_marks FROM examinations')[0][0], 100)
        self.assertIn(b'Cannot save', self.post(self.detail+'/delete').data)
        self.assertEqual(len(self.sql('SELECT * FROM examination_rubrics')), 1)
        self.assertEqual(len(self.sql('SELECT * FROM examiner_assignments')), 1)

    def test_rubric_crud_totals_and_freeze(self):
        self.post(self.detail, {'action': 'rubric', 'criterion_name': 'First', 'maximum_marks': '50', 'weightage': '50'})
        rid = self.sql('SELECT id FROM examination_rubrics')[0][0]
        self.post(self.detail, {'action': 'rubric', 'rubric_id': rid, 'criterion_name': 'Edited', 'maximum_marks': '100', 'weightage': '100'})
        self.assertEqual(self.sql('SELECT criterion_name FROM examination_rubrics')[0][0], 'Edited')
        self.assertIn(b'cannot exceed', self.post(self.detail, {'action': 'rubric', 'criterion_name': 'Extra', 'maximum_marks': '1', 'weightage': '1'}).data)
        self.post(self.detail, {'action': 'delete_rubric', 'rubric_id': rid})
        self.assertFalse(self.sql('SELECT id FROM examination_rubrics'))
        self.ready_exam()
        self.sql("INSERT INTO examination_slots(examination_id,examiner_id,slot_date,start_time,end_time,maximum_capacity) VALUES(?,2,'2099-01-01','10:00:00','10:30:00',1)", (self.exam,))
        self.assertIn(b'before any slots', self.post(self.detail, {'action': 'delete_rubric', 'rubric_id': self.sql('SELECT id FROM examination_rubrics')[0][0]}).data)

    def test_examiner_add_approve_deactivate(self):
        self.post('/admin/users', {'action': 'add', 'name': 'Added', 'email': 'added@example.test', 'department': 'Civil', 'password': 'Added-password'})
        user = self.sql("SELECT * FROM users WHERE email='added@example.test'")[0]
        self.assertTrue(check_password_hash(user['password_hash'], 'Added-password'))
        self.assertEqual(self.sql('SELECT status FROM examiner_profiles WHERE examiner_id=?', (user['id'],))[0][0], 'Deactivated')
        self.assertEqual(user['is_active'], 0)
        self.assertIn(b'Cannot save', self.post('/admin/users', {'action': 'add', 'name': 'Added', 'email': 'added@example.test', 'department': 'Civil', 'password': 'Added-password'}).data)
        for status in ['Approved', 'Deactivated', 'Approved']:
            self.post('/admin/users', {'action': 'status', 'user_id': 6, 'status': status})
            self.assertEqual(self.sql('SELECT status FROM examiner_profiles WHERE examiner_id=6')[0][0], status)
            self.assertEqual(self.sql('SELECT is_active FROM users WHERE id=6')[0][0], int(status=='Approved'))
        self.assertNotIn(b'value="Rejected"', self.client.get('/admin/users').data)
        self.post('/admin/users', {'action': 'status', 'user_id': 6, 'status': 'Rejected'})
        self.assertEqual(self.sql('SELECT status FROM examiner_profiles WHERE examiner_id=6')[0][0], 'Approved')
        self.assertIn(b'only to Examiners', self.post('/admin/users', {'action': 'status', 'user_id': 4, 'status': 'Approved'}).data)

    def test_assignments_require_approval_and_allow_removal(self):
        self.assertIn(b'approved Examiner', self.post(self.detail, {'action': 'assign', 'examiner_id': 6}).data)
        self.post(self.detail, {'action': 'assign', 'examiner_id': 2})
        self.assertEqual(len(self.sql('SELECT * FROM examiner_assignments')), 1)
        self.post(self.detail, {'action': 'unassign', 'examiner_id': 2})
        self.assertFalse(self.sql('SELECT * FROM examiner_assignments'))
        self.slots()
        self.assertIn(b'Cannot save', self.post(self.detail, {'action': 'unassign', 'examiner_id': 2}).data)

    def test_no_manual_phase_controls(self):
        page = self.client.get(self.detail)
        self.assertNotIn(b'Automatic status', page.data)
        self.assertNotIn(b'name="status"', page.data)
        self.assertNotIn(b'Activate / resume', page.data)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Slot Creation')
        self.post(self.detail, {'action':'status', 'status':'Closed'})
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Slot Creation')

    def test_completion_is_not_booking_deadline_alone(self):
        self.slots()
        now = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        dates = [now-timedelta(days=4), now-timedelta(days=3), now-timedelta(days=2), now-timedelta(days=1)]
        self.sql('UPDATE examinations SET slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=?', [d.strftime('%Y-%m-%d %H:%M:%S') for d in dates])
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Closed')
        self.sql("UPDATE bookings SET status='Completed'")
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Closed')
        self.sql('INSERT INTO evaluations(booking_id,examiner_id) VALUES(1,2)')
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Closed')
        rubric_id = self.sql('SELECT id FROM examination_rubrics')[0][0]
        evaluation_id = self.sql('SELECT id FROM evaluations')[0][0]
        self.sql('INSERT INTO evaluation_marks VALUES(?,?,80)', (evaluation_id, rubric_id))
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Completed')

    def test_completion_does_not_require_a_completed_booking(self):
        self.sql("UPDATE examinations SET slot_creation_start='2000-01-01 00:00:00',slot_creation_end='2000-01-02 00:00:00',booking_start='2000-01-03 00:00:00',booking_end='2000-01-04 00:00:00'")
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Completed')

    def test_results_need_no_admin_publication_and_are_student_specific(self):
        from results import get_student_result
        self.slots()
        self.sql('INSERT INTO bookings(student_id,slot_id,examination_id) VALUES(5,2,?)', (self.exam,))
        self.sql("UPDATE bookings SET status='Completed' WHERE id=1")
        self.sql('INSERT INTO evaluations(id,booking_id,examiner_id,remarks) VALUES(1,1,2,?)', ('Well done',))
        rubric = self.sql('SELECT id FROM examination_rubrics')[0][0]
        db = get_connection(self.path)
        try:
            self.assertIsNone(get_student_result(db, 1, 4))
            self.sql('INSERT INTO evaluation_marks VALUES(1,?,80)', (rubric,))
            result = get_student_result(db, 1, 4)
            self.assertEqual(result['total_marks'], 80)
            self.assertEqual(result['evaluation']['remarks'], 'Well done')
            self.assertIsNone(get_student_result(db, 1, 5))
            self.assertIsNone(get_student_result(db, 2, 5))
            self.sql('UPDATE evaluation_marks SET awarded_marks=101')
            self.assertIsNone(get_student_result(db, 1, 4))
        finally:
            db.close()
        page = self.client.get(self.detail).data
        self.assertNotIn(b'Set visibility', page)
        self.assertNotIn(b'value="publish"', page)
        self.post(self.detail, {'action':'publish', 'published':'1'})
        self.assertEqual(self.sql('SELECT results_published FROM examinations')[0][0], 0)
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE student_id=5')[0][0], 'Booked')

    def test_only_cancelled_bookings_allow_completion(self):
        self.slots()
        self.sql("UPDATE bookings SET status='Cancelled'")
        self.sql("UPDATE examinations SET slot_creation_start='2000-01-01 00:00:00',slot_creation_end='2000-01-02 00:00:00',booking_start='2000-01-03 00:00:00',booking_end='2000-01-04 00:00:00'")
        self.client.get(self.detail)
        self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], 'Completed')

    def test_search_by_name_and_id(self):
        self.slots()
        for url, expected in [('/admin/users?q=First+Student&role=Student', b'First Student'),
                              ('/admin/users?q=6', b'Pending Examiner'),
                              ('/admin/examinations?q=Civil+Viva', b'Civil Viva'),
                              (f'/admin/examinations?q={self.exam}', b'Civil Viva'),
                              ('/admin/bookings?q=First+Student', b'First Student'),
                              ('/admin/bookings?q=1', b'First Student')]:
            self.assertIn(expected, self.client.get(url).data)
        self.assertNotIn(b'Second Student', self.client.get('/admin/users?q=First+Student&role=Student').data)
        self.assertIn(b'No matching bookings', self.client.get('/admin/bookings?q=Missing').data)
        self.assertEqual(self.client.get('/admin/users?role=Admin').status_code, 400)

    def test_reschedule_preserves_history_and_updates_capacity(self):
        self.slots()
        self.post('/admin/bookings/1/reschedule', {'slot_id': 2, 'reason': 'Student request'})
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=1')[0][0], 2)
        self.assertEqual([r[0] for r in self.sql('SELECT status FROM examination_slots WHERE id IN(1,2) ORDER BY id')], ['Available', 'Full'])
        change = self.sql('SELECT * FROM admin_changes')[0]
        self.assertEqual((change['old_value'], change['new_value'], change['reason']), (1, 2, 'Student request'))
        self.assertIn(b'Student request', self.client.get('/admin/history').data)

    def test_reschedule_rejects_full_past_cancelled_and_same_slot(self):
        self.slots()
        self.assertIn(b'different slot', self.post('/admin/bookings/1/reschedule', {'slot_id': 1, 'reason': 'Test'}).data)
        self.sql('INSERT INTO bookings(student_id,slot_id,examination_id) VALUES(5,2,?)', (self.exam,))
        self.assertIn(b'target slot is full', self.post('/admin/bookings/1/reschedule', {'slot_id': 2, 'reason': 'Test'}).data)
        self.sql("UPDATE examination_slots SET status='Cancelled' WHERE id=3")
        self.assertIn(b'upcoming available slot', self.post('/admin/bookings/1/reschedule', {'slot_id': 3, 'reason': 'Test'}).data)
        self.sql("UPDATE examination_slots SET status='Available',slot_date='2000-01-01' WHERE id=3")
        self.assertIn(b'upcoming available slot', self.post('/admin/bookings/1/reschedule', {'slot_id': 3, 'reason': 'Test'}).data)
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=1')[0][0], 1)
        self.assertFalse(self.sql('SELECT * FROM admin_changes'))

    def test_reschedule_protects_evaluations_and_cancelled_history(self):
        self.slots()
        self.sql('INSERT INTO evaluations(booking_id,examiner_id) VALUES(1,2)')
        self.assertIn(b'Only unevaluated', self.post('/admin/bookings/1/reschedule', {'slot_id': 2, 'reason': 'Test'}).data)

    def test_reschedule_rejects_cross_exam_and_student_conflicts(self):
        self.slots()
        self.post('/admin/examinations', {**self.exam_data, 'name': 'Other exam'})
        other = self.sql('SELECT max(id) FROM examinations')[0][0]
        self.sql('INSERT INTO examiner_assignments VALUES(?,3)', (other,))
        date = self.sql('SELECT slot_date FROM examination_slots WHERE id=2')[0][0]
        self.sql("INSERT INTO examination_slots(id,examination_id,examiner_id,slot_date,start_time,end_time,maximum_capacity) VALUES(4,?,3,?,'11:00:00','11:30:00',2)", (other, date))
        self.assertIn(b'same examination', self.post('/admin/bookings/1/reschedule', {'slot_id': 4, 'reason': 'Test'}).data)
        self.sql('INSERT INTO bookings(student_id,slot_id,examination_id) VALUES(4,4,?)', (other,))
        self.assertIn(b'overlapping booked examination', self.post('/admin/bookings/1/reschedule', {'slot_id': 2, 'reason': 'Test'}).data)
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=1')[0][0], 1)
        self.assertFalse(self.sql('SELECT * FROM admin_changes'))
        self.sql("UPDATE bookings SET status='Cancelled' WHERE id=1")
        self.assertIn(b'Only unevaluated', self.post('/admin/bookings/1/reschedule', {'slot_id': 2, 'reason': 'Test'}).data)

    def test_reassign_retains_bookings_and_logs_changes(self):
        self.slots()
        self.post('/admin/slots/1/reassign', {'examiner_id': 3, 'reason': 'Faculty absence'})
        self.assertEqual(self.sql('SELECT examiner_id FROM examination_slots WHERE id=1')[0][0], 3)
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=1')[0][0], 1)
        self.assertEqual(len(self.sql('SELECT * FROM examiner_assignments WHERE examiner_id=3')), 1)
        self.assertEqual(self.sql('SELECT change_type FROM admin_changes')[0][0], 'Reassign')

    def test_reassign_rejects_conflicts_and_protects_evaluations(self):
        self.slots()
        self.assertIn(b'different Examiner', self.post('/admin/slots/1/reassign', {'examiner_id': 2, 'reason': 'Test'}).data)
        self.assertIn(b'approved Examiner', self.post('/admin/slots/1/reassign', {'examiner_id': 6, 'reason': 'Test'}).data)
        self.sql('INSERT INTO examiner_assignments VALUES(?,3)', (self.exam,))
        self.sql("UPDATE examination_slots SET examiner_id=3,start_time='10:15:00',end_time='10:45:00' WHERE id=2")
        self.assertIn(b'overlapping slot', self.post('/admin/slots/1/reassign', {'examiner_id': 3, 'reason': 'Test'}).data)
        self.sql('INSERT INTO evaluations(booking_id,examiner_id) VALUES(1,2)')
        self.assertIn(b'evaluated slots', self.post('/admin/slots/1/reassign', {'examiner_id': 3, 'reason': 'Test'}).data)
        self.assertEqual(self.sql('SELECT examiner_id FROM examination_slots WHERE id=1')[0][0], 2)

    def test_missing_records_and_history_integrity(self):
        for url in ['/admin/courses/999/edit', '/admin/examinations/999', '/admin/slots/999/reassign', '/admin/bookings/999/reschedule']:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.slots()
        self.sql('INSERT INTO evaluations(id,booking_id,examiner_id,remarks) VALUES(1,1,2,?)', ('<script>remarks</script>',))
        rubric = self.sql('SELECT id FROM examination_rubrics')[0][0]
        self.sql('INSERT INTO evaluation_marks VALUES(1,?,80)', (rubric,))
        page = self.client.get('/admin/history').data
        self.assertIn(b'Understanding', page)
        self.assertIn(b'&lt;script&gt;remarks&lt;/script&gt;', page)
        self.assertFalse(self.sql('PRAGMA foreign_key_check'))
        self.assertEqual(self.sql('PRAGMA integrity_check')[0][0], 'ok')

    def test_manage_slots_and_separate_examiner_form(self):
        self.slots()
        manage = self.client.get(self.detail).data
        self.assertIn(b'CE101', manage)
        self.assertIn(b'Edit slots', manage)
        self.assertNotIn(b'>Edit</a>', manage)
        self.assertIn(b'/admin/slots/1/edit', self.client.get(self.detail+'?section=slots').data)
        self.assertEqual(self.client.get('/admin/slots/1/edit').status_code, 200)
        date = self.sql('SELECT slot_date FROM examination_slots WHERE id=1')[0][0]
        data = {'slot_date':date,'start_time':'09:00','end_time':'09:30','maximum_capacity':'2'}
        self.post('/admin/slots/1/edit', data)
        self.assertEqual(self.sql('SELECT start_time,maximum_capacity,status FROM examination_slots WHERE id=1')[0][:], ('09:00:00',2,'Available'))
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=1')[0][0], 1)
        self.assertIn(b'overlapping slot', self.post('/admin/slots/1/edit', {**data,'start_time':'11:00','end_time':'11:30'}).data)
        self.sql('INSERT INTO evaluations(booking_id,examiner_id) VALUES(1,2)')
        self.assertIn(b'evaluated slots', self.post('/admin/slots/1/edit', data).data)
        page = self.client.get('/admin/examiners').data
        self.assertIn(b'Add Examiner', page)
        self.assertNotIn(b'name="password"', page)
        self.assertIn(b'>Activate</option>', page)
        self.assertIn(b'name="password"', self.client.get('/admin/examiners/new').data)
        self.login(4)
        self.assertEqual(self.client.get('/admin/slots/1/edit').status_code,403)
        self.assertEqual(self.client.get('/admin/examiners/new').status_code,403)

    def test_existing_database_upgrade_preserves_accounts(self):
        self.sql('DROP TABLE admin_changes')
        self.sql("UPDATE examinations SET status='Closed'")
        before = self.sql('SELECT password_hash FROM users WHERE id=1')[0][0]
        self.assertFalse(initialize_database(database_path=self.path))
        self.assertEqual(self.sql('SELECT password_hash FROM users WHERE id=1')[0][0], before)
        self.assertEqual(len(self.sql('SELECT * FROM users')), 6)
        self.assertEqual(self.sql("SELECT name FROM sqlite_master WHERE name='admin_changes'")[0][0], 'admin_changes')
        self.assertFalse(initialize_database(database_path=self.path))

    def test_requests_automatically_refresh_phase_without_admin_action(self):
        self.ready_exam()
        from unittest.mock import patch
        from phases import LOCAL_TIMEZONE
        exam = self.sql('SELECT * FROM examinations')[0]
        for field, expected in [('slot_creation_start','Slot Creation'), ('slot_creation_end','Closed'),
                                ('booking_start','Booking Open'), ('booking_end','Completed')]:
            instant = datetime.strptime(exam[field], '%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
            with patch('phases.datetime') as clock:
                clock.now.return_value = instant
                self.assertEqual(self.client.get(self.detail).status_code, 200)
            self.assertEqual(self.sql('SELECT status FROM examinations')[0][0], expected)

    def test_authentication_regression_and_approval_workflow(self):
        self.post('/logout')
        registration = dict(name='New Student', email='new@example.test', role='Student',
                            password='New-password', confirm_password='New-password')
        self.assertIn(b'Registration successful', self.post('/register', registration).data)
        self.assertIn(b'already exists', self.post('/register', registration).data)
        self.assertIn(b'Admin registration is not allowed', self.post('/register', {**registration, 'role': 'Admin'}).data)
        self.assertIn(b'Invalid email or password', self.post('/login', {'email': 'new@example.test', 'password': 'wrong'}).data)
        self.assertIn(b'Student dashboard', self.post('/login', {'email': 'new@example.test', 'password': 'New-password'}).data)
        self.post('/logout')
        self.assertEqual(self.client.get('/student/dashboard').status_code, 302)
        self.assertIn(b'requires Admin approval', self.login(6).data)
        self.login()
        self.post('/admin/users', {'action': 'status', 'user_id': 6, 'status': 'Approved'})
        self.assertIn(b'Examiner dashboard', self.login(6).data)
        other = self.app.test_client()
        other.get('/login')
        with other.session_transaction() as state:
            token = state['csrf_token']
        other.post('/login', data={'csrf_token': token, 'email': 'admin@example.test', 'password': 'Admin-password'})
        with other.session_transaction() as state:
            token = state['csrf_token']
        other.post('/admin/users', data={'csrf_token': token, 'action': 'status', 'user_id': 6, 'status': 'Deactivated'})
        self.assertEqual(self.client.get('/examiner/dashboard').status_code, 302)


if __name__ == '__main__':
    unittest.main()
