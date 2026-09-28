import unittest
from unittest.mock import MagicMock
from providers.calendar import GoogleCalendarTools


class GoogleCalendarTests(unittest.TestCase):
    def setUp(self):
        self.service, self.gmail = MagicMock(), MagicMock()
        self.provider = GoogleCalendarTools(self.service, self.gmail, 'me@example.com', 'Asia/Kolkata')
        self.events = self.service.events.return_value
        self.raw = {'id': 'instance', 'summary': 'Planning', 'etag': 'v1', 'recurringEventId': 'series',
                    'start': {'dateTime': '2026-09-28T09:00:00+05:30'},
                    'end': {'dateTime': '2026-09-28T10:00:00+05:30'}, 'organizer': {'email': 'me@example.com', 'self': True}}

    def test_list_paginates_and_expands_recurrence(self):
        self.events.list.return_value.execute.side_effect = [
            {'items': [self.raw], 'nextPageToken': 'next'}, {'items': [dict(self.raw, id='cancelled', status='cancelled')]}]
        result = self.provider.list_events('2026-09-21T00:00:00Z', '2026-10-28T00:00:00Z')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].recurring_id, 'series')
        self.assertTrue(self.events.list.call_args.kwargs['singleEvents'])
        self.assertEqual(self.events.list.call_args.kwargs['pageToken'], 'next')

    def test_all_day_exclusive_end_and_declined_response(self):
        raw = dict(self.raw, start={'date': '2026-09-28'}, end={'date': '2026-09-29'},
                   attendees=[{'email': 'me@example.com', 'self': True, 'responseStatus': 'declined'}])
        event = self.provider.parse(raw)
        self.assertTrue(event.all_day)
        self.assertEqual(event.end, '2026-09-29T00:00:00+05:30')
        self.assertEqual(event.response, 'declined')

    def test_unshared_attendee_is_unknown_not_free(self):
        self.events.list.return_value.execute.return_value = {'items': []}
        self.service.freebusy.return_value.query.return_value.execute.return_value = {
            'calendars': {'alex@example.com': {'errors': [{'reason': 'notFound'}]}}}
        result = self.provider.availability('2026-09-28T00:00:00Z', '2026-09-29T00:00:00Z', ['alex@example.com'])
        self.assertEqual(result.unknown, ['alex@example.com'])
        self.provider.busy_calendars = ['alex@example.com']
        with self.assertRaisesRegex(ValueError, 'required personal calendar'):
            self.provider.availability('2026-09-28T00:00:00Z', '2026-09-29T00:00:00Z', [])

    def proposal(self):
        return {'id': 'abc123', 'event_id': 'instance', 'etag': 'v1', 'start': '2026-09-28T09:00:00+05:30',
                'end': '2026-09-28T10:00:00+05:30', 'timezone': 'Asia/Kolkata', 'title': 'Meet',
                'attendees': ['alex@example.com'], 'source_title': 'Old meeting', 'draft_body': 'Could we move our meeting?'}

    def test_reschedule_patches_only_instance_with_if_match(self):
        self.events.patch.return_value.execute.return_value = {'id': 'instance'}
        self.events.patch.return_value.headers = {}
        self.provider.move_event(self.proposal())
        request = self.events.patch.call_args.kwargs
        self.assertEqual(request['eventId'], 'instance')
        self.assertEqual(request['sendUpdates'], 'all')
        self.assertEqual(set(request['body']), {'start', 'end'})
        self.assertEqual(self.events.patch.return_value.headers['If-Match'], 'v1')
        self.events.patch.return_value.execute.assert_called_once_with(num_retries=0)

    def test_meeting_notifies_guests_block_does_not(self):
        self.events.insert.return_value.execute.return_value = {'id': 'new'}
        p = self.proposal()
        self.provider.create_event(p)
        self.assertEqual(self.events.insert.call_args.kwargs['sendUpdates'], 'all')
        self.assertEqual(self.events.insert.call_args.kwargs['body']['reminders'], {'useDefault': False})
        p['attendees'] = []
        self.provider.create_event(p)
        self.assertEqual(self.events.insert.call_args.kwargs['sendUpdates'], 'none')

    def test_mail_only_creates_draft(self):
        self.gmail.users.return_value.drafts.return_value.create.return_value.execute.return_value = {'id': 'draft'}
        self.assertEqual(self.provider.save_mail_draft(self.proposal()), 'draft')
        self.gmail.users.return_value.messages.return_value.send.assert_not_called()
        self.gmail.users.return_value.drafts.return_value.send.assert_not_called()
