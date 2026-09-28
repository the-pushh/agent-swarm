import unittest

from agents.email.control_loop import EmailLoop, new_state
from agents.email.tool_implementations import DemoGmailTools
from agents.email.tools import Assessment, Classification


class ExplanationTests(unittest.TestCase):
    def test_glm_no_reply_explains_trigger_and_no_draft(self):
        class Classifier:
            def classify(self, message):
                return Classification("informational", .6, .99)

        class Model:
            def assess(self, message):
                return Assessment("FYI only", False, .99, "No request to answer.", None)

        events = []
        loop = EmailLoop(DemoGmailTools(), Model(), new_state(), lambda: None,
                         classifier=Classifier(), progress=events.append)
        loop.scan(1)
        self.assertTrue(any("confidence 0.60 < 0.90" in event for event in events), events)
        self.assertTrue(any("no reply needed; no draft created" in event for event in events), events)
        self.assertIsNone(loop.state["items"]["m1"]["draft"])
        self.assertIn("confidence 0.60 < 0.90", loop.state["items"]["m1"]["assessment_reason"])
