"""Calendar contracts; no Google SDK in the control loop."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start: str
    end: str
    etag: str = ''
    attendees: list[str] = field(default_factory=list)
    organizer: str = ''
    organizer_self: bool = False
    all_day: bool = False
    busy: bool = True
    response: str = 'accepted'
    link: str = ''
    recurring_id: str | None = None


@dataclass(frozen=True)
class Availability:
    busy: list[tuple[str, str]]
    unknown: list[str]


class CalendarTools(Protocol):
    account: str
    timezone: str
    state_identity: dict
    def list_events(self, start: str, end: str) -> list[Event]: ...
    def get_event(self, identifier: str) -> Event: ...
    def availability(self, start: str, end: str, attendees: list[str], ignore_event: str | None = None) -> Availability: ...
    def create_event(self, proposal: dict) -> str: ...
    def move_event(self, proposal: dict) -> str: ...
    def save_mail_draft(self, proposal: dict) -> str: ...


class ModelTools(Protocol):
    def draft_reschedule(self, event: Event, start: str, end: str, timezone: str, reason: str) -> str: ...
