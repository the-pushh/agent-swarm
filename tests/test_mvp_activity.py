"""Activity uses existing tool metadata, including historical saved runs."""
import unittest
from mvp.activity import research_activity


class ActivityTests(unittest.TestCase):
    def test_shows_actual_queries_and_source_titles_without_model_counters(self):
        record = {'sources': {'S1': {'title': 'Official pricing documentation'}}, 'events': [
            {'message': 'Research: model call 1'},
            {'message': 'Research: model call 2'},
            {'message': 'Research: searching the web', 'query': 'official hosting prices'},
            {'message': 'Research: reading source', 'source_id': 'S1', 'url': 'https://example.org/pricing'},
            {'message': 'Owner: model call 3'}]}
        result = research_activity(record, 'Compare hosting costs')
        self.assertEqual(result, ['Research working on: Compare hosting costs',
            'Searching: official hosting prices',
            'Reading: Official pricing documentation — https://example.org/pricing',
            'Owner working on: Compare hosting costs'])
        self.assertEqual(record['events'][0]['message'], 'Research: model call 1')

    def test_keeps_errors_and_handles_missing_source_title(self):
        record = {'events': [{'message': 'Research: reading source', 'source_id': 'S3', 'url': 'https://example.org'},
                             {'message': 'Search failed'}]}
        self.assertEqual(research_activity(record, 'Research options'), ['Reading: S3 — https://example.org', 'Search failed'])
