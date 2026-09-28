"""Pure interval math: timezone-aware slots, protected time, and event buffers."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field, asdict

UTC = timezone.utc


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('An explicit timezone is required')
    return result.astimezone(UTC)


def minutes(value):
    hour, minute = map(int, value.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Use HH:MM in 24-hour time')
    return hour * 60 + minute


@dataclass
class Preferences:
    timezone: str = 'Asia/Kolkata'
    work_start: str = '09:00'
    work_end: str = '18:00'
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    protected: dict = field(default_factory=lambda: {
        'Lunch': ['12:00', '13:00'], 'Snack': ['16:00', '16:30'],
        'Dinner': ['19:00', '20:00'], 'Sleep': ['22:00', '08:00']})
    buffer_minutes: int = 15
    reminder_minutes: int = 15

    def validate(self):
        ZoneInfo(self.timezone)
        if minutes(self.work_end) <= minutes(self.work_start):
            raise ValueError('Working hours must start and end on the same day')
        if not self.weekdays or any(type(d) is not int or d not in range(7) for d in self.weekdays):
            raise ValueError('Weekdays must be numbers 0–6, Monday–Sunday')
        for times in self.protected.values():
            if len(times) != 2 or minutes(times[0]) == minutes(times[1]):
                raise ValueError('Each protected period requires distinct start/end times')
        if not 0 <= self.buffer_minutes <= 120 or not 0 <= self.reminder_minutes <= 1440:
            raise ValueError('Invalid buffer or reminder minutes')
        return self

    @classmethod
    def from_dict(cls, value, zone='Asia/Kolkata'):
        return cls(**({'timezone': zone} | value)).validate()


def overlaps(start, end, other_start, other_end):
    return start < other_end and end > other_start


def comfortable(start, end, prefs):
    zone = ZoneInfo(prefs.timezone)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    if local_start.weekday() not in prefs.weekdays or local_start.date() != local_end.date():
        return False
    def clock(day, value):
        return datetime.combine(day, datetime.min.time(), zone) + timedelta(minutes=minutes(value))
    day = local_start.date()
    if start < clock(day, prefs.work_start).astimezone(UTC) or end > clock(day, prefs.work_end).astimezone(UTC):
        return False
    for previous in (day - timedelta(days=1), day):
        for begin, finish in prefs.protected.values():
            a, b = clock(previous, begin), clock(previous, finish)
            if minutes(finish) < minutes(begin):
                b += timedelta(days=1)
            if overlaps(start, end, a.astimezone(UTC), b.astimezone(UTC)):
                return False
    return True


def find_slots(start, end, duration, availability, prefs, participant_timezone=None, limit=3):
    if type(duration) is not int or not 15 <= duration <= 480:
        raise ValueError('Meeting duration must be 15–480 minutes')
    if end <= start or end - start > timedelta(days=31):
        raise ValueError('Search window must be positive and at most 31 days')
    other = Preferences.from_dict(asdict(prefs) | {'timezone': participant_timezone}) if participant_timezone else None
    busy = [(instant(a), instant(b)) for a, b in availability.busy]
    buffer = timedelta(minutes=prefs.buffer_minutes)
    # Walk real instants so DST cannot create nonexistent local slots.
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    if cursor.minute % 15:
        cursor += timedelta(minutes=15 - cursor.minute % 15)
    slots = []
    while cursor + timedelta(minutes=duration) <= end and len(slots) < limit:
        finish = cursor + timedelta(minutes=duration)
        if (comfortable(cursor, finish, prefs) and (not other or comfortable(cursor, finish, other))
                and not any(overlaps(cursor - buffer, finish + buffer, a, b) for a, b in busy)):
            slots.append((cursor.isoformat(), finish.isoformat()))
            cursor = finish  # Distinct options rather than near-identical overlapping slots.
        else:
            cursor += timedelta(minutes=15)
    return slots
