import unittest
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock
from agents.calendar.agent import open_agent
from agents.calendar.control_loop import CalendarLoop, new_state
from agents.calendar.scheduling import instant
from agents.calendar.tools import Event, Availability
from agents.calendar.tool_implementations import DemoCalendarTools, JsonModelTools, demo_completion


class CalendarLoopTests(unittest.TestCase):
    def setUp(self):
        self.now = instant('2026-09-28T08:00+00:00')
        self.provider = DemoCalendarTools()
        self.provider.timezone = 'UTC'
        self.provider.events = [Event('old', 'Past review', '2026-09-25T10:00+00:00', '2026-09-25T11:00+00:00'),
            Event('one', 'Own meeting', '2026-09-28T10:00+00:00', '2026-09-28T11:00+00:00', 'v1', ['alex@example.com'], 'me@example.com', True),
            Event('two', 'Their meeting', '2026-09-28T10:30+00:00', '2026-09-28T11:30+00:00', 'v1', ['alex@example.com'], 'alex@example.com', False)]
        self.complete = Mock(side_effect=demo_completion)
        self.state = new_state()
        self.loop = CalendarLoop(self.provider, JsonModelTools(self.complete), self.state, lambda: None, now=lambda: self.now)

    def plan(self, **kw):
        return self.loop.plan({'kind': 'meeting', 'title': 'New meeting', 'attendees': 'alex@example.com',
                               'duration': '30', 'days': '1', **kw})[0]

    def test_scan_backlog_conflicts_no_model_calls(self):
        self.loop.scan()
        self.assertTrue(self.state['events']['old']['backlog'])
        self.assertTrue(self.state['events']['one']['issues'])
        self.assertEqual(self.state['last_scan']['from'], '2026-09-21T08:00:00+00:00')
        self.complete.assert_not_called()

    def test_meeting_approval_exact_payload_and_alternative_retirement(self):
        key = self.plan()
        self.provider.create_event = Mock(return_value='new-event')
        with self.assertRaises(ValueError):
            self.loop.execute(key)
        self.loop.review(key, True)
        self.assertEqual([i['status'] for k, i in self.state['proposals'].items() if k != key], ['rejected', 'rejected'])
        self.loop.execute(key)
        self.provider.create_event.assert_called_once()
        self.assertEqual(self.state['proposals'][key]['status'], 'done')
        with self.assertRaises(ValueError):
            self.loop.execute(key)
        self.complete.assert_not_called()

    def test_changed_payload_or_event_blocks_execution(self):
        key = self.plan()
        self.loop.review(key, True)
        self.state['proposals'][key]['proposal']['attendees'] = ['someoneelse@example.com']
        with self.assertRaisesRegex(ValueError, 'exact'):
            self.loop.execute(key)
        key = self.plan(kind='reschedule', event_id='one')
        self.loop.review(key, True)
        self.provider.events[1] = replace(self.provider.events[1], etag='v2')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.loop.execute(key)

    def test_new_conflict_blocks_approved_slot(self):
        key = self.plan()
        self.loop.review(key, True)
        p = self.state['proposals'][key]['proposal']
        self.provider.availability = Mock(return_value=Availability([(p['start'], p['end'])], []))
        self.provider.create_event = Mock()
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            self.loop.execute(key)
        self.provider.create_event.assert_not_called()

    def test_nonorganizer_requires_mail_draft_and_drafts_only_selected_slot(self):
        with self.assertRaisesRegex(ValueError, 'not the organizer'):
            self.plan(kind='reschedule', event_id='two')
        key = self.plan(kind='mail', event_id='two')
        self.complete.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Generate'):
            self.loop.review(key, True)
        self.loop.draft(key)
        self.assertEqual(self.complete.call_count, 1)
        self.assertTrue(self.state['proposals'][key]['proposal']['draft_body'])
        self.loop.review(key, True)
        self.provider.save_mail_draft = Mock(return_value='draft-id')
        self.provider.move_event = Mock()
        self.loop.execute(key)
        self.provider.save_mail_draft.assert_called_once()
        self.provider.move_event.assert_not_called()

    def test_unknown_attendee_availability_is_in_proposal(self):
        key = self.plan()
        self.assertEqual(self.state['proposals'][key]['proposal']['unknown_availability'], ['alex@example.com'])

    def test_uncertain_write_is_not_retried_and_survives_restart(self):
        key = self.plan()
        self.loop.review(key, True)
        self.provider.create_event = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):
            self.loop.execute(key)
        self.assertEqual(self.state['proposals'][key]['status'], 'uncertain')
        with self.assertRaises(ValueError):
            self.loop.execute(key)
        self.provider.create_event.assert_called_once()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'calendar.json'
            with open_agent(path, self.provider, self.loop.model) as loop:
                loop.state['proposals'] = self.state['proposals']
                loop.state['proposals'][key]['status'] = 'executing'
                loop.checkpoint()
            with open_agent(path, self.provider, self.loop.model) as loop:
                self.assertEqual(loop.state['proposals'][key]['status'], 'uncertain')

    def test_reminders_deduplicate_and_cancelled_commitments_disappear(self):
        self.loop.scan()
        self.now = instant('2026-09-28T09:45+00:00')
        self.assertEqual([r['event_id'] for r in self.loop.reminders()], ['one'])
        self.assertEqual(self.loop.reminders(), [])
        self.provider.events = [e for e in self.provider.events if e.id != 'one']
        self.loop.scan()
        self.assertNotIn('one', self.state['events'])
        self.assertEqual(self.state['reminders'], [])

    def test_no_slot_never_relaxes_protected_hours(self):
        ids = self.loop.plan({'kind': 'block', 'title': 'Long block', 'duration': '480', 'days': '1'})
        self.assertEqual(ids, [])

    def test_personal_block_has_no_attendees(self):
        key = self.plan(kind='block', attendees='')
        self.assertEqual(self.state['proposals'][key]['proposal']['attendees'], [])
        with self.assertRaisesRegex(ValueError, 'cannot include'):
            self.plan(kind='block')
