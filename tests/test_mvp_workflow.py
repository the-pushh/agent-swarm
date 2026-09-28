"""Stage 1 contract tests; install requirements-mvp.txt to enable them."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('langgraph') is not None
if AVAILABLE:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from mvp.fixture import initial_state, research
    from mvp.workflow import build_graph, resume_command


@unittest.skipUnless(AVAILABLE, 'Stage 1 requires requirements-mvp.txt in Python 3.12')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = str(Path(temporary.name) / 'checkpoints.sqlite')
        self.config = {'configurable': {'thread_id': 'test'}, 'recursion_limit': 30}

    def test_review_precedes_delegation_and_stop_does_no_work(self):
        calls = []
        with SqliteSaver.from_conn_string(self.db) as saver:
            graph = build_graph(saver, lambda task: calls.append(task))
            graph.invoke(initial_state(), self.config)
            snapshot = graph.get_state(self.config)
            self.assertEqual(calls, [])
            graph.invoke(resume_command(snapshot, 'stop'), self.config)
            self.assertEqual(calls, [])
            self.assertEqual(graph.get_state(self.config).values['status'], 'stopped')

    def test_invalid_input_preserves_saved_interrupt(self):
        with SqliteSaver.from_conn_string(self.db) as saver:
            graph = build_graph(saver, research)
            graph.invoke(initial_state(), self.config)
            snapshot = graph.get_state(self.config)
            with self.assertRaisesRegex(ValueError, 'Choose one of'):
                resume_command(snapshot, 'local-printers')
            self.assertEqual(graph.get_state(self.config).values, snapshot.values)
            graph.invoke(resume_command(snapshot, 'start'), self.config)
            self.assertEqual(graph.get_state(self.config).values['status'], 'blocked')

    def test_owner_rejects_completion_without_evidence(self):
        def incomplete(task):
            return {'status': 'completed', 'artifact': 'Unsupported answer'}
        with SqliteSaver.from_conn_string(self.db) as saver:
            graph = build_graph(saver, incomplete)
            graph.invoke(initial_state(), self.config)
            graph.invoke(resume_command(graph.get_state(self.config), 'start'), self.config)
            state = graph.get_state(self.config).values
            self.assertTrue(all(task['status'] == 'blocked' for task in state['tasks'].values()))

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, '-m', 'mvp', '--db', self.db, '--json', *args],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                timeout=20)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def test_fresh_process_resume_keeps_completed_work_and_revision(self):
        first = self.cli('start', '--run', 'process-test')
        self.assertEqual(first['state']['status'], 'review')
        self.assertEqual(first['state']['events'], [])
        blocked = self.cli('resume', 'process-test', 'start')
        original = blocked['state']['tasks']['positioning']
        self.assertEqual(original['status'], 'completed')
        self.assertEqual(blocked['state']['tasks']['production']['status'], 'blocked')
        self.assertEqual(self.cli('status', 'process-test')['state'], blocked['state'])
        self.cli('resume', 'process-test', 'invalid', success=False)
        final = self.cli('resume', 'process-test', 'local-printers')
        self.assertEqual(final['state']['revision'], 2)
        self.assertEqual(final['state']['status'], 'completed')
        self.assertEqual(final['state']['tasks']['positioning'], original)
        self.assertEqual(final['state']['tasks']['production']['attempts'], 2)
        self.assertEqual(len(final['state']['events']), 6)
        self.cli('resume', 'process-test', 'local-printers', success=False)
        self.cli('start', '--run', 'process-test', success=False)
        self.assertEqual(self.cli('status', 'process-test')['state'], final['state'])

    def test_different_run_ids_do_not_share_state(self):
        self.cli('start', '--run', 'one')
        self.cli('resume', 'one', 'start')
        self.cli('start', '--run', 'two')
        self.assertEqual(self.cli('status', 'one')['state']['status'], 'blocked')
        self.assertEqual(self.cli('status', 'two')['state']['events'], [])
        self.cli('status', 'missing', success=False)


if __name__ == '__main__':
    unittest.main()
