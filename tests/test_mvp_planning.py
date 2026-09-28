"""Stage 3 contracts through the persisted review/execution graph."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from mvp.planning import Plan, build_project, generate_plan, review_command
    from test_mvp_research import ScriptedToolModel, call


def plan_data():
    data = {'success_criteria': 'Produce a useful evidence-based recommendation.', 'assumptions': [], 'steps': [
        {'id': 'investigate', 'title': 'Investigate options', 'kind': 'group', 'owner': 'Project owner',
         'instruction': 'Compare the relevant public evidence.', 'done_when': 'All child tasks have useful results.'},
        {'id': 'research', 'parent_id': 'investigate', 'title': 'Research options', 'kind': 'task',
         'owner': 'Market analyst', 'specialist': 'research', 'instruction': 'Read official sources about both options.',
         'done_when': 'A sourced comparison covers both options.'},
        {'id': 'recommend', 'parent_id': 'investigate', 'title': 'Recommend an option', 'kind': 'task',
         'owner': 'Product advisor', 'specialist': 'research', 'depends_on': ['research'],
         'instruction': 'Use prior research and check tradeoffs against sources.',
         'done_when': 'A recommendation explains the relevant tradeoffs.'}]}
    for step in data['steps']:
        step.setdefault('parent_id', None)
        step.setdefault('specialist', None)
        step.setdefault('depends_on', [])
    return data


def objective_plan_data():
    data = plan_data()
    data['objective'] = {'outcome': 'Produce one evidence-backed comparison and recommendation.', 'area': 'other', 'markers': [
        {'id': 'comparison', 'name': 'Accepted comparison', 'metric': 'Complete cited comparison briefs',
         'baseline': None, 'target': 1, 'unit': 'brief', 'counting_rule': 'Covers both options and explains tradeoffs with cited evidence.',
         'evidence_required': 'An accepted comparison brief with inspected source passages.', 'time_window': 'This project'}]}
    for step in data['steps']:
        if step['kind'] == 'task':
            step['marker_ids'] = ['comparison']
    return data


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class PlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_schema_feedback_repairs_invalid_dependencies(self):
        invalid = objective_plan_data()
        invalid['steps'][2]['depends_on'] = ['missing']
        model = ScriptedToolModel(responses=[call('InitialPlan', invalid, 1), call('InitialPlan', objective_plan_data(), 2)])
        plan = await generate_plan(model, 'Compare two publicly documented tools.')
        self.assertEqual(plan.steps[2].depends_on, ['research'])
        self.assertFalse(model.responses)

    def test_rejects_cycles_missing_references_duplicates_and_unassigned_tasks(self):
        for mutate in (
            lambda p: p['steps'][1].update(depends_on=['recommend']),
            lambda p: p['steps'][1].update(parent_id='research'),
            lambda p: p['steps'][1].update(parent_id='missing'),
            lambda p: p['steps'][1].update(depends_on=['investigate']),
            lambda p: p['steps'][1].update(specialist=None),
            lambda p: p['steps'][2].update(id='research'),
            lambda p: p['steps'][0].update(parent_id='investigate'),
            lambda p: p['steps'][1].update(specialist='invented_agent'),
        ):
            data = plan_data()
            mutate(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                Plan.model_validate(data)

    async def test_review_edit_restart_start_and_dependency_context(self):
        calls = []
        async def planner(goal):
            calls.append('plan')
            return Plan.model_validate(plan_data())
        async def runner(step, goal, context):
            calls.append(step.id)
            if step.id == 'recommend':
                self.assertEqual(context['research']['brief'], 'Actual upstream result')
            return {'status': 'completed', 'brief': 'Actual upstream result'}
        config = {'configurable': {'thread_id': 'persist'}}
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'project.sqlite')
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = build_project(saver, planner, runner)
                await graph.ainvoke({'goal': 'A research goal.'}, config)
                snap = await graph.aget_state(config)
                self.assertEqual(calls, ['plan'])
                self.assertEqual(snap.values['status'], 'awaiting_review')
                self.assertTrue(snap.interrupts)
                edit = copy.deepcopy(snap.values['plan'])
                edit['steps'][1]['owner'] = 'Specialist market owner'
                await graph.ainvoke(review_command(snap, 'edit', 1, edit), config)
                snap = await graph.aget_state(config)
                self.assertEqual(snap.values['revision'], 2)
                self.assertEqual(calls, ['plan'])
                with self.assertRaisesRegex(ValueError, 'revision changed'):
                    review_command(snap, 'start', 1)
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = build_project(saver, planner, runner)
                snap = await graph.aget_state(config)
                await graph.ainvoke(review_command(snap, 'start', 2), config)
                snap = await graph.aget_state(config)
                self.assertEqual(snap.values['status'], 'tasks_completed')
                self.assertEqual(calls, ['plan', 'research', 'recommend'])
                self.assertEqual(snap.values['plan']['steps'][1]['owner'], 'Specialist market owner')
                with self.assertRaisesRegex(ValueError, 'not waiting'):
                    review_command(snap, 'start', 2)

    async def test_unavailable_step_blocks_dependents_but_independent_work_runs(self):
        data = plan_data()
        data['steps'][1]['specialist'] = 'email'
        independent = copy.deepcopy(data['steps'][1])
        independent.update(id='independent', specialist='research')
        data['steps'].append(independent)
        calls = []
        async def planner(goal):
            return Plan.model_validate(data)
        async def runner(step, goal, context):
            calls.append(step.id)
            return {'status': 'completed'}
        config = {'configurable': {'thread_id': 'blocked'}}
        with tempfile.TemporaryDirectory() as d:
            async with AsyncSqliteSaver.from_conn_string(str(Path(d) / 'p.sqlite')) as saver:
                graph = build_project(saver, planner, runner)
                await graph.ainvoke({'goal': 'Research and send an email.'}, config)
                snap = await graph.aget_state(config)
                await graph.ainvoke(review_command(snap, 'start', 1), config)
                snap = await graph.aget_state(config)
                self.assertEqual(calls, ['independent'])
                self.assertEqual(snap.values['status'], 'blocked')
                self.assertIn('not connected', snap.values['results']['research']['reason'])
                self.assertEqual(snap.values['results']['recommend']['status'], 'blocked')

    async def test_project_adapter_passes_scope_criteria_and_dependency_sources(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from rich.console import Console
        from mvp.project import run_command
        async def planner(goal):
            return Plan.model_validate(plan_data())
        config = {'configurable': {'thread_id': 'adapter'}}
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / 'adapter'
            directory.mkdir()
            async with AsyncSqliteSaver.from_conn_string(str(directory / 'project.sqlite')) as saver:
                graph = build_project(saver, planner, None)
                await graph.ainvoke({'goal': 'Research then recommend.'}, config)
            record = {'status': 'completed', 'reason': 'Accepted useful evidence',
                      'attempts': [{'brief': {'findings': [{'claim': 'Some evidence', 'source_id': 'S1', 'passage_id': 'P1'}]}}],
                      'sources': {'S1': {'read': True, 'url': 'https://example.com/docs'}}}
            with patch('mvp.project.execute', new_callable=AsyncMock, return_value=record) as execute:
                state = await run_command(SimpleNamespace(command='start', revision=1, json=False),
                                          directory, Console(file=io.StringIO()))
                self.assertEqual(execute.await_count, 2)
                first, second = execute.await_args_list
                self.assertIn('Complete only the assigned step', first.args[0])
                self.assertIn('A sourced comparison covers both options.', first.args[0])
                self.assertIn('https://example.com/docs', second.args[0])
                self.assertIn('Some evidence', second.args[0])
                self.assertEqual(state['status'], 'tasks_completed')

    async def test_cli_reads_and_edits_saved_plan_in_fresh_processes(self):
        async def planner(goal):
            return Plan.model_validate(plan_data())
        async def runner(*args):
            self.fail('No task may start during review')
        config = {'configurable': {'thread_id': 'cli'}}
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / 'cli'
            directory.mkdir()
            async with AsyncSqliteSaver.from_conn_string(str(directory / 'project.sqlite')) as saver:
                graph = build_project(saver, planner, runner)
                await graph.ainvoke({'goal': 'A fresh-process review test.'}, config)
            entry = 'import sys; from pathlib import Path; import mvp.project as p; p.PROJECTS=Path(sys.argv.pop(1)); p.main()'
            def command(*args):
                return subprocess.run([sys.executable, '-c', entry, d, *args], capture_output=True, text=True, timeout=15)
            shown = command('--json', 'show', 'cli')
            self.assertEqual(shown.returncode, 0, shown.stderr)
            state = json.loads(shown.stdout)
            self.assertEqual(state['results'], {})
            document = json.loads((directory / 'plan.json').read_text())
            document['plan']['steps'][1]['owner'] = 'Edited research owner'
            edited = Path(d) / 'edit.json'
            edited.write_text(json.dumps(document))
            result = command('edit', 'cli', '--file', str(edited))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(command('--json', 'show', 'cli').stdout)
            self.assertEqual(state['revision'], 2)
            self.assertEqual(state['status'], 'awaiting_review')
            self.assertEqual(state['results'], {})
            self.assertEqual(state['plan']['steps'][1]['owner'], 'Edited research owner')
            self.assertTrue((directory / 'plan-v1.json').exists())
            self.assertTrue((directory / 'plan-v2.json').exists())
            stale = command('edit', 'cli', '--file', str(edited))
            self.assertEqual(stale.returncode, 1)
            self.assertIn('revision changed', stale.stdout)

    async def test_invalid_edit_does_not_consume_review(self):
        async def planner(goal):
            return Plan.model_validate(plan_data())
        async def runner(*args):
            self.fail('Must not execute before Start')
        config = {'configurable': {'thread_id': 'edit'}}
        with tempfile.TemporaryDirectory() as d:
            async with AsyncSqliteSaver.from_conn_string(str(Path(d) / 'p.sqlite')) as saver:
                graph = build_project(saver, planner, runner)
                await graph.ainvoke({'goal': 'A research goal.'}, config)
                before = await graph.aget_state(config)
                invalid = plan_data()
                invalid['steps'][2]['depends_on'] = ['missing']
                with self.assertRaises(ValueError):
                    review_command(before, 'edit', 1, invalid)
                after = await graph.aget_state(config)
                self.assertEqual(before.values, after.values)
                self.assertTrue(after.interrupts)


if __name__ == '__main__':
    unittest.main()
