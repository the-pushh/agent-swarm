import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from agents.email.screening import NewsletterFilters, screen_newsletter, sender_matches
from agents.email.tools import Message
from tui.email import EmailTab


class NewsletterFilterTests(unittest.TestCase):
    def setUp(self):
        self.message = Message('n', 't', 'Editor <editor@news.com>', 'Digest', 'AI research')
        self.classifier = Mock()
        self.classifier.match_topics.return_value = 'include'

    def test_sender_exclusion_wins_without_model(self):
        rules = NewsletterFilters(include_topics=['AI'], exclude_senders=['@news.com'])
        self.assertEqual(screen_newsletter(self.message, rules, self.classifier)['decision'], 'exclude')
        self.classifier.match_topics.assert_not_called()
        self.assertFalse(sender_matches('editor@fake-news.com', ['@news.com']))

    def test_topic_exclusion_overrides_included_sender(self):
        self.classifier.match_topics.return_value = 'exclude'
        rules = NewsletterFilters(include_senders=['editor@news.com'], exclude_topics=['politics'])
        self.assertEqual(screen_newsletter(self.message, rules, self.classifier)['decision'], 'exclude')

    def test_either_inclusion_and_uncertainty(self):
        rules = NewsletterFilters(include_topics=['AI'], include_senders=['another@news.com'])
        self.assertEqual(screen_newsletter(self.message, rules, self.classifier)['decision'], 'include')
        self.classifier.match_topics.return_value = 'uncertain'
        self.assertEqual(screen_newsletter(self.message, rules, self.classifier)['decision'], 'held')

    def test_empty_rules_hold_without_model(self):
        self.assertEqual(screen_newsletter(self.message, NewsletterFilters(), self.classifier)['decision'], 'held')
        self.classifier.match_topics.assert_not_called()

    def test_rules_rescreen_pending_preserve_approval_and_publish_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = EmailTab(Path(directory) / 'demo.json')
            tab.save_filters({})  # This test starts after preference review.
            tab.run('s')
            self.assertEqual(tab.items['m3']['screening']['decision'], 'held')
            self.assertIsNone(tab.items['m3']['draft'])
            tab.run('a', 'm1')
            tab.save_filters({'include_topics': ['export API']})
            tab.run('s')
            self.assertEqual(tab.items['m1']['status'], 'approved')
            self.assertEqual(tab.items['m3']['assessment_source'], 'glm')
            self.assertIn('m3', [key for key, _ in tab.rows('signal')])
            tab.save_filters({'exclude_topics': ['export API']})
            self.assertNotIn('m3', [key for key, _ in tab.rows('signal')])
            tab.run('s')
            self.assertEqual(tab.items['m3']['screening']['decision'], 'exclude')
            self.assertEqual(tab.items['m3']['assessment_source'], 'screening')
