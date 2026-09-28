"""Orchestration: choose adapters, load state, invoke a single control-loop step."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .control_loop import EmailLoop, new_state
from .preferences import analyze_preferences, filters_ready
from .screening import NewsletterFilters
from dataclasses import asdict
from .tool_implementations import DemoGmailTools, JevClassifierTools, JsonModelTools, demo_completion, demo_decision
from .tools import ClassifierTools, GmailTools, ModelTools
from providers.openrouter import complete_json
from providers.typesafe import decide


@contextmanager
def open_agent(state_path: Path, gmail: GmailTools, model: ModelTools, threshold: float = .85,
               classifier: ClassifierTools | None = None, progress=lambda message: None,
               on_checkpoint=lambda state: None):
    """One writer per state file; atomic checkpoints survive interrupted runs.

    Use a separate state path per account and provider (including demo vs live).
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("a") as lock:
        progress("Acquiring the agent state lock; another process may be using it…")
        fcntl.flock(lock, fcntl.LOCK_EX)
        progress("Loading saved proposals and inbox position…")
        state = json.loads(state_path.read_text()) if state_path.exists() else new_state()
        identity = getattr(gmail, "state_identity", None)
        if state.get("identity") != identity:
            if state["items"] or state.get("identity") is not None:
                raise ValueError("State belongs to another account/provider/query. Use a separate state file.")
            state["identity"] = identity

        def checkpoint():
            temporary = state_path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(state, file, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, state_path)
            on_checkpoint(state)

        # An interrupted external write may already have succeeded. Never retry it.
        for item in state["items"].values():
            if item["status"] == "executing":
                item["status"] = "uncertain"
                checkpoint()
        yield EmailLoop(gmail, model, state, checkpoint, threshold, classifier, progress)


def show(loop: EmailLoop, message_id: str | None = None):
    items = loop.state["items"]
    if message_id:
        print(json.dumps(items[message_id], indent=2))
        return
    for identifier, item in items.items():
        a = item["assessment"]
        print(f"{identifier} | {item['route']} | score={a['handling_score']:.2f} | {item['status']}")
        print(f"  {a['summary']}\n  {a['reason']}")
        if item.get("classification"):
            c = item["classification"]
            print(f"  Jev: {c['category']} (confidence={c['confidence']:.2f}); assessment={item['assessment_source']}")


def main():
    parser = argparse.ArgumentParser(description="Email agent: Jev classifier + OpenRouter GLM with fixture Gmail inbox")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--demo", action="store_true", help="Use scripted model responses without network access")
    modes.add_argument("--live", action="store_true", help="Use your connected Gmail account and real models")
    parser.add_argument("--state", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("analyze", help="Discover topics/senders from titles only")
    filters = commands.add_parser("filters", help="Replace filters with explicit human choices")
    for name in ("include-topics", "exclude-topics", "include-senders", "exclude-senders"):
        filters.add_argument("--" + name, action="append", default=[])
    scan = commands.add_parser("scan")
    scan.add_argument("--batch-size", type=int, default=50)
    status = commands.add_parser("status")
    status.add_argument("id", nargs="?")
    for name in ("approve", "reject", "execute"):
        commands.add_parser(name).add_argument("id")
    args = parser.parse_args()
    if args.live:
        from providers.gmail import GmailTools
        gmail = GmailTools.connect()
        default_path = Path(f".agent-state/email-gmail-{gmail.state_key}.json")
        print(f"Gmail: {gmail.account} | query: {gmail.query}")
    else:
        gmail = DemoGmailTools()
        default_path = Path(".agent-state/email-jev-demo.json" if args.demo else ".agent-state/email-jev-openrouter.json")
    state_path = args.state or default_path
    completion = demo_completion if args.demo else complete_json
    classifier = JevClassifierTools(demo_decision if args.demo else decide)
    with open_agent(state_path, gmail, JsonModelTools(completion), classifier=classifier) as loop:
        if args.command == "analyze":
            analyze_preferences(loop)
            print(json.dumps(loop.state["title_analysis"], indent=2))
        elif args.command == "filters":
            values = {name: getattr(args, name) for name in NewsletterFilters.__dataclass_fields__}
            loop.state["newsletter_filters"] = asdict(NewsletterFilters.from_dict(values))
            loop.state["newsletter_preferences_confirmed"] = True
            loop.state["cursor"] = None
            loop.state["completed_pages"] = 0
            loop.checkpoint()
            print("Filters saved. Run scan to process emails.")
        elif args.command == "scan":
            if not filters_ready(loop.state):
                print("Run analyze, then select filters in the TUI or with the filters command before scanning.")
                return
            loop.scan(args.batch_size)
            show(loop)
        elif args.command == "status":
            show(loop, args.id)
        elif args.command in ("approve", "reject"):
            show(loop, args.id)
            word = "APPROVE" if args.command == "approve" else "REJECT"
            if input(f"Type {word} to {args.command} this exact proposal: ") != word:
                print("No change made.")
                return
            loop.review(args.id, approve=args.command == "approve")
        elif args.command == "execute":
            loop.execute(args.id)
            show(loop, args.id)


if __name__ == "__main__":
    main()
