"""Milestone 6 integration checks across all three roles."""

import unittest
from datetime import timedelta

import test_student


class TrackingTests(unittest.TestCase):
    sql = test_student.StudentTests.sql
    post = test_student.StudentTests.post
    login = test_student.StudentTests.login
    book = test_student.StudentTests.book
    booking_id = test_student.StudentTests.booking_id
    tearDown = test_student.StudentTests.tearDown

    def setUp(self):
        test_student.StudentTests.setUp(self)

    def target_slot(self):
        original = self.sql('SELECT * FROM examination_slots WHERE id=?', (self.slot,))[0]
        self.sql("INSERT INTO examination_slots(examination_id,examiner_id,slot_date,start_time,end_time,maximum_capacity) VALUES(1,2,?,'11:00:00','11:30:00',1)", (original['slot_date'],))
        return self.sql('SELECT max(id) FROM examination_slots')[0][0]

    def evaluate(self, bid, remarks='Final feedback'):
        marks = {f'marks_{r["id"]}':str(r['maximum_marks']-5) for r in self.sql('SELECT * FROM examination_rubrics WHERE examination_id=1')}
        return self.post(f'/examiner/bookings/{bid}/evaluate', {**marks, 'remarks':remarks})

    def expire_booking(self):
        dates = [(self.now-timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S') for d in [6,5,4,3]]
        self.sql('UPDATE examinations SET slot_creation_start=?,slot_creation_end=?,booking_start=?,booking_end=? WHERE id=1', dates)

    def test_reschedule_reassign_evaluate_result_and_history(self):
        target = self.target_slot()
        self.book()
        bid = self.booking_id()
        booked_at = self.sql('SELECT booking_date FROM bookings WHERE id=?', (bid,))[0][0]
        self.login(1)
        self.assertIn(b'Booking rescheduled', self.post(f'/admin/bookings/{bid}/reschedule', {'slot_id':target,'reason':'Student timetable request'}).data)
        self.assertIn(b'Examiner reassigned', self.post(f'/admin/slots/{target}/reassign', {'examiner_id':3,'reason':'Faculty replacement'}).data)
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?', (self.slot,))[0][0], 'Available')
        self.assertEqual(self.sql('SELECT status FROM examination_slots WHERE id=?', (target,))[0][0], 'Full')
        self.login(2)
        self.assertEqual(self.client.get(f'/examiner/bookings/{bid}/evaluate').status_code,404)
        self.login(3)
        self.assertIn(b'User 4', self.client.get(f'/examiner/slots/{target}/students').data)
        self.assertIn(b'Evaluation submitted', self.evaluate(bid).data)
        self.login(4)
        page = self.client.get('/student/bookings').data
        self.assertIn(b'User 3', page)
        self.assertIn(b'11:00:00', page)
        self.assertIn(b'Completed', page)
        self.assertIn(b'Final feedback', self.client.get(f'/student/bookings/{bid}/result').data)
        self.assertEqual(self.sql('SELECT booking_date FROM bookings WHERE id=?', (bid,))[0][0], booked_at)
        self.login(5)
        self.assertEqual(self.client.get(f'/student/bookings/{bid}/result').status_code,404)
        self.login(1)
        history = self.client.get('/admin/history').data
        for value in [b'Student timetable request', b'Faculty replacement', b'Final feedback']:
            self.assertIn(value, history)
        self.assertEqual(len(self.sql('SELECT * FROM admin_changes')),2)
        self.assertEqual(self.sql('SELECT examiner_id FROM evaluations')[0][0],3)
        self.assertFalse(self.sql('PRAGMA foreign_key_check'))

    def test_rescheduled_booking_cancel_rebook_and_history_survive(self):
        target = self.target_slot()
        self.book()
        bid = self.booking_id()
        self.login(1)
        self.post(f'/admin/bookings/{bid}/reschedule', {'slot_id':target,'reason':'Move before cancellation'})
        self.login(4)
        self.post(f'/student/bookings/{bid}/cancel')
        self.book()
        records = self.sql('SELECT id,status,slot_id FROM bookings WHERE student_id=4 ORDER BY id')
        self.assertEqual([(r['status'],r['slot_id']) for r in records],[('Cancelled',target),('Booked',self.slot)])
        self.assertEqual(len(self.sql('SELECT * FROM admin_changes WHERE booking_id=?',(bid,))),1)
        self.login(1)
        self.assertIn(b'Cancelled',self.client.get('/admin/bookings').data)
        self.assertIn(b'Move before cancellation',self.client.get('/admin/history').data)

    def test_timeline_extension_reopens_booking_without_losing_history(self):
        self.book()
        bid = self.booking_id()
        self.expire_booking()
        self.assertIn(b'deadline has passed',self.post(f'/student/bookings/{bid}/cancel').data)
        self.login(1)
        dates = [(self.now+timedelta(days=d)).strftime('%Y-%m-%dT%H:%M') for d in [-4,-3,-2,2]]
        fields = ['slot_creation_start','slot_creation_end','booking_start','booking_end']
        self.post('/admin/examinations/1?section=timeline', {'action':'timeline',**dict(zip(fields,dates))})
        self.assertEqual(self.sql('SELECT status FROM examinations WHERE id=1')[0][0],'Booking Open')
        self.login(4)
        self.assertIn(b'Booking cancelled',self.post(f'/student/bookings/{bid}/cancel').data)
        self.assertEqual(len(self.sql('SELECT * FROM bookings')),1)

    def test_evaluated_records_protected_from_admin_overrides(self):
        target = self.target_slot()
        self.book()
        bid = self.booking_id()
        self.login(2)
        self.evaluate(bid)
        before = [tuple(r) for r in self.sql('SELECT * FROM evaluations')]
        self.login(1)
        self.post(f'/admin/bookings/{bid}/reschedule', {'slot_id':target,'reason':'Must fail'})
        self.post(f'/admin/slots/{self.slot}/reassign', {'examiner_id':3,'reason':'Must fail'})
        self.post(f'/admin/slots/{self.slot}/edit', {**self.slot_data,'maximum_capacity':'3'})
        self.post('/admin/examinations/1/delete')
        self.assertEqual([tuple(r) for r in self.sql('SELECT * FROM evaluations')],before)
        self.assertEqual(self.sql('SELECT slot_id FROM bookings WHERE id=?',(bid,))[0][0],self.slot)
        self.assertFalse(self.sql('SELECT * FROM admin_changes'))
        self.assertFalse(self.sql('PRAGMA foreign_key_check'))

    def test_incomplete_marks_do_not_complete_examination(self):
        self.book()
        bid = self.booking_id()
        self.sql("UPDATE bookings SET status='Completed' WHERE id=?",(bid,))
        self.sql('INSERT INTO evaluations(booking_id,examiner_id) VALUES(?,2)',(bid,))
        self.expire_booking()
        self.client.get('/student/bookings')
        self.assertEqual(self.sql('SELECT status FROM examinations WHERE id=1')[0][0],'Closed')
        self.assertIn(b'Result pending',self.client.get(f'/student/bookings/{bid}/result').data)

    def test_completed_results_survive_deactivation_and_course_archiving(self):
        self.book()
        bid = self.booking_id()
        self.login(2)
        self.evaluate(bid)
        self.login(1)
        self.post('/admin/examiners',{'action':'status','user_id':2,'status':'Deactivated'})
        self.post('/admin/courses/1/edit',{'code':'CE101','name':'Civil','status':'Inactive'})
        self.expire_booking()
        self.login(4)
        self.assertIn(b'Final feedback',self.client.get(f'/student/bookings/{bid}/result').data)
        self.assertIn(b'Completed',self.client.get('/student/bookings').data)
        self.assertEqual(self.sql('SELECT status FROM examinations WHERE id=1')[0][0],'Completed')


if __name__ == '__main__':
    unittest.main()
