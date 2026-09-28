import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from agents.email.control_loop import EmailLoop, new_state
from agents.email.sender_frequency import frequency_for
from agents.email.tools import Classification, Message
from agents.email.tool_implementations import DemoGmailTools, JevClassifierTools, JsonModelTools, demo_completion


class SenderFrequencyTests(unittest.TestCase):
    def test_50_message_page_includes_whole_page_counts_without_double_counting(self):
        class Inbox(DemoGmailTools):
            messages = [Message(str(i), str(i), "replies@example.com", "Sale", "Promotion",
                                actual_sender="sales@example.com") for i in range(51)]

        captured = []
        class Classifier:
            def classify(self, message):
                captured.append(message.sender_frequency)
                return Classification("marketing", .99, .1)

        model = Mock()
        state = new_state()
        loop = EmailLoop(Inbox(), model, state, lambda: None, classifier=Classifier())
        self.assertEqual(len(loop.scan()), 50)
        self.assertEqual(state["cursor"], "50")
        self.assertEqual(captured[0]["observed_messages"], 50)
        self.assertEqual(captured[-1]["observed_messages"], 50)
        self.assertEqual(len(loop.scan()), 1)
        self.assertEqual(captured[-1]["observed_messages"], 51)
        self.assertEqual(loop.scan(), [])
        self.assertEqual(len(state["sender_observations"]), 51)
        model.assess.assert_not_called()

    def test_date_windows_sender_identity_and_unknown_dates(self):
        observations = {
            "1": {"sender": "Brand <Sales@Example.com>", "received_at": "2026-09-25T12:00:00+00:00"},
            "2": {"sender": "sales@example.com", "received_at": "2026-09-10T12:00:00+00:00"},
            "3": {"sender": "sales@example.com", "received_at": None},
            "4": {"sender": "personal@example.com", "received_at": "2026-09-25T12:00:00+00:00"},
        }
        result = frequency_for("sales@example.com", observations, datetime(2026, 9, 26, tzinfo=timezone.utc))
        self.assertEqual(result["observed_messages"], 3)
        self.assertEqual(result["observed_last_7_days"], 1)
        self.assertEqual(result["observed_last_30_days"], 2)
        self.assertEqual(result["unknown_date_count"], 1)

    def test_frequency_reaches_both_models_without_forcing_marketing(self):
        decide = Mock(return_value={"route": {"type": "choice", "choice": "newsletter", "confidence": .99},
                                    "can_handle": {"type": "noul", "noul": .99}})
        complete = Mock(side_effect=demo_completion)
        message = Message("m", "t", "news@example.com", "Weekly digest", "Product news",
                          sender_frequency={"observed_messages": 50})
        self.assertEqual(JevClassifierTools(decide).classify(message).category, "newsletter")
        self.assertEqual(decide.call_args.args[0]["sender_frequency"]["observed_messages"], 50)
        self.assertEqual(JsonModelTools(complete).assess(message).category, "newsletter")
        import json
        self.assertEqual(json.loads(complete.call_args.args[1])["sender_frequency"]["observed_messages"], 50)
