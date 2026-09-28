"""Google Calendar operations and new Gmail drafts; no email-send endpoint."""
import base64
import hashlib
import json
import os
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from agents.calendar.tools import Event, Availability


class GoogleCalendarTools:
    def __init__(self, service, gmail, account, timezone, calendar_id='primary', busy_calendars=None):
        self.service, self.gmail, self.account, self.timezone = service, gmail, account, timezone
        self.calendar_id = calendar_id
        self.busy_calendars = busy_calendars or []
        self.state_identity = {'account': account.casefold(), 'calendar': calendar_id,
                               'busy_calendars': sorted(self.busy_calendars)}
        self.state_key = hashlib.sha256(json.dumps(self.state_identity, sort_keys=True).encode()).hexdigest()[:16]

    @classmethod
    def connect(cls, progress=lambda message: None):
        from googleapiclient.discovery import build
        from .calendar_auth import credentials, save_credentials
        creds = credentials(progress)
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        progress('Checking calendar account and timezone…')
        primary = cls.execute(service.calendars().get(calendarId='primary'))
        account = primary['id']
        expected = os.environ.get('CALENDAR_ACCOUNT', '').strip()
        if expected and expected.casefold() != account.casefold():
            raise ValueError('Calendar account differs from CALENDAR_ACCOUNT; token not replaced')
        calendar_id = os.environ.get('CALENDAR_ID') or 'primary'
        selected = primary if calendar_id == 'primary' else cls.execute(service.calendars().get(calendarId=calendar_id))
        save_credentials(creds)
        return cls(service, build('gmail', 'v1', credentials=creds, cache_discovery=False), account,
                   selected['timeZone'], calendar_id,
                   [s.strip() for s in os.environ.get('CALENDAR_BUSY_CALENDARS', '').split(',') if s.strip()])

    @staticmethod
    def execute(request):
        try:
            return request.execute(num_retries=0)
        except Exception as error:
            status = getattr(getattr(error, 'resp', None), 'status', None)
            raise RuntimeError(f'Google Calendar/draft request failed (HTTP {status or "unknown"}). '
                               'Check API access; refresh if the event changed.') from None

    def parse(self, raw):
        def date(value):
            if 'dateTime' in value:
                parsed = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(value.get('timeZone', self.timezone)))
                return parsed.isoformat()
            return datetime.fromisoformat(value['date']).replace(tzinfo=ZoneInfo(self.timezone)).isoformat()
        people = raw.get('attendees', [])
        me = next((a for a in people if a.get('self')), {})
        organizer = raw.get('organizer', {})
        return Event(raw['id'], raw.get('summary', '(Untitled event)'), date(raw['start']), date(raw['end']),
                     raw.get('etag', ''), [a['email'] for a in people if a.get('email') and a.get('responseStatus') != 'declined'],
                     organizer.get('email', ''), organizer.get('self', False), 'date' in raw['start'],
                     raw.get('transparency', 'opaque') != 'transparent', me.get('responseStatus', 'accepted'),
                     raw.get('htmlLink', ''), raw.get('recurringEventId'))

    def list_events(self, start, end):
        events, token = [], None
        while True:
            result = self.execute(self.service.events().list(calendarId=self.calendar_id, timeMin=start, timeMax=end,
                singleEvents=True, orderBy='startTime', showDeleted=False, maxResults=250, pageToken=token))
            events += [self.parse(e) for e in result.get('items', []) if e.get('status') != 'cancelled']
            token = result.get('nextPageToken')
            if not token:
                return events

    def get_event(self, identifier):
        raw = self.execute(self.service.events().get(calendarId=self.calendar_id, eventId=identifier))
        if raw.get('status') == 'cancelled':
            raise ValueError('This event was cancelled; refresh Calendar')
        return self.parse(raw)

    def availability(self, start, end, attendees, ignore_event=None):
        events = self.list_events(start, end)
        busy = [(e.start, e.end) for e in events if e.busy and e.response != 'declined' and e.id != ignore_event]
        required = {self.account if i == 'primary' else i for i in self.busy_calendars}
        own_ids = {self.calendar_id, self.account} if self.calendar_id == 'primary' else {self.calendar_id}
        ids = [i for i in dict.fromkeys(sorted(required) + attendees) if i not in own_ids]
        unknown = []
        if ids:
            if len(ids) > 50:
                raise ValueError('At most 50 external calendars can be checked at once')
            result = self.execute(self.service.freebusy().query(body={'timeMin': start, 'timeMax': end,
                'timeZone': self.timezone, 'items': [{'id': i} for i in ids]}))
            for identifier in ids:
                value = result.get('calendars', {}).get(identifier)
                if value is None or value.get('errors'):
                    if identifier in required:
                        raise ValueError('A required personal calendar is unavailable; cannot verify a safe slot')
                    unknown.append(identifier)
                else:
                    busy += [(b['start'], b['end']) for b in value.get('busy', [])]
        return Availability(busy, unknown)

    @staticmethod
    def times(proposal):
        return {'start': {'dateTime': proposal['start'], 'timeZone': proposal['timezone']},
                'end': {'dateTime': proposal['end'], 'timeZone': proposal['timezone']}}

    def create_event(self, proposal):
        body = self.times(proposal) | {'id': proposal['id'], 'summary': proposal['title'],
            'attendees': [{'email': email} for email in proposal['attendees']],
            'reminders': {'useDefault': False}, 'transparency': 'opaque'}
        result = self.execute(self.service.events().insert(calendarId=self.calendar_id, body=body,
                              sendUpdates='all' if proposal['attendees'] else 'none'))
        return result['id']

    def move_event(self, proposal):
        if not proposal['etag']:
            raise ValueError('No event version available; refresh before rescheduling')
        # Patch the selected instance only. Never update the recurring series master.
        request = self.service.events().patch(calendarId=self.calendar_id, eventId=proposal['event_id'],
                                               body=self.times(proposal), sendUpdates='all')
        request.headers['If-Match'] = proposal['etag']
        return self.execute(request)['id']

    def save_mail_draft(self, proposal):
        message = EmailMessage()
        message['From'], message['To'] = self.account, ', '.join(proposal['attendees'])
        message['Subject'] = 'Reschedule request: ' + proposal['source_title']
        message.set_content(proposal['draft_body'])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self.execute(self.gmail.users().drafts().create(userId='me', body={'message': {'raw': raw}}))['id']
