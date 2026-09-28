import unittest
from unittest.mock import Mock

from agents.email.control_loop import EmailLoop, new_state
from agents.email.tool_implementations import DemoGmailTools, JevClassifierTools, JsonModelTools, demo_completion, demo_decision


class ScanEfficiencyTests(unittest.TestCase):
    def test_excluded_sender_costs_no_body_or_model_calls(self):
        gmail = DemoGmailTools()
        gmail.messages = [gmail.messages[3]]
        gmail.read_message = Mock(wraps=gmail.read_message)
        classify = Mock(side_effect=demo_decision)
        complete = Mock(side_effect=demo_completion)
        state = new_state()
        state['newsletter_filters'] = {'exclude_senders': ['sales@example.com']}
        loop = EmailLoop(gmail, JsonModelTools(complete), state, lambda: None,
                         classifier=JevClassifierTools(classify))
        loop.scan()
        counts = {'body_reads': gmail.read_message.call_count,
                  'jev_calls': classify.call_count, 'glm_calls': complete.call_count}
        self.assertEqual(counts, {'body_reads': 0, 'jev_calls': 0, 'glm_calls': 0})
        self.assertEqual(state['items']['m4']['screening']['decision'], 'exclude')

    def test_first_assessment_precedes_next_body_read(self):
        gmail = DemoGmailTools()
        original_read = gmail.read_message
        events = []
        def read(identifier):
            events.append(('read', identifier))
            return original_read(identifier)
        gmail.read_message = read
        state = new_state()
        def checkpoint():
            if 'm1' in state['items']:
                events.append(('saved', 'm1'))
        loop = EmailLoop(gmail, JsonModelTools(demo_completion), state, checkpoint)
        loop.scan(2)
        self.assertLess(events.index(('saved', 'm1')), events.index(('read', 'm2')))
        self.assertEqual(state['last_scan']['timings']['Gmail body']['calls'], 2)
        self.assertEqual(state['last_scan']['timings']['GLM assessment']['calls'], 2)

    def test_excluded_existing_signal_disappears_and_can_be_restored(self):
        gmail = DemoGmailTools()
        state = new_state()
        loop = EmailLoop(gmail, JsonModelTools(demo_completion), state, lambda: None)
        loop.scan()
        state['newsletter_filters'] = {'exclude_senders': ['alex@example.com']}
        gmail.read_message = Mock(wraps=gmail.read_message)
        loop.scan()
        self.assertNotIn('m1', [call.args[0] for call in gmail.read_message.call_args_list])
        self.assertEqual(state['items']['m1']['assessment_source'], 'sender_filter')
        state['newsletter_filters'] = {}
        loop.scan()
        self.assertEqual(state['items']['m1']['signal_kind'], 'reply')
