import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch
from threading import Event

from tui.email import EmailTab
from tui.registry import agent_tabs
from providers.gmail import GmailTools
from tui.__main__ import TerminalApp


class EmailTabTests(unittest.TestCase):
    def test_scan_reports_real_stages_and_glm_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            events = []
            tab.run("s", progress=events.append)
            self.assertTrue(any("Email 1/4: reading" in event for event in events))
            self.assertTrue(any("Jev is classifying" in event for event in events))
            self.assertTrue(any("GLM is assessing" in event for event in events))
            self.assertTrue(all(event.startswith("Demo:") for event in events))
            self.assertTrue(any("filtering noise" in event for event in events))
            self.assertEqual(sum("GLM is assessing" in event for event in events), 2)
            events.clear()
            tab.run("s", progress=events.append)
            self.assertFalse(any("GLM is assessing" in event for event in events))

    def test_progress_is_visible_while_worker_is_waiting(self):
        entered, release = Event(), Event()

        class SlowTab:
            def run(self, action, identifier, progress):
                progress("Jev is classifying email 1/2…")
                entered.set()
                release.wait(2)
                raise ValueError("Model request timed out")

        app = TerminalApp([SlowTab()])
        try:
            app.submit("s", None)
            self.assertTrue(entered.wait(1))
            app.collect_progress()
            self.assertIn("Jev is classifying", app.notice)
            self.assertIsNotNone(app.job)
            release.set()
            with self.assertRaises(ValueError):
                app.job.result(timeout=1)
            app.collect_progress()
            self.assertIsNone(app.job)
            self.assertIn("timed out", app.notice)
            self.assertTrue(any("Jev is classifying" in line for line in app.activity))
        finally:
            release.set()
            app.executor.shutdown(wait=True)

    def test_live_tui_opens_without_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("providers.gmail.GmailTools.connect") as connect:
                # Even if saved demo data exists, the default must not load it.
                demo = EmailTab(Path(directory) / "email-tui-demo.json")
                demo.save_filters({})
                demo.run("s")
                tab = agent_tabs(Path(directory))[0]
                tab.refresh()
                self.assertEqual(tab.actions, [("c", "Connect Gmail")])
                self.assertEqual(tab.details(None), ["Gmail is not connected."])
                self.assertEqual(tab.rows(), [])
                with self.assertRaises(ValueError):
                    tab.run("s")
                connect.assert_not_called()

    def test_connect_switches_account_and_state_without_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            gmail = GmailTools(MagicMock(), "me@example.com")
            with patch("providers.gmail.GmailTools.connect", return_value=gmail) as connect:
                message = tab.run("c")
                connect.assert_called_once_with(interactive=True, progress=ANY)
            self.assertIn("me@example.com", message)
            self.assertTrue(tab.live)
            self.assertEqual(tab.rows(), [])
            self.assertNotEqual(tab.state_path.name, "demo.json")
            self.assertNotIn("n", dict(tab.actions))
            gmail.service.users.assert_not_called()

    def test_failed_connection_keeps_demo_state(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            with patch("providers.gmail.GmailTools.connect", side_effect=ValueError("Missing client JSON")):
                with self.assertRaisesRegex(ValueError, "Missing client"):
                    tab.run("c")
            self.assertFalse(tab.live)
            self.assertEqual(len(tab.rows()), 4)

    def test_demo_review_execute_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.refresh()
            self.assertEqual(tab.rows(), [])
            tab.run("s")
            self.assertEqual(len(tab.rows()), 4)
            self.assertIn("SUGGESTED REPLY", tab.details("m1"))
            with self.assertRaises(ValueError):
                tab.run("e", "m1")
            tab.run("a", "m1")
            tab.run("e", "m1")
            self.assertEqual(tab.items["m1"]["provider_draft_id"], "demo-draft-m1")
            tab.run("x", "m2")
            self.assertEqual(tab.items["m2"]["status"], "rejected")
            tab.run("s")
            self.assertEqual(len(tab.rows()), 4)
            tab.run("n")
            self.assertEqual(tab.rows(), [])

    def test_approval_rejects_stale_display(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            with tab.open() as agent:
                agent.state["items"]["m1"]["draft"]["body"] = "Changed in another terminal"
                agent.checkpoint()
            with self.assertRaisesRegex(ValueError, "changed"):
                tab.run("a", "m1")
            tab.refresh()
            self.assertEqual(tab.items["m1"]["status"], "pending")
