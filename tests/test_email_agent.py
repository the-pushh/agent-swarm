import json
import tempfile
import unittest
from pathlib import Path

from agents.email.agent import open_agent
from agents.email.control_loop import EmailLoop, new_state
from agents.email.tool_implementations import DemoGmailTools, JsonModelTools, demo_completion


class RecordingGmail(DemoGmailTools):
    def __init__(self):
        self.writes = []
        self.fail = False

    def save_draft(self, draft):
        self.writes.append(draft)
        if self.fail:
            raise TimeoutError("Response lost after possible provider success")
        return super().save_draft(draft)


class EmailAgentTests(unittest.TestCase):
    def setUp(self):
        self.gmail = RecordingGmail()
        self.calls = []

        def complete(system, user):
            self.calls.append(user)
            return demo_completion(system, user)

        self.model = JsonModelTools(complete)
        self.loop = EmailLoop(self.gmail, self.model, new_state(), lambda: None)

    def test_incremental_scan_and_deduplication(self):
        self.assertEqual(self.loop.scan(2), ["m1", "m2"])
        self.assertEqual(self.loop.scan(2), ["m3", "m4"])
        self.assertEqual(self.loop.scan(2), [])
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.gmail.writes, [])
        self.assertEqual(self.loop.state["items"]["m2"]["route"], "needs_attention")

    def test_high_score_still_requires_approval_and_executes_once(self):
        self.loop.scan()
        with self.assertRaises(ValueError):
            self.loop.execute("m1")
        self.loop.review("m1", True)
        self.loop.execute("m1")
        with self.assertRaises(ValueError):
            self.loop.execute("m1")
        self.assertEqual(len(self.gmail.writes), 1)

    def test_rejected_proposal_cannot_execute(self):
        self.loop.scan()
        self.loop.review("m1", False)
        with self.assertRaises(ValueError):
            self.loop.execute("m1")
        self.assertEqual(self.gmail.writes, [])

    def test_mutated_draft_cannot_execute(self):
        self.loop.scan()
        self.loop.review("m1", True)
        self.loop.state["items"]["m1"]["draft"]["to"] = "different@example.com"
        with self.assertRaises(ValueError):
            self.loop.execute("m1")
        self.assertEqual(self.gmail.writes, [])

    def test_ambiguous_write_cannot_retry(self):
        self.loop.scan()
        self.loop.review("m1", True)
        self.gmail.fail = True
        with self.assertRaises(TimeoutError):
            self.loop.execute("m1")
        self.assertEqual(self.loop.state["items"]["m1"]["status"], "uncertain")
        with self.assertRaises(ValueError):
            self.loop.execute("m1")
        self.assertEqual(len(self.gmail.writes), 1)

    def test_bad_model_output_keeps_cursor_for_retry(self):
        self.loop.model = JsonModelTools(lambda *_: '{"handling_score": 2}')
        with self.assertRaises(ValueError):
            self.loop.scan()
        self.assertIsNone(self.loop.state["cursor"])
        self.assertEqual(self.loop.state["items"], {})

    def test_truncated_input_requires_attention(self):
        self.loop.model = JsonModelTools(demo_completion, max_body_chars=1)
        self.loop.scan(1)
        self.assertEqual(self.loop.state["items"]["m1"]["route"], "needs_attention")

    def test_persistence_and_interrupted_write_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with open_agent(path, self.gmail, self.model) as loop:
                loop.scan(1)
                loop.review("m1", True)
            state = json.loads(path.read_text())
            state["items"]["m1"]["status"] = "executing"
            path.write_text(json.dumps(state))
            with open_agent(path, self.gmail, self.model) as loop:
                self.assertEqual(loop.state["cursor"], "1")
                self.assertEqual(loop.state["items"]["m1"]["status"], "uncertain")
                with self.assertRaises(ValueError):
                    loop.execute("m1")


if __name__ == "__main__":
    unittest.main()
