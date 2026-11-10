"""Examiner workflows tested against isolated SQLite databases."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import template_rendered
from werkzeug.security import generate_password_hash

from app import create_app
from database import get_connection
from init_db import initialize_database
from phases import LOCAL_TIMEZONE
from results import get_student_result


class ExaminerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'examiner.db'
        initialize_database('admin@example.test', 'Admin-password', self.path)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': self.path})
        self.client = self.app.test_client()
        password = generate_password_hash('User-password')
        for uid, role in [(2, 'Examiner'), (3, 'Examiner'), (4, 'Student'), (5, 'Student'), (6, 'Examiner')]:
            self.sql('INSERT INTO users(id,email,password_hash,name,role) VALUES(?,?,?,?,?)',
                     (uid, f'user{uid}@example.test', password, f'User {uid}', role))
            if role == 'Examiner':
                self.sql('INSERT INTO examiner_profiles VALUES(?,?,?)', (uid, 'Civil', 'Pending' if uid == 6 else 'Approved'))
        self.sql("INSERT INTO courses VALUES(1,'CE101','Civil','','Active')")
        now = datetime.now(LOCAL_TIMEZONE).replace(second=0, microsecond=0)
        self.now = now
        self.dates = [(now + timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S') for d in [-1, 1, 2, 3]]
        for eid in [1, 2]:
            self.sql("""INSERT INTO examinations(id,course_id,name,exam_type,duration_minutes,maximum_marks,
                       slot_creation_start,slot_creation_end,booking_start,booking_end)
                       VALUES(?,1,?,'Viva',30,100,?,?,?,?)""", (eid, f'Exam {eid}', *self.dates))
            self.sql('INSERT INTO examiner_assignments VALUES(?,?)', (eid, eid+1))
            self.sql('INSERT INTO examination_rubrics(examination_id,criterion_name,maximum_marks,weightage) VALUES(?,?,?,?)', (eid, 'Understanding', 60, 60))
            self.sql('INSERT INTO examination_rubrics(examination_id,criterion_name,maximum_marks,weightage) VALUES(?,?,?,?)', (eid, 'Explanation', 40, 40))
        self.slot_data = dict(slot_date=(now+timedelta(days=5)).strftime('%Y-%m-%d'),
                              start_time='10:00', end_time='10:30', maximum_capacity='2')
        self.login(2)

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
        with self.client.session_transaction() as state:
            token = state['csrf_token']
        return self.client.post(route, data={'csrf_token':token, **(data or {})}, follow_redirects=follow)

    def login(self, uid):
        self.post('/logout')
        return self.post('/login', {'email':'admin@example.test' if uid==1 else f'user{uid}@example.test',
                                    'password':'Admin-password' if uid==1 else 'User-password'})

    def create_slot(self):
        self.post('/examiner/examinations/1/slots/new', self.slot_data)
        return self.sql('SELECT id FROM examination_slots WHERE examination_id=1')[0][0]

    def prepare_evaluation(self):
        sid = self.create_slot()
        # Bookings are fixtures until the Student milestone provides booking screens.
        self.sql('INSERT INTO bookings(id,student_id,slot_id,examination_id) VALUES(1,4,?,1)', (sid,))
        self.sql('INSERT INTO bookings(id,student_id,slot_id,examination_id) VALUES(2,5,?,1)', (sid,))
        past = (self.now-timedelta(days=1)).strftime('%Y-%m-%d')
        self.sql('UPDATE examination_slots SET slot_date=? WHERE id=?', (past, sid))
        return sid

    def scores(self):
        return {f'marks_{r["id"]}':str(r['maximum_marks']-10)
                for r in self.sql('SELECT * FROM examination_rubrics WHERE examination_id=1')}

    def test_pages_render_and_dashboard_counts(self):
        sid = self.prepare_evaluation()
        for route in ['/examiner/dashboard','/examiner/profile','/examiner/examinations/1/slots/new',
                      f'/examiner/slots/{sid}/edit',f'/examiner/slots/{sid}/students','/examiner/bookings/1/evaluate']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code,200)
        contexts=[]
        def capture(sender, template, context, **extra):
            contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            self.client.get('/examiner/dashboard')
        context=contexts[0]
        self.assertEqual(len(context['exams']),1)
        self.assertEqual(len(context['slots']),1)
        self.assertEqual(context['booked'],2)
        self.assertEqual(context['pending'],2)
        self.assertNotIn(b'Exam 2',self.client.get('/examiner/dashboard').data)

    def test_profile_updates_only_allowed_fields(self):
        self.post('/examiner/profile',{'name':'Updated','phone':'12345','department':'Structures',
                                      'role':'Admin','status':'Approved','email':'changed@example.test'})
        user=self.sql('SELECT * FROM users WHERE id=2')[0]
        self.assertEqual((user['name'],user['phone'],user['role'],user['email']),('Updated','12345','Examiner','user2@example.test'))
        self.assertEqual(self.sql('SELECT department FROM examiner_profiles WHERE examiner_id=2')[0][0],'Structures')
        for changes in [{'name':''},{'department':''},{'phone':'x'*31},{'name':'x'*101}]:
            self.post('/examiner/profile',{'name':'Updated','phone':'12345','department':'Structures',**changes})
            self.assertEqual(self.sql('SELECT name FROM users WHERE id=2')[0][0],'Updated')

    def test_create_edit_and_delete_slot(self):
        sid=self.create_slot()
        slot=self.sql('SELECT * FROM examination_slots WHERE id=?',(sid,))[0]
        self.assertEqual((slot['examiner_id'],slot['examination_id'],slot['maximum_capacity']),(2,1,2))
        self.post(f'/examiner/slots/{sid}/edit',{**self.slot_data,'start_time':'11:00','end_time':'11:30','maximum_capacity':'3'})
        self.assertEqual(self.sql('SELECT start_time,maximum_capacity FROM examination_slots WHERE id=?',(sid,))[0][:],('11:00:00',3))
        self.post(f'/examiner/slots/{sid}/delete')
        self.assertFalse(self.sql('SELECT * FROM examination_slots'))

    def test_creation_window_exact_boundaries(self):
        for moment,allowed in [(self.dates[0],True),(self.dates[1],False)]:
            instant=datetime.strptime(moment,'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
            with patch('examiner.local_now',return_value=instant):
                self.post('/examiner/examinations/1/slots/new',self.slot_data)
            self.assertEqual(len(self.sql('SELECT * FROM examination_slots')),int(allowed))
            self.sql('DELETE FROM examination_slots')
        before=datetime.strptime(self.dates[0],'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)-timedelta(seconds=1)
        with patch('examiner.local_now',return_value=before):
            self.assertIn(b'only during the slot creation',self.post('/examiner/examinations/1/slots/new',self.slot_data).data)
        self.assertFalse(self.sql('SELECT * FROM examination_slots'))

    def test_slot_validation(self):
        cases=[{'slot_date':'bad'},{'start_time':'bad'},{'maximum_capacity':'0'},
               {'maximum_capacity':'1.5'},{'maximum_capacity':'-1'},
               {'end_time':'09:30'},{'end_time':'10:15'},
               {'slot_date':(self.now-timedelta(days=1)).strftime('%Y-%m-%d')},
               {'slot_date':(self.now+timedelta(days=1)).strftime('%Y-%m-%d')}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.post('/examiner/examinations/1/slots/new',{**self.slot_data,**changes})
                self.assertFalse(self.sql('SELECT * FROM examination_slots'))

    def test_conflicts_across_examinations(self):
        self.create_slot()
        self.sql('INSERT INTO examiner_assignments VALUES(2,2)')
        self.assertIn(b'overlaps',self.post('/examiner/examinations/2/slots/new',self.slot_data).data)
        self.post('/examiner/examinations/2/slots/new',{**self.slot_data,'start_time':'10:30','end_time':'11:00'})
        self.assertEqual(len(self.sql('SELECT * FROM examination_slots')),2)

    def test_unready_rubrics_prevent_creation(self):
        self.sql('DELETE FROM examination_rubrics WHERE examination_id=1')
        self.assertIn(b'complete the examination rubrics',self.post('/examiner/examinations/1/slots/new',self.slot_data).data)
        self.assertFalse(self.sql('SELECT * FROM examination_slots'))

    def test_edit_delete_deadline(self):
        sid=self.create_slot()
        start=datetime.strptime(self.dates[2],'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
        with patch('examiner.local_now',return_value=start):
            for route in [f'/examiner/slots/{sid}/edit',f'/examiner/slots/{sid}/delete']:
                self.assertIn(b'before Student booking starts',self.post(route,self.slot_data).data)
        self.assertTrue(self.sql('SELECT * FROM examination_slots WHERE id=?',(sid,)))
        # Editing is still allowed in the gap after creation closes, before booking.
        gap=start-timedelta(hours=1)
        with patch('examiner.local_now',return_value=gap):
            self.post(f'/examiner/slots/{sid}/edit',{**self.slot_data,'maximum_capacity':'4'})
        self.assertEqual(self.sql('SELECT maximum_capacity FROM examination_slots WHERE id=?',(sid,))[0][0],4)

    def test_slots_with_bookings_protected_and_cancelled_history_retained(self):
        sid=self.create_slot()
        self.sql('INSERT INTO bookings(student_id,slot_id,examination_id) VALUES(4,?,1)',(sid,))
        for action in ['edit','delete']:
            self.assertIn(b'has bookings',self.post(f'/examiner/slots/{sid}/{action}',self.slot_data).data)
        self.sql("UPDATE bookings SET status='Cancelled'")
        self.post(f'/examiner/slots/{sid}/delete')
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(sid,))[0][0],'Cancelled')
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)

    def test_other_examiner_cannot_access_or_modify_records(self):
        sid=self.prepare_evaluation()
        self.login(3)
        urls=[f'/examiner/slots/{sid}/edit',f'/examiner/slots/{sid}/students',
              '/examiner/bookings/1/evaluate','/examiner/examinations/1/slots/new']
        for route in urls:
            self.assertEqual(self.client.get(route).status_code,404)
            if not route.endswith('/students'):
                self.assertEqual(self.post(route,self.slot_data,False).status_code,404)
        self.assertEqual(self.post(f'/examiner/slots/{sid}/delete',follow=False).status_code,404)
        self.assertFalse(self.sql('SELECT * FROM evaluations'))

    def test_role_csrf_and_deactivation_restrictions(self):
        sid=self.prepare_evaluation()
        for uid in [1,4]:
            self.login(uid)
            for route in ['/examiner/dashboard','/examiner/profile',f'/examiner/slots/{sid}/students',
                          '/examiner/examinations/1/slots/new',f'/examiner/slots/{sid}/edit','/examiner/bookings/1/evaluate']:
                self.assertEqual(self.client.get(route).status_code,403)
            for route in ['/examiner/profile',f'/examiner/slots/{sid}/delete','/examiner/bookings/1/evaluate',
                          '/examiner/examinations/1/slots/new',f'/examiner/slots/{sid}/edit']:
                self.assertEqual(self.post(route,{},False).status_code,403)
        self.login(2)
        for route in ['/examiner/profile',f'/examiner/slots/{sid}/delete','/examiner/bookings/1/evaluate',
                      '/examiner/examinations/1/slots/new',f'/examiner/slots/{sid}/edit']:
            self.assertEqual(self.client.post(route,data={}).status_code,400)
        self.sql("UPDATE examiner_profiles SET status='Deactivated' WHERE examiner_id=2")
        self.assertEqual(self.client.get('/examiner/dashboard').status_code,302)
        self.assertNotIn(b'Examiner dashboard',self.login(6).data)

    def test_removed_assignment_and_reassigned_slot_revoke_access(self):
        self.sql('DELETE FROM examiner_assignments WHERE examination_id=1')
        self.assertEqual(self.client.get('/examiner/examinations/1/slots/new').status_code,404)
        self.sql('INSERT INTO examiner_assignments VALUES(1,2)')
        sid=self.prepare_evaluation()
        self.sql('INSERT INTO examiner_assignments VALUES(1,3)')
        self.sql('UPDATE examination_slots SET examiner_id=3 WHERE id=?',(sid,))
        self.assertEqual(self.client.get('/examiner/bookings/1/evaluate').status_code,404)
        self.assertEqual(self.post('/examiner/bookings/1/evaluate',self.scores(),False).status_code,404)

    def test_evaluation_commits_marks_and_direct_result(self):
        sid=self.prepare_evaluation()
        page=self.post('/examiner/bookings/1/evaluate',{**self.scores(),'remarks':'Well done'})
        self.assertIn(b'Evaluation submitted',page.data)
        booking=self.sql('SELECT * FROM bookings WHERE id=1')[0]
        self.assertEqual(booking['status'],'Completed')
        self.assertTrue(booking['completed_at'])
        self.assertEqual(len(self.sql('SELECT * FROM evaluation_marks')),2)
        self.assertEqual(self.sql('SELECT examiner_id FROM evaluations')[0][0],2)
        self.assertNotEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(sid,))[0][0],'Completed')
        db=get_connection(self.path)
        try:
            result=get_student_result(db,1,4)
            self.assertEqual(result['total_marks'],80)
            self.assertEqual(result['evaluation']['remarks'],'Well done')
            self.assertIsNone(get_student_result(db,1,5))
            self.assertIsNone(get_student_result(db,2,5))
        finally:
            db.close()
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=2')[0][0],'Booked')
        self.assertIn(b'View evaluation',page.data)
        self.assertIn(b'Well done',self.client.get('/examiner/bookings/1/evaluate').data)

    def test_final_evaluation_completes_slot_and_examination_after_deadline(self):
        sid=self.prepare_evaluation()
        dates=[(self.now-timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S') for d in [6,5,4,3]]
        self.sql('UPDATE examinations SET slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=? WHERE id=1',dates)
        self.post('/examiner/bookings/1/evaluate',self.scores())
        self.assertEqual(self.sql('SELECT status FROM examinations WHERE id=1')[0][0],'Closed')
        self.post('/examiner/bookings/2/evaluate',self.scores())
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(sid,))[0][0],'Completed')
        self.assertEqual(self.sql('SELECT status FROM examinations WHERE id=1')[0][0],'Completed')
        self.assertFalse(self.sql('PRAGMA foreign_key_check'))
        self.assertEqual(self.sql('PRAGMA integrity_check')[0][0],'ok')

    def test_invalid_marks_rollback_entire_evaluation(self):
        self.prepare_evaluation()
        rid=self.sql('SELECT id FROM examination_rubrics WHERE examination_id=1')[0][0]
        for value in ['', '-1','61','nan','inf','abc']:
            with self.subTest(value=value):
                self.post('/examiner/bookings/1/evaluate',{**self.scores(),f'marks_{rid}':value})
                self.assertFalse(self.sql('SELECT * FROM evaluations'))
                self.assertFalse(self.sql('SELECT * FROM evaluation_marks'))
                self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=1')[0][0],'Booked')
        self.post('/examiner/bookings/1/evaluate',{**self.scores(),'remarks':'x'*2001})
        self.assertFalse(self.sql('SELECT * FROM evaluations'))

    def test_missing_criterion_and_unready_rubrics_rejected(self):
        self.prepare_evaluation()
        self.post('/examiner/bookings/1/evaluate',{})
        self.assertFalse(self.sql('SELECT * FROM evaluations'))
        self.sql('UPDATE examination_rubrics SET weightage=10 WHERE examination_id=1')
        self.assertIn(b'complete the examination rubrics',self.post('/examiner/bookings/1/evaluate',self.scores()).data)
        self.assertFalse(self.sql('SELECT * FROM evaluations'))

    def test_database_failure_does_not_leave_partial_evaluation(self):
        self.prepare_evaluation()
        rid = self.sql('SELECT max(id) FROM examination_rubrics WHERE examination_id=1')[0][0]
        self.sql(f"""CREATE TRIGGER fail_second_mark BEFORE INSERT ON evaluation_marks
                    WHEN NEW.rubric_id={rid} BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        self.post('/examiner/bookings/1/evaluate', self.scores())
        self.assertFalse(self.sql('SELECT * FROM evaluations'))
        self.assertFalse(self.sql('SELECT * FROM evaluation_marks'))
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=1')[0][0], 'Booked')

    def test_duplicate_submission_and_cancelled_bookings_blocked(self):
        self.prepare_evaluation()
        self.post('/examiner/bookings/1/evaluate',{**self.scores(),'remarks':'Original'})
        self.post('/examiner/bookings/1/evaluate',{**self.scores(),'remarks':'Changed'})
        self.assertEqual(len(self.sql('SELECT * FROM evaluations')),1)
        self.assertEqual(self.sql('SELECT remarks FROM evaluations')[0][0],'Original')
        self.sql("UPDATE bookings SET status='Cancelled' WHERE id=2")
        self.assertIn(b'Only pending bookings',self.post('/examiner/bookings/2/evaluate',self.scores()).data)
        self.assertEqual(len(self.sql('SELECT * FROM evaluations')),1)

    def test_evaluation_allowed_before_slot_starts(self):
        sid=self.create_slot()
        self.sql('INSERT INTO bookings(id,student_id,slot_id,examination_id) VALUES(1,4,?,1)',(sid,))
        self.assertIn(b'Evaluation submitted',self.post('/examiner/bookings/1/evaluate',self.scores()).data)
        self.assertEqual(len(self.sql('SELECT * FROM evaluations')),1)
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=1')[0][0],'Completed')

    def test_zero_marks_and_escaped_feedback(self):
        self.prepare_evaluation()
        self.post('/examiner/bookings/1/evaluate',{**{key:'0' for key in self.scores()},'remarks':'<script>alert(1)</script>'})
        self.assertEqual(sum(r[0] for r in self.sql('SELECT awarded_marks FROM evaluation_marks')),0)
        page=self.client.get('/examiner/bookings/1/evaluate').data
        self.assertIn(b'&lt;script&gt;',page)
        self.assertNotIn(b'<script>alert',page)

    def test_empty_dashboard_and_missing_records(self):
        self.sql('DELETE FROM examiner_assignments WHERE examiner_id=2')
        self.assertIn(b'No assigned examinations',self.client.get('/examiner/dashboard').data)
        for route in ['/examiner/examinations/999/slots/new','/examiner/slots/999/edit',
                      '/examiner/slots/999/students','/examiner/bookings/999/evaluate']:
            self.assertEqual(self.client.get(route).status_code,404)


if __name__ == '__main__':
    unittest.main()
