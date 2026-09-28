import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents.email.control_loop import EmailLoop, new_state
from agents.email.tool_implementations import DemoGmailTools, JsonModelTools, demo_completion
from tui.email import EmailTab


class ScanTimeframeTests(unittest.TestCase):
    def test_scan_records_times_and_partial_vs_completed_backlog(self):
        class DatedInbox(DemoGmailTools):
            def read_message(self, identifier):
                return replace(super().read_message(identifier), received_at="2026-09-20T12:00:00+00:00")
        loop = EmailLoop(DatedInbox(), JsonModelTools(demo_completion), new_state(), lambda: None)
        loop.scan(2)
        first = loop.state["last_scan"]
        self.assertEqual(first["status"], "complete")
        self.assertTrue(first["more_pages"])
        self.assertEqual(first["processed"], 2)
        self.assertLessEqual(first["started_at"], first["finished_at"])
        self.assertEqual(loop.state["items"]["m1"]["received_at"], "2026-09-20T12:00:00+00:00")
        loop.scan(2)
        self.assertFalse(loop.state["last_scan"]["more_pages"])

    def test_ui_reports_unknown_legacy_dates_without_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "state.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            received, scope = tab.timeframe()
            self.assertIn("not recorded", received)
            self.assertIn("pass complete", scope)
            tab.items["m1"]["received_at"] = "2026-09-20T12:00:00+00:00"
            received, _ = tab.timeframe()
            self.assertIn("20 Sep 2026", received)
            self.assertIn("1 undated", received)
            tab.last_scan = None
            self.assertIn("scan time unknown", tab.timeframe()[1])

    def test_failed_scan_is_not_reported_as_a_completed_pass(self):
        class BrokenInbox(DemoGmailTools):
            def list_inbox(self, cursor, limit):
                raise RuntimeError("offline")
        loop = EmailLoop(BrokenInbox(), JsonModelTools(demo_completion), new_state(), lambda: None)
        with self.assertRaises(RuntimeError):
            loop.scan()
        self.assertEqual(loop.state["last_scan"]["status"], "failed")
        self.assertIsNone(loop.state["cursor"])
