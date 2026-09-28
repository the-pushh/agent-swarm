"""Deterministic commitments, slot proposals, reminders, and human approval gates."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import uuid
from .scheduling import Preferences, instant, overlaps, comfortable, find_slots


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def new_state():
    return {'events': {}, 'proposals': {}, 'notified': {}, 'reminders': [], 'preferences': {}}


class CalendarLoop:
    def __init__(self, calendar, model, state, checkpoint, progress=lambda message: None, now=None):
        self.calendar, self.model, self.state = calendar, model, state
        self.checkpoint, self.progress = checkpoint, progress
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def preferences(self):
        return Preferences.from_dict(self.state['preferences'], self.calendar.timezone)

    def scan(self):
        now = self.now()
        start, end = now-timedelta(days=7), now+timedelta(days=30)
        self.progress('Calendar: reading past 7 days and next 30 days, including recurring instances…')
        events = self.calendar.list_events(start.isoformat(), end.isoformat())
        events = [e for e in events if e.response != 'declined']
        future = [e for e in events if instant(e.end) > now]
        results = {}
        for event in events:
            issues = []
            if instant(event.end) > now:
                if event.busy:
                    conflicts = [other.title for other in future if other.id != event.id and other.busy and
                                 overlaps(instant(event.start), instant(event.end), instant(other.start), instant(other.end))]
                    if conflicts:
                        issues.append('Overlaps: ' + ', '.join(conflicts))
                if not event.all_day and not comfortable(instant(event.start), instant(event.end), self.preferences):
                    issues.append('Outside preferred hours or during protected meal/sleep time')
                if event.response in ('needsAction', 'tentative'):
                    issues.append('Your attendance is not confirmed')
            results[event.id] = {'event': asdict(event), 'issues': issues,
                                 'backlog': instant(event.end) <= now}
        # Replacing the snapshot removes cancelled/moved/declined events from reminders.
        self.state['events'] = results
        self.state['last_scan'] = {'started_at': now.isoformat(), 'finished_at': self.now().isoformat(),
                                   'from': start.isoformat(), 'through': end.isoformat(), 'count': len(results)}
        self.state['reminders'] = [r for r in self.state['reminders'] if r['event_id'] in results and
                                   results[r['event_id']]['event']['start'] == r['event_start']]
        self.progress(f'Calendar: {len(results)} commitments loaded. Conflicts checked locally; no model calls.')
        self.checkpoint()

    def plan(self, request):
        kind = request.get('kind', 'find').strip().lower()
        if kind not in ('find', 'meeting', 'block', 'reschedule', 'mail'):
            raise ValueError('Mode must be find, meeting, block, reschedule, or mail')
        prefs = self.preferences
        source = self.calendar.get_event(request['event_id']) if request.get('event_id') else None
        if kind in ('reschedule', 'mail') and source is None:
            raise ValueError('Select the commitment to reschedule first')
        if source and source.all_day:
            raise ValueError('Use Google Calendar to move all-day events; this planner handles timed meetings')
        if kind == 'reschedule' and not source.organizer_self:
            raise ValueError('You are not the organizer. Choose mail to request a reschedule.')
        title = (request.get('title') or (source.title if source else '')).strip()
        if not title or len(title) > 300:
            raise ValueError('Enter a meeting/block title (up to 300 characters)')
        attendees = [address.strip().casefold() for address in request.get('attendees', '').split(',') if address.strip()]
        if source and kind in ('reschedule', 'mail'):
            attendees = list(dict.fromkeys(source.attendees + ([source.organizer] if source.organizer else [])))
        attendees = [a for a in dict.fromkeys(attendees) if a.casefold() != self.calendar.account.casefold()]
        if len(attendees) > 40 or any(not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+', a) for a in attendees):
            raise ValueError('Use up to 40 explicit attendee email addresses')
        if kind == 'meeting' and not attendees:
            raise ValueError('Enter at least one attendee, or choose block')
        if kind == 'block' and attendees:
            raise ValueError('A personal time block cannot include attendees')
        if kind == 'mail' and not attendees:
            raise ValueError('No known attendee/organizer addresses for a reschedule draft')
        default_duration = int((instant(source.end) - instant(source.start)).total_seconds() / 60) if source else 30
        duration, days = int(request.get('duration') or default_duration), int(request.get('days', 14))
        if not 1 <= days <= 30:
            raise ValueError('Search 1–30 days ahead')
        earliest = request.get('earliest', '').strip()
        start = datetime.fromisoformat(earliest) if earliest else self.now()
        if start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(prefs.timezone))
            if start.astimezone(timezone.utc).astimezone(ZoneInfo(prefs.timezone)).replace(tzinfo=None) != datetime.fromisoformat(earliest):
                raise ValueError('That local time does not exist due to a timezone clock change')
        start = max(start.astimezone(timezone.utc), self.now())
        end = start + timedelta(days=days)
        other_zone = request.get('participant_timezone', '').strip() or None
        if other_zone:
            ZoneInfo(other_zone)
        self.progress('Checking your live calendar and shared attendee availability…')
        availability = self.calendar.availability((start-timedelta(minutes=prefs.buffer_minutes)).isoformat(),
                                                  (end+timedelta(minutes=prefs.buffer_minutes)).isoformat(), attendees,
                                                  source.id if kind == 'reschedule' else None)
        slots = find_slots(start, end, duration, availability, prefs, other_zone)
        if not slots:
            self.progress('No suitable slot found within the requested window; no protected hours were relaxed.')
            return []
        group = uuid.uuid4().hex
        identifiers = []
        for begin, finish in slots:
            proposal = {'id': uuid.uuid4().hex, 'group': group, 'kind': kind, 'title': title,
                        'start': begin, 'end': finish, 'timezone': prefs.timezone, 'attendees': attendees,
                        'unknown_availability': availability.unknown, 'participant_timezone': other_zone,
                        'event_id': source.id if source else None, 'etag': source.etag if source else None,
                        'source_title': source.title if source else None, 'source_start': source.start if source else None,
                        'preferences': asdict(prefs), 'reason': request.get('reason', '').strip(), 'draft_body': None}
            # Draft only one selected option; no GLM calls for alternate slots or read-only scans.
            self.state['proposals'][proposal['id']] = {'proposal': proposal, 'status': 'pending',
                                                       'approved_fingerprint': None, 'provider_id': None}
            identifiers.append(proposal['id'])
        self.progress(f'{len(slots)} suitable options found. Choose one; nothing has been booked.')
        self.checkpoint()
        return identifiers

    def draft(self, identifier):
        item = self.state['proposals'][identifier]
        proposal = item['proposal']
        if item['status'] != 'pending' or proposal['kind'] != 'mail':
            raise ValueError('Select a pending reschedule mail option')
        event = self.calendar.get_event(proposal['event_id'])
        if event.etag != proposal['etag']:
            raise ValueError('The source event changed. Refresh and plan again.')
        self.progress('GLM is drafting a reschedule request for this selected time only…')
        proposal['draft_body'] = self.model.draft_reschedule(event, proposal['start'], proposal['end'],
                                                            proposal['timezone'], proposal['reason'])
        self.checkpoint()

    def review(self, identifier, approve):
        item = self.state['proposals'][identifier]
        if item['status'] != 'pending':
            raise ValueError('Only pending proposals can be reviewed')
        if approve and item['proposal']['kind'] == 'mail' and not item['proposal']['draft_body']:
            raise ValueError('Generate and review the draft with D before approving')
        item['status'] = 'approved' if approve else 'rejected'
        item['approved_fingerprint'] = fingerprint(item['proposal']) if approve else None
        if approve:
            for other in self.state['proposals'].values():
                if other is not item and other['proposal']['group'] == item['proposal']['group'] and other['status'] == 'pending':
                    other['status'] = 'rejected'  # Alternate slots are not additional meetings.
        self.checkpoint()

    def execute(self, identifier):
        item = self.state['proposals'][identifier]
        proposal = item['proposal']
        if item['status'] != 'approved' or fingerprint(proposal) != item['approved_fingerprint']:
            raise ValueError('Approve this exact proposal before executing')
        if proposal['kind'] == 'find':
            item['status'] = 'done'
            self.checkpoint()
            return
        if instant(proposal['start']) <= self.now():
            raise ValueError('Proposed time has passed; find another slot')
        if proposal['preferences'] != asdict(self.preferences):
            raise ValueError('Your scheduling preferences changed; plan and review again')
        if proposal['event_id']:
            event = self.calendar.get_event(proposal['event_id'])
            if event.etag != proposal['etag']:
                raise ValueError('Event changed since review; refresh and plan again')
        self.progress('Rechecking availability immediately before the approved action…')
        prefs = self.preferences
        start, end = instant(proposal['start']), instant(proposal['end'])
        availability = self.calendar.availability((start-timedelta(minutes=prefs.buffer_minutes)).isoformat(),
                                                  (end+timedelta(minutes=prefs.buffer_minutes)).isoformat(), proposal['attendees'],
                                                  proposal['event_id'] if proposal['kind'] == 'reschedule' else None)
        if set(availability.unknown) - set(proposal['unknown_availability']):
            raise ValueError('More attendee availability is unknown than when approved; review again')
        if not find_slots(start, end, int((end-start).total_seconds()/60), availability, prefs,
                          proposal['participant_timezone'], limit=1):
            raise ValueError('This slot is no longer available; plan again')
        item['status'] = 'executing'
        self.checkpoint()
        try:
            if proposal['kind'] == 'mail':
                result = self.calendar.save_mail_draft(proposal)
            elif proposal['kind'] == 'reschedule':
                result = self.calendar.move_event(proposal)
            else:
                result = self.calendar.create_event(proposal)
            if not isinstance(result, str) or not result:
                raise ValueError('Provider returned no identifier')
        except Exception:
            item['status'] = 'uncertain'
            self.checkpoint()
            raise
        item.update(status='done', provider_id=result)
        self.checkpoint()
        self.progress('Approved action completed. Reschedule email drafts are never sent.')

    def reminders(self):
        now = self.now()
        due = []
        for identifier, item in self.state['events'].items():
            event = item['event']
            start, end = instant(event['start']), instant(event['end'])
            if not event['busy'] or end <= now:
                continue
            if event['all_day']:
                local = start.astimezone(ZoneInfo(self.preferences.timezone))
                alert_at = local.replace(hour=9, minute=0).astimezone(timezone.utc)
            else:
                alert_at = start - timedelta(minutes=self.preferences.reminder_minutes)
            key = identifier + ':' + event['start']
            if alert_at <= now < end and key not in self.state['notified']:
                reminder = {'event_id': identifier, 'event_start': event['start'], 'title': event['title'],
                            'at': now.isoformat(), 'text': f"{event['title']} · {start.astimezone(ZoneInfo(self.preferences.timezone)).strftime('%a %H:%M %Z')}"}
                self.state['notified'][key] = now.isoformat()
                self.state['reminders'].append(reminder)
                due.append(reminder)
        if due:
            self.state['reminders'] = self.state['reminders'][-100:]
            self.checkpoint()
        return due
