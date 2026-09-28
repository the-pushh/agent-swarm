import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agents.email.control_loop import EmailLoop, new_state
from agents.email.preferences import analyze_preferences, filters_ready
from agents.email.tool_implementations import DemoGmailTools, JsonModelTools, demo_completion
from agents.email.tools import InboxPage
from tui.email import EmailTab
from tui.__main__ import TerminalApp


class PreferenceDiscoveryTests(unittest.TestCase):
    def make_loop(self):
        self.gmail = DemoGmailTools()
        self.gmail.read_message = Mock(side_effect=AssertionError('Analyze read a body'))
        self.gmail.read_titles = Mock(wraps=self.gmail.read_titles)
        self.complete = Mock(side_effect=demo_completion)
        self.loop = EmailLoop(self.gmail, JsonModelTools(self.complete), new_state(), lambda: None)
        return self.loop

    def test_analyze_batches_titles_caches_and_never_guesses_choices(self):
        loop = self.make_loop()
        loop.state['cursor'] = 'older-page'
        analyze_preferences(loop)
        self.gmail.read_titles.assert_called_once_with(['m1', 'm2', 'm3', 'm4'])
        self.gmail.read_message.assert_not_called()
        self.assertEqual(loop.state['items'], {})
        self.assertEqual(loop.state['cursor'], 'older-page')
        self.assertNotIn('last_scan', loop.state)
        self.assertNotIn('newsletter_filters', loop.state)
        self.assertFalse(filters_ready(loop.state))
        payload = json.loads(self.complete.call_args.args[1])
        self.assertTrue(all('body' not in title for title in payload['titles']))
        candidates = loop.state['title_analysis']['candidates']
        self.assertEqual(len([c for c in candidates if c['kind'] == 'sender']), 4)
        analyze_preferences(loop)
        self.assertEqual(self.complete.call_count, 1)
        self.assertEqual(self.gmail.read_titles.call_count, 1)

    def test_empty_inbox_does_not_call_model(self):
        loop = self.make_loop()
        self.gmail.list_inbox = Mock(return_value=InboxPage([], None))
        analyze_preferences(loop)
        self.complete.assert_not_called()
        self.assertEqual(loop.state['title_analysis']['candidates'], [])

    def test_failure_retains_metadata_for_retry(self):
        loop = self.make_loop()
        self.complete.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            analyze_preferences(loop)
        self.assertNotIn('title_analysis', loop.state)
        self.assertEqual(len(loop.state['title_cache']), 4)
        self.gmail.read_message.assert_not_called()

    def test_topics_require_observed_evidence(self):
        titles = [DemoGmailTools().read_title('m3')]
        invalid = [{'topics': [{'name': 'AI', 'evidence_ids': ['invented']}]},
                   {'topics': [{'name': '', 'evidence_ids': ['m3']}]}, {'topics': 'AI'}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                JsonModelTools(lambda *_: json.dumps(value)).analyze_titles(titles)

    def test_connect_analyze_mark_save_scan_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / 'demo.json')
            with self.assertRaisesRegex(ValueError, 'Analyze'):
                tab.run('s')
            tab.run('t')
            self.assertEqual(tab.rows('signal'), [])
            self.assertEqual(tab.analysis_choices, {})
            self.assertEqual(len(tab.rows('analysis')), 6)
            with self.assertRaisesRegex(ValueError, 'Mark at least one'):
                tab.save_analysis()
            tab.mark_filter('filter:sender:news@example.com', 'include')
            tab.mark_filter('filter:sender:sales@example.com', 'exclude')
            tab.save_analysis()
            self.assertEqual(tab.get_filters()['include_senders'], ['news@example.com'])
            self.assertEqual(tab.get_filters()['exclude_senders'], ['sales@example.com'])
            tab.run('s')
            self.assertEqual([key for key, _ in tab.rows('signal')], ['m2', 'm1', 'm3'])
            self.assertEqual(tab.items['m4']['assessment_source'], 'sender_filter')
            self.assertTrue(all(item['status'] == 'pending' for item in tab.items.values()))

    def test_manual_rules_preserved_and_choices_editable(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / 'demo.json')
            tab.save_filters({'include_topics': ['Manual topic'], 'exclude_senders': ['extra@example.com']})
            tab.run('t')
            tab.mark_filter('filter:sender:news@example.com', 'exclude')
            tab.mark_filter('filter:sender:news@example.com', 'include')
            tab.save_analysis()
            self.assertIn('Manual topic', tab.get_filters()['include_topics'])
            self.assertIn('extra@example.com', tab.get_filters()['exclude_senders'])
            self.assertNotIn('news@example.com', tab.get_filters()['exclude_senders'])
            tab.mark_filter('filter:sender:news@example.com', None)
            self.assertNotIn('news@example.com', tab.get_filters()['include_senders'])

    def test_stale_analysis_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / 'demo.json')
            tab.run('t')
            tab.mark_filter('filter:sender:news@example.com', 'include')
            with tab.open() as agent:
                agent.state['title_analysis']['created_at'] = 'changed'
                agent.checkpoint()
            with self.assertRaisesRegex(ValueError, 'changed'):
                tab.save_analysis()

    def test_analyze_opens_checklist_and_renders_without_signal_junk(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / 'demo.json')
            app = TerminalApp([tab])
            screen = Mock()
            screen.getmaxyx.return_value = (30, 120)
            try:
                app.submit('t', None)
                app.job.result(timeout=2)
                app.collect_progress()
                self.assertTrue(app.show_analysis)
                self.assertFalse(app.show_activity)
                app.draw(screen)
                self.assertTrue(app.rendered_selection.startswith('filter:'))
                app.select_view('signal')
                self.assertEqual(app.rows(), [])
            finally:
                app.executor.shutdown(wait=True)
