"""Interrupted process reconciliation and goal-level verification."""
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from rich.console import Console
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from mvp.planning import Plan, build_project
    from mvp.project import run_command
    from mvp.recovery import reconcile_task
    from mvp.completion import GoalCheck, assess_goal
    from mvp.replanning import PlanPatch
    from test_mvp_planning import plan_data
    from test_mvp_research import ScriptedToolModel, brief_data, call


def record_data():
    return {'status': 'completed', 'reason': 'Both official sources support the comparison.',
            'question': 'A source-supported comparison.', 'model': 'test', 'search_calls': 1, 'page_reads': 2,
            'verdict': {'status': 'accepted', 'attempt': 1, 'reason': 'Both official sources support the comparison.'},
            'attempts': [{'brief': brief_data(), 'issues': []}],
            'sources': {'S1': {'read': True, 'url': 'https://example.com/one', 'passages': {'P1': 'Evidence one'}},
                        'S2': {'read': True, 'url': 'https://example.com/two', 'passages': {'P1': 'Evidence two'}}}}


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def seed(self, directory):
        data = plan_data()
        data['steps'] = [{**data['steps'][1], 'parent_id': None}]
        async def planner(goal):
            return Plan.model_validate(data)
        async with AsyncSqliteSaver.from_conn_string(str(directory/'project.sqlite')) as saver:
            graph = build_project(saver, planner, None)
            await graph.ainvoke({'goal': 'Compare tools using two public sources.', 'living': True, 'verify_goal': True},
                                {'configurable': {'thread_id': directory.name}})

    def test_partial_corrupt_and_changed_assignment_never_replay(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            assignment = {'task': 'one'}
            (directory/'assignment.json').write_text(json.dumps(assignment))
            (directory/'run.json').write_text(json.dumps({'status': 'running'}))
            self.assertEqual(reconcile_task(directory, assignment)['status'], 'failed')
            self.assertEqual(json.loads((directory/'run.json').read_text())['status'], 'running')
            (directory/'run.json').write_text('{broken')
            self.assertEqual(reconcile_task(directory, assignment)['status'], 'failed')
            (directory/'run.json').write_text(json.dumps(record_data()))
            self.assertEqual(reconcile_task(directory, {'task': 'different'})['status'], 'failed')
            self.assertEqual(reconcile_task(directory, assignment)['status'], 'completed')
            self.assertTrue((directory/'report.md').exists())
            data = record_data()
            data['sources']['S1']['passages'] = {}
            (directory/'run.json').write_text(json.dumps(data))
            self.assertEqual(reconcile_task(directory, assignment)['status'], 'failed')

    async def test_process_dies_after_artifact_then_recovery_adopts_without_model_research(self):
        async def keep(model, state):
            return PlanPatch(base_revision=state['revision'], action='keep', reason='Accepted work meets the task.', upsert=[], remove=[])
        final = GoalCheck(status='satisfied', reason='Saved evidence meets the research goal.', unmet=[], evidence_task_ids=['research'])
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)/'crash'
            directory.mkdir()
            await self.seed(directory)
            (directory/'fixture.json').write_text(json.dumps(record_data()))
            script = '''import asyncio, io, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rich.console import Console
from mvp.project import run_command, project_lock
from mvp.live import save_json
root=Path(sys.argv[1])
async def die(question, folder, model, console, **kwargs):
    save_json(folder/'run.json', json.loads((root/'fixture.json').read_text()))
    os._exit(19)
async def main():
    with project_lock(root), patch('mvp.project.execute', side_effect=die):
        await run_command(SimpleNamespace(command='start', revision=1, json=False), root, Console(file=io.StringIO()))
asyncio.run(main())
'''
            child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, str(directory)], capture_output=True, text=True, timeout=15)
            self.assertEqual(child.returncode, 19, child.stderr)
            before = (directory/'tasks'/'research'/'run.json').read_bytes()
            with patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_patch', side_effect=keep), \
                 patch('mvp.project.assess_goal', new_callable=AsyncMock, return_value=final), \
                 patch('mvp.project.execute', new_callable=AsyncMock) as execute:
                state = await run_command(SimpleNamespace(command='recover', json=False), directory, Console(file=io.StringIO()))
                execute.assert_not_called()
            self.assertEqual(state['status'], 'goal_satisfied')
            self.assertTrue(state['results']['research']['recovered'])
            self.assertEqual(state['recoveries'], 1)
            self.assertFalse(state['resume_available'])
            self.assertEqual(before, (directory/'tasks'/'research'/'run.json').read_bytes())
            self.assertIn('Goal review', (directory/'summary.md').read_text())
            with self.assertRaisesRegex(ValueError, 'No interrupted'):
                await run_command(SimpleNamespace(command='recover', json=False), directory, Console(file=io.StringIO()))

    async def test_cancelled_task_recovery_pauses_with_original_partial_evidence(self):
        async def cancel(question, folder, model, console, **kwargs):
            (folder/'run.json').write_text(json.dumps({'status': 'running'}))
            raise asyncio.CancelledError()
        async def pause(model, state):
            return PlanPatch(base_revision=state['revision'], action='pause', reason='Interrupted evidence needs a new research attempt.', upsert=[], remove=[])
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)/'cancel'
            directory.mkdir()
            await self.seed(directory)
            with patch('mvp.project.execute', side_effect=cancel), self.assertRaises(asyncio.CancelledError):
                await run_command(SimpleNamespace(command='start', revision=1, json=False), directory, Console(file=io.StringIO()))
            self.assertTrue(json.loads((directory/'state.json').read_text())['resume_available'])
            with patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_patch', side_effect=pause):
                state = await run_command(SimpleNamespace(command='recover', json=False), directory, Console(file=io.StringIO()))
            self.assertEqual(state['status'], 'paused')
            self.assertEqual(state['results']['research']['status'], 'failed')
            self.assertEqual(json.loads((directory/'tasks'/'research'/'run.json').read_text())['status'], 'running')

    async def test_interrupted_planning_recovers_to_review_without_starting_tasks(self):
        data = plan_data()
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)/'planning'
            directory.mkdir()
            with patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, side_effect=RuntimeError('provider stopped')):
                with self.assertRaises(RuntimeError):
                    await run_command(SimpleNamespace(command='create', goal='A source-supported research goal.', json=False), directory, Console(file=io.StringIO()))
            self.assertTrue(json.loads((directory/'state.json').read_text())['resume_available'])
            with patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, return_value=Plan.model_validate(data)), \
                 patch('mvp.project.execute', new_callable=AsyncMock) as execute:
                state = await run_command(SimpleNamespace(command='recover', json=False), directory, Console(file=io.StringIO()))
                execute.assert_not_called()
            self.assertEqual(state['status'], 'awaiting_review')
            self.assertEqual(state['results'], {})
            self.assertFalse(state['resume_available'])

    async def test_goal_check_rejects_invented_evidence_and_reports_unmet_goal(self):
        model = ScriptedToolModel(responses=[
            call('CheckedGoal', {'status': 'satisfied', 'reason': 'Goal completed with all required evidence.',
                'unmet': [], 'evidence_task_ids': ['invented']}, 1),
            call('CheckedGoal', {'status': 'not_satisfied', 'reason': 'The email was researched but has not been sent.',
                'unmet': ['Send the email.'], 'evidence_task_ids': ['research']}, 2)])
        result = await assess_goal(model, {'goal': 'Research then email the result.', 'results': {'research': {'status': 'completed'}}})
        self.assertEqual(result.status, 'not_satisfied')
        self.assertEqual(result.unmet, ['Send the email.'])


if __name__ == '__main__':
    unittest.main()
