"""Calendar TUI adapter. Provider calls run in the shared background worker."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from queue import Empty, SimpleQueue
from time import monotonic
from zoneinfo import ZoneInfo
from agents.calendar.agent import open_agent
from agents.calendar.control_loop import new_state, fingerprint
from agents.calendar.scheduling import Preferences, instant
from agents.calendar.tool_implementations import DemoCalendarTools, JsonModelTools, demo_completion


class CalendarTab:
    name = 'Calendar agent'
    toolbar = [('c', 'Connect'), ('s', 'Scan'), ('f', 'Plan'), ('h', 'Hours')]
    action_messages = {'c': 'Connecting Calendar…', 's': 'Reviewing calendar commitments…',
                       'f': 'Finding considerate meeting times…', 'r': 'Finding reschedule options…',
                       'h': 'Saving preferred hours…', 'd': 'Drafting a reschedule request…'}
    footer = '↑↓ Select · F Plan · R Reschedule · D Draft · B Past week · L Activity'
    connection_label = 'Calendar'
    empty_label = 'No commitments.'
    wide_list = True

    def __init__(self, state_path, live=False, calendar=None):
        self.state_path, self.live = state_path, live or calendar is not None
        self.calendar = calendar if self.live else DemoCalendarTools()
        if self.calendar is None:
            self.toolbar = [('c', 'Connect')]
        self.state = new_state()
        self.last_scan = None
        self.updates = SimpleQueue()
        self.next_scan = monotonic() + 300
        self.next_reminder_check = 0

    @property
    def connection(self):
        return self.calendar

    def publish_checkpoint(self, state):
        self.updates.put(deepcopy(state))

    def apply(self, state):
        self.state = deepcopy(state)
        self.last_scan = self.state.get('last_scan')

    def consume_updates(self):
        latest = None
        while True:
            try:
                latest = self.updates.get_nowait()
            except Empty:
                break
        if latest is not None:
            self.apply(latest)
        return latest is not None

    def open(self, progress=lambda message: None):
        if self.calendar is None:
            raise ValueError('Connect Calendar first')
        from providers.openrouter import complete_json
        return open_agent(self.state_path, self.calendar,
                          JsonModelTools(complete_json if self.live else demo_completion),
                          progress, self.publish_checkpoint)

    def refresh(self):
        if self.calendar:
            with self.open() as agent:
                self.apply(agent.state)

    def preferences(self):
        return Preferences.from_dict(self.state['preferences'], self.calendar.timezone if self.calendar else 'Asia/Kolkata')

    def formatted(self, value):
        return instant(value).astimezone(ZoneInfo(self.preferences().timezone)).strftime('%a %d %b %H:%M %Z')

    def rows(self, view='signal'):
        now = datetime.now(timezone.utc)
        entries = []
        if view != 'backlog':
            for identifier, item in self.state['proposals'].items():
                if view == 'activity' or item['status'] in ('pending', 'approved', 'uncertain'):
                    p = item['proposal']
                    entries.append(('p:' + identifier, f"{p['kind'].upper()} · {p['title']} · {self.formatted(p['start'])}"))
        for identifier, item in sorted(self.state['events'].items(), key=lambda pair: pair[1]['event']['start']):
            event = item['event']
            past = instant(event['end']) <= now
            if (view == 'backlog' and past) or view == 'activity' or (view == 'signal' and not past):
                label = 'REVIEW' if item['issues'] else 'PAST' if past else 'UPCOMING'
                entries.append(('e:' + identifier, f"{'! ' if item['issues'] else ''}{event['title']} · {self.formatted(event['start'])}"))
        return entries

    def timeframe(self):
        if not self.last_scan:
            return 'Past week + coming commitments', 'Scan reads 7 days back and 30 days ahead.'
        zone = ZoneInfo(self.preferences().timezone)
        start, end = instant(self.last_scan['from']).astimezone(zone), instant(self.last_scan['through']).astimezone(zone)
        checked = instant(self.last_scan['finished_at']).astimezone(zone)
        return ('Commitments · ' + self.preferences().timezone,
                f"Window {start:%d %b} – {end:%d %b} · checked {checked:%H:%M} · refresh 5 min")

    def details(self, identifier):
        if identifier is None:
            return ['Calendar is not connected.'] if not self.calendar else [
                'Scan to review commitments. F finds a time, schedules a meeting, or blocks time.',
                'B shows the past week. H sets working hours, meals, sleep and reminders.']
        if identifier.startswith('e:'):
            item = self.state['events'][identifier[2:]]
            e = item['event']
            lines = [e['title'], self.formatted(e['start']), 'Until ' + self.formatted(e['end']),
                     'All-day event' if e['all_day'] else '', '', 'Attendees: ' + ', '.join(e['attendees']),
                     'Organizer: ' + e['organizer'], '']
            if instant(e['end']) <= datetime.now(timezone.utc):
                return lines + ['Past-week backlog. Review whether follow-up is needed; completion is not inferred.']
            lines += item['issues']
            if item['issues']:
                lines += ['', 'Would you like to reschedule? R finds alternatives.']
            lines += ['', 'R — Reschedule / request a reschedule',
                      'Organizer changes notify guests after approval.' if e['organizer_self'] else
                      'You are not the organizer; rescheduling creates a mail draft request.']
            return lines
        item = self.state['proposals'][identifier[2:]]
        p = item['proposal']
        lines = [p['title'], p['kind'].upper(), '', self.formatted(p['start']), 'Until ' + self.formatted(p['end']),
                 '', 'Attendees: ' + (', '.join(p['attendees']) or 'Only you'),
                 'Unknown availability: ' + ', '.join(p['unknown_availability']) if p['unknown_availability'] else 'Calendar availability checked.',
                 'Participant local hours: ' + (p['participant_timezone'] or 'not verified') if p['attendees'] else '', '',
                 'Working hours, meals, sleep, weekends and buffers respected.']
        if p['kind'] == 'mail':
            lines += ['', 'MAIL DRAFT', p['draft_body'] or 'Press D to generate a draft for this option.',
                      'Execution saves a Gmail draft only. Nothing is sent.']
        elif p['kind'] in ('meeting', 'reschedule'):
            lines += ['', 'Execution creates/updates the event and sends Google Calendar invitations/updates to guests.']
        elif p['kind'] == 'find':
            lines += ['', 'Find only: this option makes no calendar changes.']
        if item['status'] == 'uncertain':
            lines += ['', 'Provider result uncertain. Check Google Calendar/Gmail; automatic retry is blocked.']
        elif item['status'] == 'pending':
            lines += ['', 'Approve [A]   Reject [X]']
        elif item['status'] == 'approved':
            lines += ['', 'Approved. Execute [E]']
        return lines

    def audit_details(self, identifier):
        return self.details(identifier) + ['', 'Read-only scans and interval checks use no model calls.',
                                          'GLM is called only for an explicitly selected reschedule email draft.']

    def confirmation(self, action, identifier):
        if not identifier or not identifier.startswith('p:'):
            raise ValueError('Select a proposed time/action first')
        item = self.state['proposals'][identifier[2:]]
        if action in ('a', 'x') and item['status'] != 'pending':
            raise ValueError('Only pending options can be reviewed')
        if action == 'e' and item['status'] != 'approved':
            raise ValueError('Approve this exact option before executing')
        if action == 'x':
            return 'Reject this option?'
        effects = {'meeting': 'create the meeting and notify guests', 'block': 'create a personal time block',
                   'reschedule': 'move this occurrence and notify guests', 'mail': 'save this email draft; never send',
                   'find': 'acknowledge this suggested time; no calendar changes'}
        return ('Approve' if action == 'a' else 'Execute') + ': ' + effects[item['proposal']['kind']] + '?'

    def form_fields(self, action, identifier):
        if not self.calendar:
            raise ValueError('Connect Calendar first')
        prefs = self.preferences()
        if action == 'h':
            return [('timezone', 'Timezone', prefs.timezone), ('work_start', 'Work starts', prefs.work_start),
                    ('work_end', 'Work ends', prefs.work_end), ('weekdays', 'Weekdays (Mon=0 … Sun=6)', ','.join(map(str, prefs.weekdays))),
                    *[(name.lower(), name + ' (HH:MM-HH:MM)', '-'.join(values)) for name, values in prefs.protected.items()],
                    ('buffer_minutes', 'Buffer minutes', str(prefs.buffer_minutes)),
                    ('reminder_minutes', 'Reminder minutes', str(prefs.reminder_minutes))]
        if action not in ('f', 'r'):
            return None
        source = None
        if action == 'r':
            if not identifier or not identifier.startswith('e:'):
                raise ValueError('Select a future commitment to reschedule')
            source = self.state['events'][identifier[2:]]['event']
            if instant(source['end']) <= datetime.now(timezone.utc):
                raise ValueError('Past commitments are review-only; use Plan to schedule a follow-up')
        mode = ('reschedule' if source['organizer_self'] else 'mail') if source else 'find'
        return [('kind', 'Mode: find / meeting / block / reschedule / mail', mode),
                ('title', 'Title', source['title'] if source else ''),
                ('attendees', 'Attendee emails, comma-separated', ','.join(source['attendees']) if source else ''),
                ('duration', 'Duration minutes', str(int((instant(source['end']) - instant(source['start'])).total_seconds() / 60)) if source else '30'), ('earliest', 'Earliest local ISO date/time (blank = now)', ''),
                ('days', 'Search days ahead', '14'), ('participant_timezone', 'Participant timezone (optional IANA)', ''),
                ('reason', 'Reschedule reason (optional)', '')]

    def run(self, action, identifier=None, progress=lambda message: None, payload=None):
        if action == 'c':
            from providers.calendar import GoogleCalendarTools
            calendar = GoogleCalendarTools.connect(progress)
            path = self.state_path.parent / f'calendar-{calendar.state_key}.json'
            from providers.openrouter import complete_json
            with open_agent(path, calendar, JsonModelTools(complete_json), progress) as agent:
                agent.scan()
                state = deepcopy(agent.state)
            self.calendar, self.state_path, self.live = calendar, path, True
            self.toolbar = type(self).toolbar
            self.updates = SimpleQueue()
            self.apply(state)
            self.next_scan = monotonic() + 300
            return f'Connected Calendar: {calendar.account}. Upcoming commitments are monitored while the TUI runs.'
        displayed = self.state['proposals'].get(identifier[2:]) if identifier and identifier.startswith('p:') else None
        with self.open(progress) as agent:
            if action == 's':
                self.next_scan = monotonic() + 300
                agent.scan()
                result = 'Calendar refreshed: past 7 days and next 30 days.'
            elif action in ('f', 'r'):
                request = dict(payload or {})
                if action == 'r':
                    request['event_id'] = identifier[2:]
                ids = agent.plan(request)
                self.last_planned_ids = ids
                result = f'{len(ids)} suitable options. Choose one to review.' if ids else 'No suitable slots; adjust duration/window or hours.'
            elif action == 'h':
                values = dict(payload or {})
                values['weekdays'] = [int(v.strip()) for v in values['weekdays'].split(',')]
                values['buffer_minutes'], values['reminder_minutes'] = int(values['buffer_minutes']), int(values['reminder_minutes'])
                values['protected'] = {name: values.pop(name.lower()).split('-') for name in ('Lunch', 'Snack', 'Dinner', 'Sleep')}
                agent.state['preferences'] = asdict(Preferences.from_dict(values))
                agent.checkpoint()
                result = 'Preferred hours saved. Existing approved plans require review if these hours changed.'
            else:
                if not identifier or not identifier.startswith('p:'):
                    raise ValueError('Select a proposal first')
                key = identifier[2:]
                if displayed is None or fingerprint(displayed) != fingerprint(agent.state['proposals'].get(key)):
                    raise ValueError('Proposal changed; refresh and review again')
                if action == 'd':
                    agent.draft(key)
                    result = 'Draft generated locally. Review it before approving.'
                elif action in ('a', 'x'):
                    agent.review(key, action == 'a')
                    result = 'Approved. Execute separately.' if action == 'a' else 'Rejected.'
                elif action == 'e':
                    agent.execute(key)
                    result = 'Action completed. Reschedule email drafts were not sent.'
                    self.next_scan = monotonic()
                else:
                    raise ValueError('Unknown Calendar action')
            self.apply(agent.state)
        return result

    def poll_reminders(self):
        if not self.calendar or monotonic() < self.next_reminder_check:
            return []
        self.next_reminder_check = monotonic() + 10
        with self.open() as agent:
            due = agent.reminders()
            if due:
                self.apply(agent.state)
        return due

    def background_scan_due(self):
        return self.calendar is not None and monotonic() >= self.next_scan
