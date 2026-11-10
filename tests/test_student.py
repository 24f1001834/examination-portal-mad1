"""Student booking, history, privacy, and end-to-end result tests."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from flask import template_rendered

from phases import LOCAL_TIMEZONE
import test_examiner


class StudentTests(unittest.TestCase):
    # Reuse the temporary-database setup, not the Examiner test cases.
    sql=test_examiner.ExaminerTests.sql
    post=test_examiner.ExaminerTests.post
    login=test_examiner.ExaminerTests.login
    tearDown=test_examiner.ExaminerTests.tearDown

    def setUp(self):
        test_examiner.ExaminerTests.setUp(self)
        # Create actual slots through Examiner routes before opening booking.
        for eid,uid in [(1,2),(2,3)]:
            self.login(uid)
            self.post(f'/examiner/examinations/{eid}/slots/new',self.slot_data)
        self.slot=self.sql('SELECT id FROM examination_slots WHERE examination_id=1')[0][0]
        self.other_slot=self.sql('SELECT id FROM examination_slots WHERE examination_id=2')[0][0]
        dates=[(self.now+timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S') for d in [-4,-3,-2,2]]
        self.sql('UPDATE examinations SET slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=?',dates)
        self.sql('UPDATE examination_slots SET maximum_capacity=1')
        self.login(4)

    def book(self,slot=None):
        return self.post(f'/student/slots/{slot or self.slot}/book')

    def booking_id(self):
        return self.sql('SELECT id FROM bookings WHERE student_id=4 ORDER BY id DESC')[0][0]

    def test_pages_and_empty_state(self):
        for route in ['/student/dashboard','/student/profile','/student/examinations','/student/examinations/1','/student/bookings']:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code,200)
        self.assertIn(b'No booking history',self.client.get('/student/bookings').data)
        self.assertIn(b'No upcoming examinations',self.client.get('/student/dashboard').data)
        self.assertIn(b'Book</button>',self.client.get('/student/examinations/1').data)

    def test_booking_and_dashboard_counts(self):
        self.assertIn(b'Slot booked',self.book().data)
        b=self.sql('SELECT * FROM bookings')[0]
        self.assertEqual((b['student_id'],b['slot_id'],b['examination_id'],b['status']),(4,self.slot,1,'Booked'))
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(self.slot,))[0][0],'Full')
        contexts=[]
        def capture(sender,template,context,**extra):
            contexts.append(context)
        with template_rendered.connected_to(capture,self.app):
            self.client.get('/student/dashboard')
        self.assertEqual(len(contexts[0]['upcoming']),1)
        self.assertEqual(len(contexts[0]['bookings']),1)
        self.assertEqual(len(contexts[0]['available']),2)
        self.assertEqual(contexts[0]['result_ids'],[])
        self.assertIn(b'User 2',self.client.get('/student/bookings').data)

    def test_full_slot_and_duplicate_booking_rejected(self):
        self.book()
        self.assertIn(b'already have a booking',self.book().data)
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)
        self.login(5)
        self.assertIn(b'slot is full',self.book().data)
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)

    def test_cancel_restores_seat_and_rebooking_keeps_history(self):
        self.book()
        bid=self.booking_id()
        self.assertIn(b'Booking cancelled',self.post(f'/student/bookings/{bid}/cancel').data)
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=?',(bid,))[0][0],'Cancelled')
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(self.slot,))[0][0],'Available')
        self.book()
        self.assertEqual([r[0] for r in self.sql('SELECT status FROM bookings ORDER BY id')],['Cancelled','Booked'])
        self.assertIn(b'Cancelled',self.client.get('/student/bookings').data)

    def test_creation_booking_and_cancellation_boundaries(self):
        exam=self.sql('SELECT * FROM examinations WHERE id=1')[0]
        begin=datetime.strptime(exam['booking_start'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
        end=datetime.strptime(exam['booking_end'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
        with patch('student.local_now',return_value=begin-timedelta(seconds=1)):
            self.assertIn(b'booking is closed',self.book().data)
        with patch('student.local_now',return_value=begin):
            self.book()
        bid=self.booking_id()
        with patch('student.local_now',return_value=end):
            self.assertIn(b'deadline has passed',self.post(f'/student/bookings/{bid}/cancel').data)
        with patch('student.local_now',return_value=end-timedelta(seconds=1)):
            self.post(f'/student/bookings/{bid}/cancel')
        with patch('student.local_now',return_value=end):
            self.assertIn(b'booking is closed',self.book().data)
        self.assertEqual(len(self.sql("SELECT * FROM bookings WHERE status='Booked'")),0)

    def test_overlapping_exam_rejected_but_adjacent_slot_allowed(self):
        self.book()
        self.assertIn(b'overlaps',self.book(self.other_slot).data)
        self.sql("UPDATE examination_slots SET start_time='10:30:00',end_time='11:00:00' WHERE id=?",(self.other_slot,))
        self.assertIn(b'Slot booked',self.book(self.other_slot).data)
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),2)

    def test_inactive_course_examiner_and_unavailable_slots(self):
        self.sql("UPDATE courses SET status='Inactive'")
        self.assertIn(b'booking is closed',self.book().data)
        self.assertEqual(self.client.get('/student/examinations/1').status_code,404)
        self.sql("UPDATE courses SET status='Active'")
        self.sql("UPDATE examiner_profiles SET status='Deactivated' WHERE examiner_id=2")
        self.assertIn(b'Examiner is inactive',self.book().data)
        self.sql("UPDATE examiner_profiles SET status='Approved' WHERE examiner_id=2")
        for status in ['Cancelled','Completed']:
            self.sql('UPDATE examination_slots SET status=? WHERE id=?',(status,self.slot))
            self.assertIn(b'not available',self.book().data)
        self.sql("UPDATE examination_slots SET status='Available',slot_date='2000-01-01' WHERE id=?",(self.slot,))
        self.assertIn(b'not available',self.book().data)
        self.assertFalse(self.sql('SELECT * FROM bookings'))

    def test_search_filters_and_draft_examinations(self):
        for route,expected in [('/student/examinations?q=Exam+1',b'Exam 1'),
                               ('/student/examinations?q=CE101',b'Exam 2'),
                               ('/student/examinations?q=1',b'Exam 1'),
                               ('/student/examinations?course=1&type=Viva',b'Exam 1')]:
            self.assertIn(expected,self.client.get(route).data)
        self.assertNotIn(b'Exam 2',self.client.get('/student/examinations?q=Exam+1').data)
        self.assertIn(b'No matching examinations',self.client.get('/student/examinations?type=Practical').data)
        self.assertEqual(self.client.get('/student/examinations?type=Invalid').status_code,400)
        self.assertEqual(self.client.get('/student/examinations/999').status_code,404)
        self.sql('UPDATE examinations SET slot_creation_start=NULL,slot_creation_end=NULL,booking_start=NULL,booking_end=NULL WHERE id=1')
        self.assertEqual(self.client.get('/student/examinations/1').status_code,404)
        self.assertIn(b'booking is closed',self.book().data)

    def test_profile_validation_and_protected_fields(self):
        self.post('/student/profile',{'name':'Updated','phone':'123','role':'Admin','email':'wrong@example.test','is_active':'0'})
        u=self.sql('SELECT * FROM users WHERE id=4')[0]
        self.assertEqual((u['name'],u['phone'],u['role'],u['email'],u['is_active']),('Updated','123','Student','user4@example.test',1))
        for changes in [{'name':''},{'phone':'x'*31},{'name':'x'*101}]:
            self.post('/student/profile',{'name':'Updated','phone':'123',**changes})
            self.assertEqual(self.sql('SELECT name FROM users WHERE id=4')[0][0],'Updated')

    def test_ownership_and_role_access(self):
        self.book()
        bid=self.booking_id()
        self.login(5)
        self.assertEqual(self.client.get(f'/student/bookings/{bid}/result').status_code,404)
        self.assertEqual(self.post(f'/student/bookings/{bid}/cancel',follow=False).status_code,404)
        self.assertNotIn(b'User 2',self.client.get('/student/bookings').data)
        self.assertEqual(self.sql('SELECT status FROM bookings')[0][0],'Booked')
        for uid in [1,2]:
            self.login(uid)
            for route in ['/student/dashboard','/student/profile','/student/examinations','/student/examinations/1',
                          '/student/bookings',f'/student/bookings/{bid}/result']:
                self.assertEqual(self.client.get(route).status_code,403)
            for route in ['/student/profile',f'/student/slots/{self.slot}/book',f'/student/bookings/{bid}/cancel']:
                self.assertEqual(self.post(route,{},False).status_code,403)
        self.post('/logout')
        self.assertEqual(self.client.get('/student/bookings').status_code,302)

    def test_csrf_missing_records_and_deactivation(self):
        self.book()
        bid=self.booking_id()
        for route in ['/student/profile',f'/student/slots/{self.slot}/book',f'/student/bookings/{bid}/cancel']:
            self.assertEqual(self.client.post(route,data={}).status_code,400)
        self.assertEqual(self.client.get(f'/student/slots/{self.slot}/book').status_code,405)
        self.assertEqual(self.post('/student/slots/999/book',follow=False).status_code,404)
        self.assertEqual(self.post('/student/bookings/999/cancel',follow=False).status_code,404)
        self.assertEqual(self.client.get('/student/bookings/999/result').status_code,404)
        self.sql('UPDATE users SET is_active=0 WHERE id=4')
        self.assertEqual(self.client.get('/student/dashboard').status_code,302)

    def test_end_to_end_direct_result_and_other_student_pending(self):
        self.sql('UPDATE examination_slots SET maximum_capacity=2 WHERE id=?',(self.slot,))
        self.book()
        bid=self.booking_id()
        self.assertIn(b'Result pending',self.client.get(f'/student/bookings/{bid}/result').data)
        self.login(5)
        self.book()
        other_bid=self.sql('SELECT id FROM bookings WHERE student_id=5')[0][0]
        self.login(2)
        end=datetime.strptime(self.slot_data['slot_date']+' 10:30:00','%Y-%m-%d %H:%M:%S').replace(tzinfo=LOCAL_TIMEZONE)
        scores={f'marks_{r["id"]}':str(r['maximum_marks']-10) for r in self.sql('SELECT * FROM examination_rubrics WHERE examination_id=1')}
        with patch('examiner.local_now',return_value=end):
            self.post(f'/examiner/bookings/{bid}/evaluate',{**scores,'remarks':'<script>feedback</script>'})
        self.login(4)
        result=self.client.get(f'/student/bookings/{bid}/result')
        self.assertIn(b'80.0',result.data)
        self.assertIn(b'Understanding',result.data)
        self.assertIn(b'&lt;script&gt;feedback&lt;/script&gt;',result.data)
        self.assertNotIn(b'Result pending',result.data)
        self.assertIn(b'View result',self.client.get('/student/bookings').data)
        self.assertIn(b'Only pending bookings',self.post(f'/student/bookings/{bid}/cancel').data)
        self.assertEqual(self.sql('SELECT status FROM bookings WHERE id=?',(other_bid,))[0][0],'Booked')
        self.login(5)
        self.assertIn(b'Result pending',self.client.get(f'/student/bookings/{other_bid}/result').data)
        self.assertEqual(self.client.get(f'/student/bookings/{bid}/result').status_code,404)
        self.assertFalse(self.sql('PRAGMA foreign_key_check'))
        self.assertEqual(self.sql('PRAGMA integrity_check')[0][0],'ok')

    def test_incomplete_evaluation_does_not_expose_marks_or_feedback(self):
        self.book()
        bid=self.booking_id()
        self.sql("UPDATE bookings SET status='Completed' WHERE id=?",(bid,))
        self.sql('INSERT INTO evaluations(booking_id,examiner_id,remarks) VALUES(?,2,?)',(bid,'Private incomplete feedback'))
        page=self.client.get(f'/student/bookings/{bid}/result').data
        self.assertIn(b'Result pending',page)
        self.assertNotIn(b'Private incomplete feedback',page)

    def test_simultaneous_requests_cannot_overbook(self):
        clients=[]
        for uid in [4,5]:
            client=self.app.test_client()
            with client.session_transaction() as state:
                state['user_id']=uid
                state['csrf_token']='test-token'
            clients.append(client)
        barrier=Barrier(2)
        def compete(client):
            barrier.wait(timeout=10)
            return client.post(f'/student/slots/{self.slot}/book',data={'csrf_token':'test-token'}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(compete,clients))
        self.assertEqual(statuses,[302,302])
        self.assertEqual(len(self.sql("SELECT * FROM bookings WHERE status='Booked'")),1)
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(self.slot,))[0][0],'Full')

    def test_double_booking_and_double_cancel_are_idempotent(self):
        self.book()
        self.book()
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)
        bid=self.booking_id()
        self.post(f'/student/bookings/{bid}/cancel')
        self.post(f'/student/bookings/{bid}/cancel')
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)
        self.assertEqual(self.sql('SELECT status FROM bookings')[0][0],'Cancelled')
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?',(self.slot,))[0][0],'Available')


if __name__=='__main__':
    unittest.main()
