import io
import json
import unittest
from unittest.mock import Mock, patch

from agents.email.control_loop import EmailLoop, new_state
from agents.email.tool_implementations import (
    DemoGmailTools, JevClassifierTools, JsonModelTools, demo_completion, demo_decision,
)
from providers.typesafe import decide


class JevTests(unittest.TestCase):
    def make_loop(self, decision=demo_decision):
        self.completion = Mock(side_effect=demo_completion)
        self.loop = EmailLoop(DemoGmailTools(), JsonModelTools(self.completion), new_state(),
                              lambda: None, classifier=JevClassifierTools(decision))
        self.loop.state["newsletter_filters"] = {"exclude_senders": ["blocked@example.com"]}
        return self.loop

    def test_marketing_skips_glm_but_newsletters_get_summaries(self):
        loop = self.make_loop()
        loop.scan()
        self.assertEqual(self.completion.call_count, 3)
        items = loop.state["items"]
        self.assertEqual(items["m4"]["assessment_source"], "jev")
        self.assertEqual(items["m2"]["route"], "needs_attention")
        self.assertTrue(all(item["status"] == "pending" for item in items.values()))
        with self.assertRaises(ValueError):
            loop.execute("m3")

    def test_low_confidence_falls_back_but_low_ability_does_not_block_noise_filtering(self):
        for field in ("confidence", "noul"):
            def low(state, questions):
                answer = demo_decision(state, questions)
                answer["route" if field == "confidence" else "can_handle"][field] = .2
                return answer
            self.make_loop(low).scan()
            self.assertEqual(self.completion.call_count, 4 if field == "confidence" else 3)

    def test_truncated_input_cannot_bypass_glm(self):
        loop = self.make_loop()
        loop.classifier = JevClassifierTools(demo_decision, max_body_chars=1)
        loop.scan()
        self.assertEqual(self.completion.call_count, 4)
        self.assertEqual(loop.state["items"]["m3"]["route"], "needs_attention")

    def test_bad_classifier_response_stops_without_advancing(self):
        for value in (True, float("nan"), 1.1, "0.99"):
            def bad(state, questions):
                answer = demo_decision(state, questions)
                answer["route"]["confidence"] = value
                return answer
            loop = self.make_loop(bad)
            with self.assertRaises(ValueError):
                loop.scan()
            self.assertEqual(loop.state["items"], {})
            self.assertIsNone(loop.state["cursor"])
            self.assertEqual(loop.state["last_scan"]["status"], "failed")
            self.completion.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("providers.typesafe.urlopen")
    def test_missing_key_does_not_make_request(self, request):
        with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
            decide({}, {})
        request.assert_not_called()

    @patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}, clear=True)
    @patch("providers.typesafe.urlopen")
    def test_official_request_contract(self, request):
        request.return_value = io.BytesIO(json.dumps({"answers": {"x": {"type": "noul", "noul": .9}}}).encode())
        self.assertEqual(decide({"body": "hi"}, {"x": {"type": "noul"}})["x"]["noul"], .9)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(sent.data)["model"], "jev-latest")

    def test_classifier_failure_retains_earlier_successes(self):
        def fail_second(state, questions):
            if state["subject"] == "Budget decision":
                raise TimeoutError()
            return demo_decision(state, questions)
        loop = self.make_loop(fail_second)
        with self.assertRaises(TimeoutError):
            loop.scan()
        self.assertEqual(list(loop.state["items"]), ["m1"])
        self.assertIsNone(loop.state["cursor"])
        loop.classifier = JevClassifierTools(demo_decision)
        loop.scan()
        self.assertEqual(self.completion.call_count, 3)
