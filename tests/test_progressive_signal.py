import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from agents.email.tool_implementations import demo_completion
from tui.email import EmailTab
from tui.__main__ import TerminalApp


class ProgressiveSignalTests(unittest.TestCase):
    def test_first_signal_is_visible_and_saved_before_later_assessment_finishes(self):
        entered, release = Event(), Event()

        def slow_second(system, user):
            if json.loads(user)["subject"] == "Budget decision":
                entered.set()
                release.wait(3)
                raise RuntimeError("Second assessment failed")
            return demo_completion(system, user)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.json"
            tab = EmailTab(path)
            tab.save_filters({})  # This test starts after preference review.
            app = TerminalApp([tab])
            try:
                with patch("tui.email.demo_completion", side_effect=slow_second):
                    app.submit("s", None)
                    self.assertTrue(entered.wait(2))
                    app.collect_progress()
                    self.assertIsNotNone(app.job)
                    self.assertEqual([key for key, _ in app.rows()], ["m1"])
                    self.assertEqual(tab.last_scan["reviewed"], 1)
                    self.assertEqual(tab.last_scan["page_size"], 4)
                    self.assertIn("1/4 assessed", tab.timeframe()[1])
                    saved = json.loads(path.read_text())
                    self.assertIn("m1", saved["items"])
                    self.assertIsNone(saved["cursor"])
                    release.set()
                    with self.assertRaises(RuntimeError):
                        app.job.result(timeout=2)
                    app.collect_progress()
                    self.assertEqual([key for key, _ in app.rows()], ["m1"])
                    self.assertEqual(tab.last_scan["status"], "failed")
            finally:
                release.set()
                app.executor.shutdown(wait=True)

    def test_snapshot_does_not_share_mutable_agent_state_and_selection_stays_put(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            tab.consume_updates()
            full = {"items": dict(tab.items), "cursor": None, "last_scan": tab.last_scan}
            tab.items = {"m1": full["items"]["m1"]}
            app = TerminalApp([tab])
            try:
                app.rendered_selection = "m1"
                tab.publish_checkpoint(full)
                full["items"]["m1"] = {"invalid": True}
                app.collect_progress()
                self.assertEqual(app.rows()[app.selected][0], "m1")
                self.assertIn("assessment", tab.items["m1"])
                app.selected = 0
                app.collect_progress()  # No new snapshot must not undo keyboard navigation.
                self.assertEqual(app.selected, 0)
            finally:
                app.executor.shutdown(wait=True)
