import tempfile
import unittest
from pathlib import Path

from agents.email.control_loop import EmailLoop, new_state
from agents.email.tools import Assessment, Classification
from agents.email.tool_implementations import DemoGmailTools
from tui.email import EmailTab
from tui.__main__ import TerminalApp


class SignalTests(unittest.TestCase):
    def test_signal_prioritizes_replies_summarizes_newsletters_and_hides_marketing(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.save_filters({"exclude_senders": ["blocked@example.com"]})
            tab.run("s")
            tab.run("s")
            self.assertEqual([key for key, _ in tab.rows("signal")], ["m2", "m1", "m3"])
            self.assertEqual([key for key, _ in tab.rows("noise")], ["m4"])
            self.assertEqual(tab.items["m3"]["assessment_source"], "glm")
            self.assertIn("export API", tab.items["m3"]["assessment"]["summary"])
            self.assertEqual(tab.items["m4"]["assessment_source"], "jev")
            self.assertIn("STEP VERDICTS", tab.audit_details("m4"))
            self.assertNotIn("STEP VERDICTS", tab.details("m3"))
            self.assertTrue(all(item["status"] == "pending" for item in tab.items.values()))
            self.assertEqual(len(tab.items["m4"]["verdicts"]), 6)
            self.assertEqual(tab.toolbar, [("c", "Connect"), ("t", "Analyze"), ("s", "Scan")])
            self.assertIn("Approve [A]   Reject [X]", tab.details("m1"))
            tab.run("a", "m1")
            self.assertIn("Approved. Execute [E]", tab.details("m1"))

    def test_scanning_does_not_switch_signal_to_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            app = TerminalApp([EmailTab(Path(directory) / "demo.json")])
            app.tab.save_filters({})
            try:
                app.submit("s", None)
                app.job.result(timeout=2)
                app.collect_progress()
                self.assertFalse(app.show_activity)
                self.assertEqual(len(app.rows()), 2)
                app.select_view("activity")
                self.assertTrue(app.show_activity)
            finally:
                app.executor.shutdown(wait=True)

    def test_urgent_classifier_disagreement_is_not_hidden_as_marketing(self):
        class Classifier:
            def classify(self, message):
                return Classification("urgent_reply", .95, .1)
        class Model:
            def assess(self, message):
                return Assessment("Maybe an ad", False, .95, "Looks promotional", None, category="marketing")
        loop = EmailLoop(DemoGmailTools(), Model(), new_state(), lambda: None, classifier=Classifier())
        loop.scan(1)
        self.assertEqual(loop.state["items"]["m1"]["signal_kind"], "needs_attention")

    def test_old_pending_assessments_are_reclassified_without_touching_approvals(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / "demo.json")
            tab.save_filters({})  # This test starts after preference review.
            tab.run("s")
            tab.run("a", "m1")
            with tab.open() as loop:
                for item in loop.state["items"].values():
                    item.pop("policy_version")
                loop.state["cursor"] = None
                loop.checkpoint()
            tab.run("s")
            self.assertEqual(tab.items["m1"]["status"], "approved")
            self.assertNotIn("policy_version", tab.items["m1"])
            self.assertEqual(tab.items["m2"]["policy_version"], 2)
