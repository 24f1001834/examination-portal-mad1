"""Exact schedule boundaries without waiting for the clock."""
import unittest
from datetime import datetime, timezone
from phases import LOCAL_TIMEZONE, scheduled_phase


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.exam = dict(status='Closed',
                         slot_creation_start='2026-11-10 10:00:00',
                         slot_creation_end='2026-11-10 11:00:00',
                         booking_start='2026-11-10 12:00:00',
                         booking_end='2026-11-10 13:00:00')

    def at(self, time):
        return datetime.fromisoformat('2026-11-10T' + time).replace(tzinfo=LOCAL_TIMEZONE)

    def test_exact_boundaries_and_gap(self):
        for time, phase in [('09:59:59','Draft'), ('10:00:00','Slot Creation'),
                            ('10:59:59','Slot Creation'), ('11:00:00','Closed'),
                            ('12:00:00','Booking Open'), ('12:59:59','Booking Open'),
                            ('13:00:00','Closed')]:
            with self.subTest(time=time):
                self.assertEqual(scheduled_phase(self.exam, self.at(time)), phase)

    def test_completed_never_reopens(self):
        for time in ['10:30:00', '12:30:00']:
            self.assertEqual(scheduled_phase({**self.exam, 'status':'Completed'}, self.at(time)), 'Completed')

    def test_clock_converts_from_utc(self):
        utc = datetime(2026,11,10,4,30,tzinfo=timezone.utc)
        self.assertEqual(scheduled_phase(self.exam, utc), 'Slot Creation')
