"""Living planner contracts over the real LangGraph and structured-output loops."""
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from mvp.planning import Plan, build_project, review_command
    from mvp.replanning import PlanPatch, apply_patch, generate_patch, reflect, MAX_REVIEWS
    from test_mvp_planning import plan_data
    from test_mvp_research import ScriptedToolModel, call


def state_data():
    return {'goal': 'Produce a grounded recommendation.', 'plan': Plan.model_validate(plan_data()).model_dump(),
            'revision': 1, 'results': {}, 'history': [], 'constraints': [], 'living': True,
            'reviews': 0, 'patches': [], 'known_ids': ['investigate', 'research', 'recommend'],
            'last_event': {'kind': 'task_result', 'task_id': 'research'}}


def patch_data(state, **kwargs):
    return PlanPatch.model_validate({'base_revision': state['revision'], 'action': 'keep',
        'reason': 'Remaining work is still appropriate.', 'upsert': [], 'remove': [], **kwargs})


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class ReplanningTests(unittest.IsolatedAsyncioTestCase):
    def test_patch_rejects_completed_changes_stale_cycles_and_retired_ids(self):
        state = state_data()
        state['results']['research'] = {'status': 'completed', 'report': 'unchanged.md'}
        original = copy.deepcopy(state)
        changed = copy.deepcopy(state['plan']['steps'][1])
        changed['instruction'] = 'Replace accepted work with something different.'
        for patch in (
            patch_data(state, base_revision=2),
            patch_data(state, action='revise', remove=['research']),
            patch_data(state, action='revise', upsert=[changed]),
            patch_data(state, action='revise', upsert=[{**state['plan']['steps'][2], 'depends_on': ['recommend']}]),
            patch_data(state, action='keep', remove=['recommend']),
            patch_data(state, action='revise', remove=['missing']),
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                apply_patch(state, PlanPatch.model_validate(patch.model_dump()))
        state['known_ids'].append('old')
        with self.assertRaisesRegex(ValueError, 'Retired'):
            apply_patch(state, PlanPatch.model_validate(patch_data(state, action='revise',
                upsert=[{**state['plan']['steps'][2], 'id': 'old'}]).model_dump()))
        state['known_ids'].remove('old')
        self.assertEqual(state, original)
        state['known_ids'] = state['known_ids'] + [f'retired_{n}' for n in range(21)]
        with self.assertRaisesRegex(ValueError, 'Lifetime step limit'):
            apply_patch(state, patch_data(state, action='revise',
                upsert=[{**state['plan']['steps'][2], 'id': 'new_step'}]))

    def test_revision_cannot_depend_on_a_blocked_attempt(self):
        state = state_data()
        state['results']['research'] = {'status': 'blocked', 'reason': 'No accepted evidence.'}
        changed = {**state['plan']['steps'][2], 'instruction': 'Reuse the blocked research as a final report.'}
        with self.assertRaisesRegex(ValueError, 'cannot depend on blocked'):
            apply_patch(state, patch_data(state, action='revise', upsert=[changed]))
        indirect = {**changed, 'id': 'after_recommend', 'depends_on': ['recommend']}
        with self.assertRaisesRegex(ValueError, 'even indirectly'):
            apply_patch(state, patch_data(state, action='revise', upsert=[indirect]))

    async def test_model_gets_validation_feedback_for_invalid_patch(self):
        state = state_data()
        state['results']['research'] = {'status': 'completed'}
        bad = patch_data(state, action='revise', remove=['research']).model_dump()
        good = patch_data(state).model_dump()
        model = ScriptedToolModel(responses=[call('CheckedPatch', bad, 1), call('CheckedPatch', good, 2)])
        result = await generate_patch(model, state)
        self.assertEqual(result.action, 'keep')
        self.assertFalse(model.responses)

    async def test_completed_branch_is_preserved_while_blocker_splits_and_rewires(self):
        data = plan_data()
        data['steps'][2]['specialist'] = 'web_search'
        follow = {**data['steps'][2], 'id': 'finish', 'specialist': 'research', 'depends_on': ['recommend']}
        data['steps'].append(follow)
        calls, events = [], []
        async def planner(goal):
            return Plan.model_validate(data)
        async def runner(step, goal, context):
            calls.append(step.id)
            return {'status': 'completed', 'report': f'{step.id}.md', 'brief': step.id}
        async def replanner(state):
            events.append(copy.deepcopy(state['last_event']))
            if state['last_event']['task_id'] == 'recommend':
                old = next(s for s in state['plan']['steps'] if s['id'] == 'recommend')
                a = {**old, 'id': 'part_a', 'specialist': 'research'}
                b = {**old, 'id': 'part_b', 'specialist': 'research', 'depends_on': ['part_a']}
                end = {**follow, 'depends_on': ['part_b']}
                return PlanPatch.model_validate(patch_data(state, action='revise', upsert=[a,b,end], remove=['recommend']).model_dump())
            return patch_data(state)
        config = {'configurable': {'thread_id': 'living'}, 'recursion_limit': 80}
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / 'p.sqlite')
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = build_project(saver, planner, runner, replanner)
                await graph.ainvoke({'goal': 'A research goal.', 'living': True}, config)
                snapshot = await graph.aget_state(config)
                self.assertEqual(calls, [])
                await graph.ainvoke(review_command(snapshot, 'start', 1), config)
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = build_project(saver, planner, runner, replanner)
                state = (await graph.aget_state(config)).values
                self.assertEqual(state['status'], 'tasks_completed')
                self.assertEqual(state['revision'], 2)
                self.assertEqual(calls, ['research', 'part_a', 'part_b', 'finish'])
                self.assertEqual(state['results']['research']['report'], 'research.md')
                self.assertEqual(state['results']['recommend']['status'], 'blocked')
                self.assertEqual(len(state['patches']), 5)
                self.assertEqual(events[1]['status'], 'blocked')

    async def test_limits_and_invalid_response_pause_without_losing_work(self):
        state = state_data()
        state['results']['research'] = {'status': 'completed', 'report': 'keep.md'}
        async def invalid(s):
            return patch_data(s, base_revision=99)
        result = await reflect(state, invalid)
        self.assertEqual(result['status'], 'paused')
        self.assertNotIn('plan', result)
        self.assertEqual(state['results']['research']['report'], 'keep.md')
        state['reviews'] = MAX_REVIEWS
        async def never(s):
            self.fail('Budget must stop before model call')
        result = await reflect(state, never)
        self.assertIn('limit', result['reason'])

    async def test_pause_then_cli_constraint_preserves_completed_result_and_versions(self):
        import io
        import json
        from types import SimpleNamespace
        from unittest.mock import patch as mock_patch
        from rich.console import Console
        from mvp.project import run_command
        async def planner(goal):
            return Plan.model_validate(plan_data())
        async def runner(step, goal, context):
            return {'status': 'completed', 'report': 'original.md'}
        async def pause(state):
            return patch_data(state, action='pause', reason='Need a user constraint before choosing the next approach.')
        async def revise(model, state):
            self.assertEqual(state['constraints'], ['Use official documentation only.'])
            changed = {**state['plan']['steps'][2], 'instruction': 'Recommend based only on official documentation.'}
            return patch_data(state, action='revise', upsert=[changed])
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)/'pause-test'
            directory.mkdir()
            config = {'configurable': {'thread_id': directory.name}}
            async with AsyncSqliteSaver.from_conn_string(str(directory/'project.sqlite')) as saver:
                graph = build_project(saver, planner, runner, pause)
                await graph.ainvoke({'goal': 'A research goal.', 'living': True}, config)
                snapshot = await graph.aget_state(config)
                await graph.ainvoke(review_command(snapshot, 'start', 1), config)
                state = (await graph.aget_state(config)).values
                self.assertEqual(state['status'], 'paused')
                self.assertEqual(list(state['results']), ['research'])
            args = SimpleNamespace(command='revise', constraint='Use official documentation only.', revision=1, json=False)
            with mock_patch('mvp.project.ChatOpenRouter'), mock_patch('mvp.project.generate_patch', side_effect=revise):
                state = await run_command(args, directory, Console(file=io.StringIO()))
            self.assertEqual(state['status'], 'awaiting_review')
            self.assertEqual(state['revision'], 2)
            self.assertEqual(state['results']['research']['report'], 'original.md')
            self.assertEqual(list(state['results']), ['research'])
            self.assertTrue((directory/'plan-v1.json').exists())
            self.assertTrue((directory/'plan-v2.json').exists())
            self.assertEqual(len(json.loads((directory/'changes.json').read_text())), 2)
            async with AsyncSqliteSaver.from_conn_string(str(directory/'project.sqlite')) as saver:
                graph = build_project(saver, planner, runner, pause)
                snapshot = await graph.aget_state(config)
                changed = copy.deepcopy(snapshot.values['plan'])
                changed['steps'][1]['instruction'] = 'Rewrite completed work differently.'
                with self.assertRaisesRegex(ValueError, 'Attempted task'):
                    review_command(snapshot, 'edit', 2, changed)

    async def test_user_constraint_is_reviewed_before_new_work(self):
        calls = []
        async def planner(goal):
            return Plan.model_validate(plan_data())
        async def runner(step, goal, context):
            calls.append(step.id)
            self.assertIn('official sources only', goal)
            return {'status': 'completed'}
        async def replanner(state):
            if state['last_event']['kind'] == 'user_constraint':
                updated = {**state['plan']['steps'][1], 'instruction': 'Research official sources only for both options.'}
                return PlanPatch.model_validate(patch_data(state, action='revise', upsert=[updated]).model_dump())
            return patch_data(state)
        config = {'configurable': {'thread_id': 'direction'}}
        with tempfile.TemporaryDirectory() as d:
            async with AsyncSqliteSaver.from_conn_string(str(Path(d)/'p.sqlite')) as saver:
                graph = build_project(saver, planner, runner, replanner)
                await graph.ainvoke({'goal': 'Compare tools.', 'living': True}, config)
                await graph.aupdate_state(config, {'constraints': ['official sources only'], 'status': 'revising',
                    'review_after_replan': True, 'last_event': {'kind': 'user_constraint'}}, as_node='direction')
                await graph.ainvoke(None, config)
                snapshot = await graph.aget_state(config)
                self.assertEqual(snapshot.values['revision'], 2)
                self.assertEqual(snapshot.values['status'], 'awaiting_review')
                self.assertTrue(snapshot.interrupts)
                self.assertEqual(calls, [])
                await graph.ainvoke(review_command(snapshot, 'start', 2), config)
                self.assertEqual(calls, ['research', 'recommend'])


if __name__ == '__main__':
    unittest.main()
