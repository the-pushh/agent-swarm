"""Headless interaction tests through Textual's real widgets and project commands."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

AVAILABLE = (importlib.util.find_spec('textual') is not None
             and importlib.util.find_spec('langchain_openrouter') is not None)
if AVAILABLE:
    from textual.widgets import Button, Input, Markdown, Tree, TabbedContent
    from mvp.terminal import GoalApp
    from mvp.planning import Plan
    from mvp.completion import GoalCheck
    from mvp.replanning import PlanPatch
    from test_mvp_planning import plan_data, objective_plan_data
    from test_mvp_recovery import record_data


def one_task():
    data = plan_data()
    data['steps'] = [{**data['steps'][1], 'parent_id': None}]
    return Plan.model_validate(data)


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def send(self, app, pilot, text):
        app.query_one('#message', Input).value = text
        await pilot.click('#send')
        await app.workers.wait_for_complete()
        await pilot.pause()

    async def create_goal(self, app, pilot):
        await self.send(app, pilot, 'Compare two tools using official documentation.')

    async def test_chat_plan_start_inspect_revise_and_reopen(self):
        async def keep(model, state):
            return PlanPatch(base_revision=state['revision'], action='keep', reason='The result covers the planned work.', upsert=[], remove=[])
        async def execute(question, directory, model, console, **kwargs):
            from mvp.live import save_json, report_markdown
            record = record_data()
            save_json(directory/'run.json', record)
            (directory/'report.md').write_text(report_markdown(record))
            return record
        final = GoalCheck(status='satisfied', reason='Both options are compared with useful evidence.', unmet=[], evidence_task_ids=['research'])
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), \
             patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, return_value=one_task()), \
             patch('mvp.project.generate_patch', side_effect=keep), patch('mvp.project.execute', side_effect=execute), \
             patch('mvp.project.assess_goal', new_callable=AsyncMock, return_value=final):
            app = GoalApp(projects=Path(d))
            async with app.run_test(size=(140,48)) as pilot:
                await self.create_goal(app, pilot)
                self.assertEqual(app.state['status'], 'awaiting_review')
                self.assertEqual(app.state['results'], {})
                self.assertFalse(app.query_one('#inspector').display)
                await self.send(app, pilot, 'Focus the comparison on ease of deployment.')
                self.assertEqual(app.state['status'], 'awaiting_review')
                self.assertEqual(app.state['results'], {})
                await pilot.click('#inspect-toggle')
                tree = app.query_one('#goal-map', Tree)
                tree.select_node(tree.root.children[0])
                await pilot.pause()
                self.assertEqual(app.selected_id, 'research')
                await self.send(app, pilot, 'start')
                self.assertEqual(app.state['status'], 'goal_satisfied')
                self.assertFalse(app.busy)
                self.assertIn('goal check passed', app.messages[-1]['text'])
                app.selected_id = 'research'
                app.show_selected()
                self.assertIn('Research result', app.query_one('#details', Markdown)._markdown)
                saved_run = app.current_run
                app.save_screenshot('/tmp/agent-swarm-tested.svg')
                await self.send(app, pilot, 'Preserve all accepted work and return to review before doing anything else.')
                self.assertEqual(app.state['status'], 'awaiting_review')
                self.assertEqual(len(app.state['constraints']), 2)
                self.assertEqual(app.state['results']['research']['status'], 'completed')
            reopened = GoalApp(projects=Path(d), initial_run=saved_run)
            async with reopened.run_test(size=(100,38)) as pilot:
                await reopened.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(reopened.state['status'], 'awaiting_review')
                self.assertEqual(reopened.state['results']['research']['status'], 'completed')
                reopened.open_project('missing')
                await reopened.workers.wait_for_complete()
                self.assertEqual(reopened.state['status'], 'awaiting_review')
                self.assertIn('Saved project not found', reopened.messages[-1]['text'])
                self.assertTrue(any(m['text'] == 'start' for m in reopened.messages))

    async def test_numbered_start_runs_only_selected_task_then_next_ready(self):
        data = one_task().model_dump()
        base = data['steps'][0]
        data['steps'] = [{**base, 'id': f'task-{i}', 'title': f'Research option {i}'} for i in range(1, 5)]
        data['steps'][1]['depends_on'] = ['task-3']
        data['objective'] = objective_plan_data()['objective']
        for step in data['steps']:
            step['marker_ids'] = ['comparison']
        async def keep(model, state):
            return PlanPatch(base_revision=state['revision'], action='keep', reason='Keep the remaining work.', upsert=[], remove=[])
        async def execute(question, directory, model, console, **kwargs):
            from mvp.live import save_json, report_markdown
            record = record_data()
            save_json(directory/'run.json', record)
            (directory/'report.md').write_text(report_markdown(record))
            return record
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), \
             patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, return_value=Plan.model_validate(data)), \
             patch('mvp.project.generate_patch', side_effect=keep), patch('mvp.project.execute', side_effect=execute) as runner:
            app = GoalApp(projects=Path(d))
            async with app.run_test(size=(140,48)) as pilot:
                await self.create_goal(app, pilot)
                self.assertIn('Finish line', app.messages[-1]['text'])
                await self.send(app, pilot, '/goal')
                self.assertIn('not yet verified', app.messages[-1]['text'])
                self.assertIn('Evidence:', app.messages[-1]['text'])
                await self.send(app, pilot, 'start 2')
                self.assertIn('must wait for: task-3', app.messages[-1]['text'])
                self.assertEqual(runner.call_count, 0)
                self.assertEqual(app.state['reviews'], 0)
                await self.send(app, pilot, 'start 99')
                self.assertIn('no task 99', app.messages[-1]['text'])
                await self.send(app, pilot, 'Start 4')
                self.assertEqual(set(app.state['results']), {'task-4'})
                self.assertEqual(app.state['status'], 'awaiting_review')
                self.assertEqual(app.state['constraints'], [])
                await self.send(app, pilot, 'start')
                self.assertEqual(set(app.state['results']), {'task-4', 'task-1'})
                self.assertEqual(app.state['status'], 'awaiting_review')
                run = app.current_run
            reopened = GoalApp(projects=Path(d), initial_run=run)
            async with reopened.run_test(size=(120,40)) as pilot:
                await reopened.workers.wait_for_complete()
                await self.send(reopened, pilot, 'start')
                self.assertEqual(set(reopened.state['results']), {'task-4', 'task-1', 'task-3'})
                self.assertEqual(reopened.state['status'], 'awaiting_review')

    async def test_human_work_requires_evaluation_and_preserves_corrections(self):
        from mvp.evaluation import WorkEvaluation
        data = one_task().model_dump()
        base = data['steps'][0]
        data['steps'] += [
            {**base, 'id': 'implement', 'title': 'Implement the recommendation', 'specialist': 'human', 'depends_on': ['research']},
            {**base, 'id': 'followup', 'title': 'Research followup results', 'depends_on': ['implement']}]
        async def keep(model, state):
            return PlanPatch(base_revision=state['revision'], action='keep', reason='Keep the remaining work.', upsert=[], remove=[])
        async def execute(question, directory, model, console, **kwargs):
            from mvp.live import save_json, report_markdown
            record = record_data()
            save_json(directory/'run.json', record)
            (directory/'report.md').write_text(report_markdown(record))
            return record
        rejected = WorkEvaluation(status='changes_required', summary='The submitted implementation omits the researched requirement.', corrections=['Add the missing validation.'], research_task_ids=['research'])
        accepted = WorkEvaluation(status='accepted', summary='The submitted code and output meet the researched requirements.', corrections=[], research_task_ids=['research'])
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), \
             patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, return_value=Plan.model_validate(data)), \
             patch('mvp.project.generate_patch', side_effect=keep), patch('mvp.project.execute', side_effect=execute) as runner, \
             patch('mvp.project.evaluate_work', new_callable=AsyncMock, side_effect=[rejected, accepted]) as evaluator:
            app = GoalApp(projects=Path(d))
            async with app.run_test(size=(140,48)) as pilot:
                await self.create_goal(app, pilot)
                await self.send(app, pilot, 'start')
                await self.send(app, pilot, 'start 2')
                self.assertEqual(app.state['status'], 'awaiting_human')
                self.assertFalse(app.query_one('#agent-tile-1').display)
                self.assertTrue(app.query_one('#evaluator-tile').display)
                self.assertEqual(runner.call_count, 1)
                await self.send(app, pilot, 'done')
                evaluator.assert_not_awaited()
                self.assertNotIn('implement', app.state['results'])
                await self.send(app, pilot, 'done: Here is the first implementation with incomplete validation.')
                self.assertEqual(app.state['status'], 'awaiting_human')
                self.assertIn('Add the missing validation', app.messages[-1]['text'])
                self.assertNotIn('implement', app.state['results'])
                self.assertEqual(set(evaluator.call_args.args[1]['research']), {'research'})
                run = app.current_run
            reopened = GoalApp(projects=Path(d), initial_run=run)
            async with reopened.run_test(size=(140,48)) as pilot:
                await reopened.workers.wait_for_complete()
                self.assertEqual(reopened.state['status'], 'awaiting_human')
                await self.send(reopened, pilot, 'done: Here is the corrected implementation and matching validation output.')
                self.assertEqual(reopened.state['results']['implement']['status'], 'completed')
                self.assertEqual(len(reopened.state['human_reviews']), 2)
                self.assertNotIn('followup', reopened.state['results'])
                self.assertEqual(reopened.state['status'], 'awaiting_review')
                self.assertEqual(runner.call_count, 1)
                await pilot.click('#inspect-toggle')
                reopened.query_one('#inspect-tabs', TabbedContent).active = 'agents-tab'
                await pilot.pause()
                await pilot.click('#evaluator-tile')
                reopened.show_agent()
                details = reopened.query_one('#agent-details', Markdown)._markdown
                self.assertIn('Evaluator · Work verification', details)
                self.assertIn('changes required', details)
                self.assertIn('accepted', details)

    async def test_coordinator_visible_during_planning_and_after_reopen(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def planner(model, goal):
            entered.set()
            await release.wait()
            return one_task()
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), \
             patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', side_effect=planner):
            app = GoalApp(projects=Path(d))
            async with app.run_test(size=(140,48)) as pilot:
                app.query_one('#message', Input).value = 'Compare two tools using official documentation.'
                await pilot.click('#send')
                await asyncio.wait_for(entered.wait(), 5)
                await pilot.click('#inspect-toggle')
                app.query_one('#inspect-tabs', TabbedContent).active = 'agents-tab'
                await pilot.pause()
                await pilot.click('#coordinator-tile')
                self.assertIn('planning', str(app.query_one('#coordinator-tile', Button).label))
                self.assertIn('turning the goal into a plan', app.query_one('#agent-details', Markdown)._markdown)
                self.assertFalse(app.query_one('#agent-tile-0').display)
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertIn('waiting for you', str(app.query_one('#coordinator-tile', Button).label))
                self.assertIn('Created initial plan', app.query_one('#agent-details', Markdown)._markdown)
                run = app.current_run
            reopened = GoalApp(projects=Path(d), initial_run=run)
            async with reopened.run_test(size=(140,48)) as pilot:
                await reopened.workers.wait_for_complete()
                reopened.selected_agent = '@swarm'
                reopened.show_agent()
                self.assertIn('Created initial plan', reopened.query_one('#agent-details', Markdown)._markdown)
                self.assertIn('waiting for you', str(reopened.query_one('#coordinator-tile', Button).label))

    async def test_cancel_keeps_interface_responsive_and_offers_recovery(self):
        entered = asyncio.Event()
        async def slow(question, directory, model, console, **kwargs):
            (directory/'run.json').write_text(json.dumps({'events': [{'message': 'Research: reading official documentation'}]}))
            console.print('Research: reading official documentation')
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                (directory/'run.json').write_text(json.dumps({'status': 'cancelled'}))
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-placeholder'}), \
             patch('mvp.project.ChatOpenRouter'), patch('mvp.project.generate_plan', new_callable=AsyncMock, return_value=one_task()), \
             patch('mvp.project.execute', side_effect=slow):
            app = GoalApp(projects=Path(d))
            async with app.run_test(size=(140,48)) as pilot:
                await self.create_goal(app, pilot)
                app.query_one('#message', Input).value = 'start'
                await pilot.click('#send')
                await asyncio.wait_for(entered.wait(), 5)
                await pilot.click('#inspect-toggle')
                self.assertTrue(app.query_one('#inspector').display)
                app.query_one('#inspect-tabs', TabbedContent).active = 'agents-tab'
                await pilot.pause()
                await pilot.click('#agent-tile-0')
                self.assertIn('reading official documentation', app.query_one('#agent-details', Markdown)._markdown)
                self.assertIn('working', str(app.query_one('#agent-tile-0', Button).label))
                app.query_one('#message', Input).value = 'stop'
                await pilot.click('#send')
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertFalse(app.busy)
                self.assertTrue(app.state['resume_available'])
                self.assertIn('resume', app.messages[-1]['text'])
                self.assertEqual(app.state['results'], {})


if __name__ == '__main__':
    unittest.main()
