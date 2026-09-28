"""Tool contracts. Providers implement these; the control loop knows only these types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class InboxPage:
    message_ids: list[str]
    next_cursor: str | None


@dataclass(frozen=True)
class MessageTitle:
    id: str
    sender: str
    subject: str
    received_at: str | None = None


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    context_incomplete: bool = False
    received_at: str | None = None
    actual_sender: str | None = None
    sender_frequency: dict | None = None


@dataclass(frozen=True)
class Assessment:
    summary: str
    needs_reply: bool
    handling_score: float
    reason: str
    draft_body: str | None
    truncated: bool = False
    category: str = "needs_attention"


@dataclass(frozen=True)
class Draft:
    message_id: str
    thread_id: str
    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class Classification:
    category: Literal["urgent_reply", "reply", "newsletter", "marketing", "informational", "needs_attention"]
    confidence: float
    handling_score: float
    truncated: bool = False


class ClassifierTools(Protocol):
    def match_topics(self, message: Message, include: list[str], exclude: list[str]) -> str: ...
    def classify(self, message: Message) -> Classification: ...


class GmailTools(Protocol):
    def read_titles(self, message_ids: list[str]) -> list[MessageTitle]: ...
    def read_title(self, message_id: str) -> MessageTitle: ...
    def list_inbox(self, cursor: str | None, limit: int) -> InboxPage: ...
    def read_message(self, message_id: str) -> Message: ...
    def save_draft(self, draft: Draft) -> str:
        """Create a Gmail draft, never send; return its provider ID."""
        ...


class ModelTools(Protocol):
    def analyze_titles(self, titles: list[MessageTitle]) -> list[dict]: ...
    def assess(self, message: Message) -> Assessment:
        """One model call for triage, ability estimate, and optional reply text."""
        ...


ReviewStatus = Literal["pending", "approved", "rejected", "executing", "done", "uncertain"]
