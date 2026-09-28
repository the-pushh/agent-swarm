import unittest
from dataclasses import asdict
from agents.calendar.scheduling import Preferences, instant, find_slots, comfortable
from agents.calendar.tools import Availability


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.prefs = Preferences(timezone='UTC')

    def slots(self, start, end, duration=30, busy=(), zone=None):
        return find_slots(instant(start), instant(end), duration, Availability(list(busy), []), self.prefs, zone)

    def test_lunch_snack_dinner_sleep_and_weekends_are_protected(self):
        for start, end in [('2026-09-28T12:00+00:00', '2026-09-28T13:00+00:00'),
                           ('2026-09-28T16:00+00:00', '2026-09-28T16:30+00:00'),
                           ('2026-09-28T19:00+00:00', '2026-09-28T20:00+00:00'),
                           ('2026-09-28T23:00+00:00', '2026-09-29T07:00+00:00'),
                           ('2026-09-27T09:00+00:00', '2026-09-27T18:00+00:00')]:
            self.assertEqual(self.slots(start, end), [], (start, end))

    def test_buffers_and_half_open_boundaries(self):
        busy = [('2026-09-28T10:00+00:00', '2026-09-28T11:00+00:00')]
        result = self.slots('2026-09-28T09:30+00:00', '2026-09-28T12:00+00:00', busy=busy)
        self.assertEqual(result[0][0], '2026-09-28T11:15:00+00:00')
        self.assertEqual(result[0][1], '2026-09-28T11:45:00+00:00')

    def test_all_day_busy_blocks_day_and_unknown_availability_is_not_fabricated(self):
        result = self.slots('2026-09-28T09:00+00:00', '2026-09-28T18:00+00:00',
                            busy=[('2026-09-28T00:00+00:00', '2026-09-29T00:00+00:00')])
        self.assertEqual(result, [])

    def test_participant_local_hours_checked(self):
        self.assertEqual(self.slots('2026-09-28T09:00+00:00', '2026-09-28T12:00+00:00', zone='America/Los_Angeles'), [])

    def test_overnight_sleep_overrides_extended_work_hours(self):
        self.prefs.work_start, self.prefs.work_end = '06:00', '23:00'
        self.assertFalse(comfortable(instant('2026-09-28T07:00+00:00'), instant('2026-09-28T07:30+00:00'), self.prefs))
        self.assertFalse(comfortable(instant('2026-09-28T22:00+00:00'), instant('2026-09-28T22:30+00:00'), self.prefs))

    def test_dst_day_slots_are_real_instants(self):
        self.prefs = Preferences(timezone='America/New_York', weekdays=list(range(7)))
        result = self.slots('2026-11-01T00:00+00:00', '2026-11-02T00:00+00:00')
        self.assertEqual(result[0][0], '2026-11-01T14:00:00+00:00')  # 09:00 after fallback.

    def test_invalid_duration_and_preferences_fail(self):
        with self.assertRaises(ValueError):
            self.slots('2026-09-28T09:00+00:00', '2026-09-28T18:00+00:00', duration=-5)
        with self.assertRaises(ValueError):
            Preferences(work_start='18:00', work_end='09:00').validate()
