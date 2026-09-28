"""Deterministic policy. No provider SDK, prompts, or CLI concerns here."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Callable
from time import monotonic

from .tools import Assessment, Classification, ClassifierTools, Draft, GmailTools, ModelTools
from .sender_frequency import frequency_for
from .screening import NewsletterFilters, screen_newsletter, sender_matches
from .preferences import load_titles


def new_state() -> dict:
    return {"cursor": None, "items": {}}


def fingerprint(draft: dict) -> str:
    return hashlib.sha256(json.dumps(draft, sort_keys=True).encode()).hexdigest()


def glm_reasons(classification: Classification | None, threshold: float = .85) -> list[str]:
    """Explain the same gates used to decide whether GLM is necessary."""
    if classification is None:
        return ["no classifier configured"]
    reasons = []
    if classification.category not in ("marketing", "informational"):
        reasons.append(f"Jev category is {classification.category}")
    if classification.confidence < .90:
        reasons.append(f"confidence {classification.confidence:.2f} < 0.90")
    if classification.truncated:
        reasons.append("input was truncated or contains unread content")
    return reasons


class EmailLoop:
    def __init__(self, gmail: GmailTools, model: ModelTools, state: dict,
                 checkpoint: Callable[[], None], threshold: float = .85,
                 classifier: ClassifierTools | None = None,
                 progress: Callable[[str], None] = lambda message: None):
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be in [0, 1]")
        self.gmail, self.model = gmail, model
        self.state, self.checkpoint, self.threshold = state, checkpoint, threshold
        self.classifier = classifier
        self.progress = progress

    def measured(self, stage, operation):
        started = monotonic()
        try:
            return operation()
        finally:
            duration = monotonic() - started
            timing = self.state["last_scan"].setdefault("timings", {}).setdefault(stage, {"calls": 0, "seconds": 0})
            timing["calls"] += 1
            timing["seconds"] = round(timing["seconds"] + duration, 3)
            self.progress(f"{stage}: {duration:.1f}s")

    @staticmethod
    def needs_assessment(item, filters):
        if not item:
            return True
        if item["status"] != "pending":
            return False
        if item.get("policy_version") != 2 or not item.get("filter_revision"):
            return True
        if item["filter_revision"] == filters.revision:
            return False
        return (item.get("assessment", {}).get("category") == "newsletter"
                or item.get("assessment_source") == "sender_filter"
                or sender_matches(item.get("actual_sender") or item["sender"], filters.exclude_senders))

    def save_sender_exclusion(self, title, filters, frequency):
        reason = "Sender explicitly excluded by you; body, Jev and GLM skipped. Gmail unchanged."
        assessment = Assessment(title.subject, False, 1, reason, None, category="informational")
        self.state["items"][title.id] = {
            "assessment": asdict(assessment), "classification": None,
            "assessment_source": "sender_filter", "assessment_reason": reason,
            "policy_version": 2, "filter_revision": filters.revision,
            "screening": {"decision": "exclude", "reason": reason}, "signal_kind": "informational",
            "subject": title.subject, "sender": title.sender, "actual_sender": title.sender,
            "sender_frequency": frequency, "received_at": title.received_at,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "verdicts": [{"step": "Sender filter", "verdict": reason}],
            "route": "agent_can_handle", "draft": None, "status": "pending",
            "approved_fingerprint": None, "provider_draft_id": None,
        }

    def scan(self, batch_size: int = 50) -> list[str]:
        if not 1 <= batch_size <= 50:
            raise ValueError("batch_size must be between 1 and 50")
        self._scan_started = monotonic()
        self.state["last_scan"] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "status": "running", "processed": 0,
            "query": getattr(self.gmail, "query", None),
            "page_number": self.state.get("completed_pages", 0) + 1 if self.state["cursor"] else 1,
            "reviewed": 0, "page_size": None, "timings": {},
        }
        self.checkpoint()
        try:
            processed = self._scan_page(batch_size)
        except Exception:
            self.state["last_scan"].update(status="failed", finished_at=datetime.now(timezone.utc).isoformat())
            self.checkpoint()
            raise
        self.state["last_scan"].update(status="complete", finished_at=datetime.now(timezone.utc).isoformat(),
                                       processed=len(processed), more_pages=self.state["cursor"] is not None)
        self.state["last_scan"]["elapsed_seconds"] = round(monotonic() - self._scan_started, 3)
        self.state["completed_pages"] = self.state["last_scan"]["page_number"]
        self.checkpoint()
        return processed

    def _scan_page(self, batch_size: int) -> list[str]:
        """Read one page; checkpoint each success, advance cursor only after all succeed.

        At the end, the next scan starts a fresh pass to discover new arrivals.
        Already assessed message IDs cost no further read/model calls.
        """
        self.progress(f"Fetching inbox page (up to {batch_size} emails)…")
        page = self.measured("Gmail list", lambda: self.gmail.list_inbox(self.state["cursor"], batch_size))
        if len(page.message_ids) > batch_size:
            raise ValueError("Provider exceeded requested page size")
        self.state["last_scan"]["page_size"] = len(page.message_ids)
        self.checkpoint()
        processed = []
        self.progress(f"Received {len(page.message_ids)} emails; checking saved assessments.")
        observations = self.state.get("sender_observations", {}).copy()
        for identifier, item in self.state["items"].items():
            if item.get("sender"):
                observations.setdefault(identifier, {"sender": item.get("actual_sender") or item["sender"],
                                                      "received_at": item.get("received_at")})
        filters = NewsletterFilters.from_dict(self.state.get("newsletter_filters", {}))
        pending = [identifier for identifier in page.message_ids
                   if self.needs_assessment(self.state["items"].get(identifier), filters)]
        self.progress(f"Reading metadata for {len(pending)} unprocessed emails; applying sender exclusions before bodies…")
        titles = {title.id: title for title in self.measured("Gmail metadata", lambda: load_titles(self, pending))}
        for title in titles.values():
            observations[title.id] = {"sender": title.sender, "received_at": title.received_at}
        self.state["sender_observations"] = observations
        self.checkpoint()
        self.progress("Sender frequencies counted from headers; body assessment now streams one email at a time.")
        for index, message_id in enumerate(page.message_ids, 1):
            if message_id not in titles:
                self.state["last_scan"]["reviewed"] = index
                self.checkpoint()
                continue
            step = f"Email {index}/{len(page.message_ids)}"
            title = titles[message_id]
            frequency = frequency_for(title.sender, observations)
            if sender_matches(title.sender, filters.exclude_senders):
                self.progress(f"{step}: excluded sender; no body read or model calls.")
                self.save_sender_exclusion(title, filters, frequency)
                processed.append(message_id)
                self.state["last_scan"].update(processed=len(processed), reviewed=index)
                self.checkpoint()
                continue
            self.progress(f"{step}: reading message body…")
            message = self.measured("Gmail body", lambda: self.gmail.read_message(message_id))
            if message.id != message_id:
                raise ValueError("Provider returned a different message")
            message = replace(message, sender_frequency=frequency)
            if self.classifier:
                self.progress(f"{step}: Jev is classifying and scoring handling ability…")
            classification = self.measured("Jev classification", lambda: self.classifier.classify(message)) if self.classifier else None
            reasons = glm_reasons(classification, self.threshold)
            screening = None
            if classification and classification.category == "newsletter":
                self.progress(f"{step}: screening newsletter against topic and sender rules…")
                screening = self.measured("Newsletter filter", lambda: screen_newsletter(message, filters, self.classifier))
                self.progress(f"{step}: newsletter {screening['decision']}: {screening['reason']}")
            blocked = screening is not None and screening["decision"] != "include"
            skip_glm = not reasons or blocked
            if blocked:
                reasons = [screening["reason"]]
                assessment = Assessment(message.subject, False, 0, screening["reason"], None, category="newsletter")
            elif skip_glm:
                self.progress(f"{step}: {classification.category}; filtering noise without a GLM call.")
                assessment = Assessment(
                    message.subject, False, classification.handling_score,
                    f"Jev classified this as {classification.category}; filtered from Signal. Gmail unchanged.",
                    None, category=classification.category)
            else:
                self.progress(f"{step}: calling GLM because " + "; ".join(reasons) + ".")
                self.progress(f"{step}: GLM is assessing whether a reply is needed…")
                assessment = self.measured("GLM assessment", lambda: self.model.assess(message))
            if assessment.category == "newsletter" and screening is None:
                screening = self.measured("Newsletter filter", lambda: screen_newsletter(message, filters, self.classifier))
            if assessment.draft_body:
                self.progress(f"{step}: reply proposed; draft text generated locally for your review.")
            elif assessment.needs_reply:
                self.progress(f"{step}: reply needed, but no draft proposed; human input required.")
            else:
                self.progress(f"{step}: no reply needed; no draft created.")
            self.progress(f"{step}: assessment validated; preparing the local proposal.")
            ready = (assessment.handling_score >= self.threshold and not assessment.truncated
                     and (not assessment.needs_reply or assessment.draft_body is not None))
            if classification and (classification.category == "needs_attention" or classification.truncated):
                ready = False
            kind = assessment.category
            if assessment.truncated or (classification and classification.truncated):
                kind = "needs_attention"
            elif classification and classification.category in ("urgent_reply", "reply", "needs_attention") and kind in ("marketing", "informational"):
                kind = "needs_attention"  # A disagreement must not hide a potentially urgent email.
            if blocked:
                kind = "newsletter"
            draft = None
            if assessment.draft_body:
                subject = message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
                draft = asdict(Draft(message.id, message.thread_id, message.sender,
                                     subject, assessment.draft_body))
            self.state["items"][message_id] = {
                "assessment": asdict(assessment),
                "classification": asdict(classification) if classification else None,
                "assessment_source": "screening" if blocked else "jev" if skip_glm else "glm",
                "assessment_reason": "; ".join(reasons) if reasons else "Confident noise classification; GLM skipped.",
                "policy_version": 2,
                "filter_revision": filters.revision,
                "screening": screening,
                "signal_kind": kind,
                "subject": message.subject,
                "sender": message.sender,
                "actual_sender": message.actual_sender or message.sender,
                "sender_frequency": frequency,
                "received_at": message.received_at,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "verdicts": [
                    {"step": "Read", "verdict": "Incomplete context" if message.context_incomplete else "Message body read"},
                    {"step": "Sender frequency", "verdict": frequency},
                    {"step": "Jev", "verdict": asdict(classification) if classification else "Not configured"},
                    {"step": "Routing", "verdict": "; ".join(reasons) if reasons else "Skip GLM; confident noise"},
                    {"step": "GLM", "verdict": {k: v for k, v in asdict(assessment).items() if k != "draft_body"} if not skip_glm else "Skipped"},
                    {"step": "Disposition", "verdict": f"{'Noise' if kind in ('marketing', 'informational') else 'Signal'}: {kind}; no Gmail changes"},
                ],
                "route": "agent_can_handle" if ready else "needs_attention",
                "draft": draft,
                "status": "pending",  # Even high confidence never grants approval.
                "approved_fingerprint": None,
                "provider_draft_id": None,
            }
            if screening:
                self.state["items"][message_id]["verdicts"].insert(3, {"step": "Newsletter intent", "verdict": screening})
            self.progress(f"{step}: saving proposal locally; awaiting human review.")
            self.state["last_scan"].setdefault("first_assessment_seconds", round(monotonic() - self._scan_started, 3))
            processed.append(message_id)
            self.state["last_scan"]["processed"] = len(processed)
            self.state["last_scan"]["reviewed"] = index
            self.checkpoint()
        self.state["cursor"] = page.next_cursor
        self.progress("Saving inbox position for the next scan…")
        self.checkpoint()
        self.progress("Page complete; more inbox pages remain." if page.next_cursor else
                      "Inbox pass complete; the next scan starts a fresh pass.")
        return processed

    def review(self, message_id: str, approve: bool) -> None:
        """Human-only entry point. This method is never exposed to the model."""
        item = self.state["items"][message_id]
        self.progress("Checking that this proposal is still pending review…")
        if item["status"] != "pending":
            raise ValueError("Only pending items can be reviewed")
        item["status"] = "approved" if approve else "rejected"
        if approve and item["draft"] is not None:
            item["approved_fingerprint"] = fingerprint(item["draft"])
        self.progress("Saving your approval locally…" if approve else "Saving your rejection locally…")
        self.checkpoint()

    def execute(self, message_id: str) -> None:
        """The only write path. Fail closed, including on ambiguous provider failure."""
        self.progress("Verifying human approval and the exact proposed draft…")
        item = self.state["items"][message_id]
        if item["status"] != "approved":
            raise ValueError("Explicit human approval is required")
        if item["draft"] is None:
            self.progress("Recording acknowledgment locally; there is no Gmail action.")
            item["status"] = "done"  # Human acknowledged triage; no external action.
            self.checkpoint()
            return
        if fingerprint(item["draft"]) != item["approved_fingerprint"]:
            raise ValueError("Draft changed after approval; refusing to execute")
        item["status"] = "executing"
        self.checkpoint()  # Crash after this point must not cause an automatic retry.
        try:
            self.progress("Preparing to save the approved reply draft…")
            provider_id = self.gmail.save_draft(Draft(**item["draft"]))
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("Provider did not return a draft ID")
        except Exception:
            item["status"] = "uncertain"
            self.checkpoint()
            raise
        item["provider_draft_id"] = provider_id
        item["status"] = "done"
        self.progress("Draft saved; recording completion locally. No email sent.")
        self.checkpoint()
