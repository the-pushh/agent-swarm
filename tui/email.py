"""Presentation adapter; all agent policy stays in agents/email."""

from datetime import datetime
import re
from copy import deepcopy
from queue import Empty, SimpleQueue

from agents.email.agent import open_agent
from agents.email.control_loop import fingerprint, glm_reasons, new_state
from agents.email.tools import Classification
from agents.email.preferences import (analyze_preferences, filters_ready, choices_for,
                                      candidate_key, filters_from_choices)
from agents.email.screening import NewsletterFilters, sender_matches
from agents.email.tool_implementations import (
    DemoGmailTools, JevClassifierTools, JsonModelTools, demo_completion, demo_decision,
)


class EmailTab:
    name = "Gmail agent"
    toolbar = [("c", "Connect"), ("t", "Analyze"), ("s", "Scan")]
    description = "Offline demo · fixture inbox · scripted Jev + GLM · no network"
    actions = [("c", "Connect Gmail"), ("t", "Analyze topics/senders"), ("s", "Scan 50"), ("r", "Refresh"), ("a", "Approve"),
               ("x", "Reject"), ("e", "Execute"), ("n", "Reset")]

    def __init__(self, state_path, gmail=None, live=False):
        self.state_path = state_path
        self.gmail = gmail
        self.live = live or gmail is not None
        if self.live and gmail is None:
            self.name = "Gmail agent"
            self.description = "Gmail not connected · press C to connect your account"
            self.actions = [("c", "Connect Gmail")]
            self.toolbar = [("c", "Connect")]
        if gmail is not None:
            self.name = "Gmail agent"
            self.description = f"LIVE · {gmail.account} · Jev + GLM · drafts only"
            self.actions = [action for action in self.actions if action[0] != "n"]
        self.items = {}
        self.cursor = None
        self.last_scan = None
        self.title_analysis = {}
        self.analysis_choices = {}
        self.newsletter_filters = {}
        self.updates = SimpleQueue()

    def publish_checkpoint(self, state):
        # The worker publishes detached snapshots, never its mutable state.
        self.updates.put(deepcopy({"items": state["items"], "cursor": state["cursor"],
                                   "last_scan": state.get("last_scan"), "newsletter_filters": state.get("newsletter_filters", {}),
                                   "title_analysis": state.get("title_analysis", {})}))

    def consume_updates(self):
        latest = None
        while True:
            try:
                latest = self.updates.get_nowait()
            except Empty:
                break
        if latest is not None:
            self.items, self.cursor = latest["items"], latest["cursor"]
            self.last_scan = latest["last_scan"]
            if self.newsletter_filters != latest["newsletter_filters"] or self.title_analysis != latest["title_analysis"]:
                self.newsletter_filters = latest["newsletter_filters"]
                self.title_analysis = latest["title_analysis"]
                self.analysis_choices = choices_for(self.title_analysis, self.newsletter_filters)
        return latest is not None

    def open(self, progress=lambda message: None):
        if self.live and self.gmail is None:
            raise ValueError("Connect Gmail first with C.")
        if self.gmail is not None:
            self.gmail.progress = progress
            from providers.openrouter import complete_json
            from providers.typesafe import decide
            return open_agent(self.state_path, self.gmail, JsonModelTools(complete_json),
                              classifier=JevClassifierTools(decide), progress=progress,
                              on_checkpoint=self.publish_checkpoint)
        return open_agent(self.state_path, DemoGmailTools(), JsonModelTools(demo_completion),
                          classifier=JevClassifierTools(demo_decision), progress=progress,
                          on_checkpoint=self.publish_checkpoint)

    def refresh(self):
        if self.live and self.gmail is None:
            return
        with self.open() as agent:
            self.items = agent.state["items"]
            self.cursor = agent.state["cursor"]
            self.last_scan = agent.state.get("last_scan")
            self.newsletter_filters = agent.state.get("newsletter_filters", {})
            self.title_analysis = agent.state.get("title_analysis", {})
            self.analysis_choices = choices_for(self.title_analysis, self.newsletter_filters)

    def filter_revision(self):
        from agents.email.screening import NewsletterFilters
        return NewsletterFilters.from_dict(getattr(self, "newsletter_filters", {})).revision

    def get_filters(self):
        return filters_from_choices(self.title_analysis, self.analysis_choices, self.newsletter_filters)

    def save_filters(self, values, expected_analysis=None, expected_filters=None):
        from dataclasses import asdict
        filters = NewsletterFilters.from_dict(values)
        with self.open() as agent:
            if expected_filters is not None and fingerprint(asdict(NewsletterFilters.from_dict(agent.state.get("newsletter_filters", {})))) != expected_filters:
                raise ValueError("Filters changed since review; propose them again")
            if expected_analysis is not None and fingerprint(expected_analysis) != fingerprint(agent.state.get("title_analysis", {})):
                raise ValueError("Analysis changed; refresh and review again")
            agent.state["newsletter_filters"] = asdict(filters)
            agent.state["newsletter_preferences_confirmed"] = True
            agent.state["cursor"] = None
            agent.state["completed_pages"] = 0
            agent.checkpoint()
        self.refresh()

    def mark_filter(self, identifier, decision):
        if decision not in ("include", "exclude", None):
            raise ValueError("Choose Include, Exclude, or undecided")
        key = identifier.removeprefix("filter:")
        if key not in {candidate_key(c) for c in self.title_analysis.get("candidates", [])}:
            raise ValueError("Select a discovered topic or sender")
        if decision is None:
            self.analysis_choices.pop(key, None)
        else:
            self.analysis_choices[key] = decision

    def save_analysis(self):
        filters = self.get_filters()
        if not NewsletterFilters.from_dict(filters).configured:
            raise ValueError("Mark at least one topic or sender Include/Exclude, or add a manual rule with M")
        self.save_filters(filters, expected_analysis=self.title_analysis)

    def timeframe(self):
        """Actual dates of visible messages, not a claim of full query coverage."""
        visible = [self.items[key] for key, _ in self.rows("signal") if key in self.items]
        dates = [datetime.fromisoformat(item["received_at"]).astimezone()
                 for item in visible if item.get("received_at")]
        unknown = len(visible) - len(dates)
        if dates:
            start, end = min(dates).strftime("%d %b %Y"), max(dates).strftime("%d %b %Y")
            received = f"Email dates: {start}" + (f" – {end}" if end != start else "")
            if unknown:
                received += f" · {unknown} undated"
        else:
            received = "Email dates not recorded for these saved results" if visible else "No attention items scanned"
        query = self.gmail.query if self.gmail else ""
        relative = re.fullmatch(r"newer_than:(\d+)d", query)
        scope = (f"Inbox: last {relative[1]} days" if relative else f"Inbox: {query}" if query else "Entire inbox") if self.gmail else "Demo"
        if self.last_scan:
            timestamp = self.last_scan.get("finished_at") or self.last_scan["started_at"]
            scanned = datetime.fromisoformat(timestamp).astimezone().strftime("%d %b %H:%M %Z")
            status = self.last_scan["status"]
            coverage = ("more backlog" if self.cursor else "pass complete") if status == "complete" else "partial; retry scan" if status == "failed" else "incomplete scan"
            line = f"{scope} · {scanned} · {coverage}"
            if status == "running":
                page = self.last_scan.get("page_number", 1)
                size = self.last_scan.get("page_size")
                progress = f"{self.last_scan.get('reviewed', 0)}/{size} assessed" if size is not None else "fetching"
                line = f"{scope} · page {page}: {progress}"
            elif self.last_scan.get("page_number"):
                line += f" · p{self.last_scan['page_number']}"
        elif self.items:
            line = f"{scope} · scan time unknown · {'more backlog' if self.cursor else 'coverage unknown'}"
        else:
            line = f"{scope} · not scanned yet"
        return received, line

    @staticmethod
    def kind(item):
        return item.get("signal_kind", "reply" if item["assessment"]["needs_reply"] else "needs_attention")

    def rows(self, view="activity"):
        if view == "analysis":
            labels = {"include": "[+]", "exclude": "[-]"}
            return [("filter:" + candidate_key(c),
                     f"{labels.get(self.analysis_choices.get(candidate_key(c)), '[ ]')} {c['kind'].capitalize()} · {c['value']} ({len(c['evidence_ids'])})")
                    for c in self.title_analysis.get("candidates", [])]
        order = {"urgent_reply": 0, "reply": 1, "needs_attention": 2, "newsletter": 3,
                 "marketing": 4, "informational": 5}
        labels = {"urgent_reply": "URGENT", "reply": "REPLY", "needs_attention": "REVIEW",
                  "newsletter": "READ", "marketing": "PROMO", "informational": "FYI"}
        entries = sorted(self.items.items(), key=lambda pair: order[self.kind(pair[1])])
        return [(identifier, f"{labels[self.kind(item)]} · {item['assessment']['summary']}")
                for identifier, item in entries
                if view == "activity" or (view == "noise" and self.kind(item) in ("marketing", "informational"))
                or (view == "signal" and (self.kind(item) in ("urgent_reply", "reply", "needs_attention")
                        or (self.kind(item) == "newsletter" and (item.get("screening") or {}).get("decision") == "include"
                            and item.get("filter_revision") == self.filter_revision()))
                    and item["status"] not in ("done", "rejected")
                    and not sender_matches(item.get("actual_sender") or item.get("sender", ""), self.newsletter_filters.get("exclude_senders", [])))]

    def overview(self):
        return f"{len(self.rows('signal'))} to read or act on · {len(self.rows('noise'))} noise filtered"

    def analysis_details(self, identifier):
        candidates = self.title_analysis.get("candidates", [])
        candidate = next((c for c in candidates if "filter:" + candidate_key(c) == identifier), None)
        if candidate is None:
            return ["No titles found in this inbox query.", "M adds filters manually; Esc returns to Signal."]
        choice = self.analysis_choices.get(candidate_key(candidate), "undecided")
        lines = [candidate["kind"].upper(), candidate["value"], "", f"Your choice: {choice}", "", "SUBJECTS"]
        for title in self.title_analysis["sample"]:
            if title["id"] in candidate["evidence_ids"]:
                lines += [title["subject"], title["sender"], ""]
        lines += ["Include [+ / I]   Exclude [- / X]   Undecided [U]", "",
                  "Enter saves your choices as filters. M adds rules manually.",
                  "Excluded senders skip all body/model reads. Topic rules screen newsletters."]
        return lines

    def details(self, identifier):
        if identifier and identifier.startswith("filter:"):
            return self.analysis_details(identifier)
        if identifier is None:
            if self.live and not self.gmail:
                return ["Gmail is not connected."]
            return ["No attention items found in the emails scanned so far." if self.items else "Analyze subjects and senders, mark Include/Exclude, save your choices, then Scan."]
        item = self.items[identifier]
        assessment = item["assessment"]
        kind = self.kind(item)
        titles = {"urgent_reply": "REPLY URGENTLY", "reply": "REPLY REQUESTED", "newsletter": "NEWSLETTER SUMMARY",
                  "needs_attention": "NEEDS YOUR REVIEW", "marketing": "PROMOTION FILTERED", "informational": "ROUTINE UPDATE"}
        lines = [titles[kind], "", item.get("subject", ""), item.get("sender", ""), "", assessment["summary"]]
        if kind == "needs_attention":
            lines += ["", assessment["reason"]]
        if item["draft"]:
            draft = item["draft"]
            lines += ["", "SUGGESTED REPLY", f"To: {draft['to']}", f"Subject: {draft['subject']}",
                      "", draft["body"]]
        elif assessment["needs_reply"]:
            lines += ["", "Your input is needed before a reply can be drafted."]
        elif kind in ("marketing", "informational"):
            lines += ["", "Hidden from Signal. Still present in Gmail; nothing deleted or archived."]
        else:
            lines += ["", "No reply requested."]
        lines += [""]
        if item["status"] == "pending":
            lines += ["Approve [A]   Reject [X]"]
        elif item["status"] == "approved":
            lines += ["Approved. Execute [E]"]
        return lines

    def audit_details(self, identifier):
        if identifier and identifier.startswith("filter:"):
            return self.analysis_details(identifier)
        if self.live and self.gmail is None:
            return ["CONNECT YOUR GMAIL", "", "Press C to connect. Google consent opens in your browser.",
                    "Return here after signing in; your account will appear above.", "",
                    "Save the GCP Desktop OAuth JSON at .secrets/gmail-client.json first.",
                    "See docs/gmail-setup.md for GCP setup.", "",
                    "Scanning sends email text to Jev and OpenRouter.",
                    "Drafts require your approval. Nothing is sent."]
        if identifier is None:
            return ["Your Gmail inbox is connected." if self.gmail else "Your demo inbox is ready.",
                    "", "Press S to scan up to 50 emails.",
                    "Live scans send email text to Jev and OpenRouter." if self.gmail else "All model responses are scripted.",
                    "Select a proposal, read its details, then approve or reject it.",
                    "Execute is a separate step after approval.",
                    "Press C to connect real Gmail." if not self.gmail else "Press C to reconnect or switch accounts."]
        item = self.items[identifier]
        assessment, classification = item["assessment"], item.get("classification")
        lines = [f"PROPOSAL {identifier} · {item['status'].upper()}", "",
                 assessment["summary"], "", f"Route: {item['route']}",
                 f"Handling score: {assessment['handling_score']:.0%}", assessment["reason"]]
        if classification:
            lines += ["", f"Jev: {classification['category']}",
                      f"Classification confidence: {classification['confidence']:.0%}",
                      f"Assessment by: {item['assessment_source'].upper()}"]
        if item.get("assessment_source") == "glm":
            explanation = item.get("assessment_reason") or "; ".join(glm_reasons(
                Classification(**classification) if classification else None))
            lines += ["", f"Why GLM was called: {explanation}",
                      "GLM assesses this email only; it does not retrieve extra thread context."]
        draft = item["draft"]
        if draft:
            lines += ["", "PROPOSED DRAFT", f"To: {draft['to']}", f"Subject: {draft['subject']}",
                      f"Reply to: {draft['message_id']} · Thread: {draft['thread_id']}", "", draft["body"],
                      "", "Approval permits saving this exact draft in Gmail. Nothing is sent." if self.gmail
                      else "Approval permits saving this exact draft in the demo. Nothing is sent."]
        else:
            lines += ["", "NO DRAFT",
                      "A reply is needed, but the model could not propose a draft." if assessment["needs_reply"]
                      else "The assessment says no reply is needed; no draft was created.",
                      "Approval acknowledges this assessment only.",
                      "It does not resolve any request described in the email."]
        if item.get("provider_draft_id"):
            lines += ["", f"{'Gmail' if self.gmail else 'Simulated'} draft ID: {item['provider_draft_id']}"]
        lines += ["", "STEP VERDICTS"]
        import json
        for verdict in item.get("verdicts", []):
            value = verdict["verdict"]
            if isinstance(value, dict):
                value = {key: val for key, val in value.items() if key != "draft_body"}
                value = json.dumps(value, ensure_ascii=False)
            lines += [f"{verdict['step']}: {value}"]
        if not item.get("verdicts"):
            lines += ["Earlier assessment; detailed step verdicts were not recorded."]
        return lines

    def confirmation(self, action, identifier):
        if action == "n":
            return "Reset this TUI's demo state? CLI state is separate."
        if identifier not in self.items:
            raise ValueError("Select a proposal first")
        item = self.items[identifier]
        if action in ("a", "x") and item["status"] != "pending":
            raise ValueError("Only pending proposals can be reviewed")
        if action == "e" and item["status"] != "approved":
            raise ValueError("Approve this proposal before executing it")
        if action == "a":
            return "Approve this exact proposal? Execute remains a separate step."
        if action == "x":
            return "Reject this proposal?"
        return "Execute this approved proposal in Gmail?" if self.gmail else "Execute this approved proposal in the offline demo?"

    def run(self, action, identifier=None, progress=lambda message: None):
        def report(message):
            progress(f"Demo: {message}" if not self.gmail and action != "c" else message)

        if action == "c":
            from providers.gmail import GmailTools
            from providers.gmail_auth import authorize
            if self.gmail is not None:
                authorize(progress=report)
            gmail = GmailTools.connect(interactive=True, progress=report)
            state_path = self.state_path.parent / f"email-gmail-{gmail.state_key}.json"
            # Load successfully before replacing the displayed account and proposals.
            from providers.openrouter import complete_json
            from providers.typesafe import decide
            with open_agent(state_path, gmail, JsonModelTools(complete_json),
                            classifier=JevClassifierTools(decide), progress=report) as agent:
                items, cursor = agent.state["items"], agent.state["cursor"]
                last_scan = agent.state.get("last_scan")
                title_analysis = agent.state.get("title_analysis", {})
                newsletter_filters = agent.state.get("newsletter_filters", {})
            self.gmail, self.live, self.state_path = gmail, True, state_path
            self.items, self.cursor = items, cursor
            self.last_scan = last_scan
            self.title_analysis = title_analysis
            self.analysis_choices = choices_for(title_analysis, newsletter_filters)
            self.newsletter_filters = newsletter_filters
            self.updates = SimpleQueue()  # Never replay snapshots from the previous account/demo.
            self.name = "Gmail agent"
            self.description = f"LIVE · {gmail.account} · Jev + GLM · drafts only"
            self.toolbar = type(self).toolbar
            self.actions = [("c", "Reconnect Gmail")] + [a for a in type(self).actions if a[0] not in ("c", "n")]
            return f"Connected: {gmail.account}. Press Analyze [T] to choose topics and senders, then Scan."
        # Compare the displayed proposal with fresh state under the agent lock.
        # Another terminal cannot change what this human is approving unnoticed.
        displayed = self.items.get(identifier)
        with self.open(progress=report) as agent:
            if action == "t":
                analyze_preferences(agent)
                message = "Mark topics and senders Include/Exclude, then Enter to save. M adds manual filters."
            elif action == "s" and not filters_ready(agent.state):
                raise ValueError("Use Analyze [T] and mark topics/senders before scanning")
            elif action == "s":
                processed = agent.scan(50)
                page = agent.state["last_scan"]["page_number"]
                message = f"Page {page} complete: {len(processed)} new results. " + (
                    "Press Scan for the next page." if agent.state["cursor"] else "Inbox pass complete.")
            elif action == "r":
                message = "State refreshed."
            elif action == "n":
                if self.gmail is not None:
                    raise ValueError("Reset is only available in demo mode")
                agent.state.clear()
                agent.state.update(new_state())
                agent.checkpoint()
                message = "Demo reset. Press S to start again."
            else:
                current = agent.state["items"].get(identifier)
                if displayed is None or fingerprint(displayed) != fingerprint(current):
                    raise ValueError("Proposal changed. Refresh and review it again.")
                if action in ("a", "x"):
                    agent.review(identifier, approve=action == "a")
                    message = "Approved; press E to execute." if action == "a" else "Proposal rejected."
                elif action == "e":
                    agent.execute(identifier)
                    message = "Executed in Gmail. No email sent." if self.gmail else "Executed in demo. No email sent."
                else:
                    raise ValueError("Unknown action")
            self.items, self.cursor = agent.state["items"], agent.state["cursor"]
            self.last_scan = agent.state.get("last_scan")
            self.newsletter_filters = agent.state.get("newsletter_filters", {})
            self.title_analysis = agent.state.get("title_analysis", {})
            self.analysis_choices = choices_for(self.title_analysis, self.newsletter_filters)
        return message
