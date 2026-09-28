"""Objective targets remain fixed and need measured evidence for completion."""
import copy
import importlib.util
import unittest

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from mvp.planning import Plan, generate_plan
    from mvp.replanning import PlanPatch, apply_patch, validate_replacement
    from mvp.completion import assess_goal
    from mvp.objectives import MarkerCheck, validate_marker_checks, objective_markdown
    from test_mvp_planning import objective_plan_data, plan_data
    from test_mvp_research import ScriptedToolModel, call


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class ObjectiveTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        return {'goal': 'Produce a sourced comparison.', 'plan': Plan.model_validate(objective_plan_data()).model_dump(),
                'revision': 1, 'results': {}, 'known_ids': ['investigate', 'research', 'recommend']}

    async def test_new_plans_require_objective_but_legacy_plans_still_load(self):
        Plan.model_validate(plan_data())
        model = ScriptedToolModel(responses=[call('InitialPlan', plan_data(), 1), call('InitialPlan', objective_plan_data(), 2)])
        plan = await generate_plan(model, 'Produce a sourced comparison.')
        self.assertIsNotNone(plan.objective)
        self.assertIsNone(plan.objective.markers[0].baseline)
        self.assertFalse(model.responses)

    def test_tasks_must_advance_valid_markers_and_all_markers_need_work(self):
        for ids in ([], ['invented']):
            data = objective_plan_data()
            data['steps'][1]['marker_ids'] = ids
            with self.assertRaises(ValueError):
                Plan.model_validate(data)
        data = objective_plan_data()
        data['objective']['markers'].append({**data['objective']['markers'][0], 'id': 'unassigned'})
        with self.assertRaises(ValueError):
            Plan.model_validate(data)

    def test_replanner_cannot_move_finish_line_after_start(self):
        state = self.state()
        changed = copy.deepcopy(state['plan']['objective'])
        changed['markers'][0]['target'] = 2
        patch = PlanPatch(base_revision=1, action='revise', reason='User requested a larger target.', upsert=[], remove=[], objective=changed)
        # Only an explicit user correction before Start can alter the proposal.
        with self.assertRaises(ValueError):
            apply_patch(state, patch)
        state['last_event'] = {'kind': 'user_constraint'}
        self.assertEqual(apply_patch(state, patch).objective.markers[0].target, 2)
        state['objective_locked'] = True
        with self.assertRaises(ValueError):
            apply_patch(state, patch)
        with self.assertRaises(ValueError):
            validate_replacement(state, {**state['plan'], 'objective': changed})

    async def test_final_review_rejects_task_completion_without_marker_proof(self):
        state = self.state()
        state['plan']['objective']['area'] = 'users'
        marker = state['plan']['objective']['markers'][0]
        marker.update(target=1000, unit='users', metric='Unique activated real users', counting_rule='Exclude test accounts and bots; count people who complete activation.')
        state['results'] = {'research': {'status': 'completed', 'reason': 'The research report proposes an acquisition plan.'}}
        invalid = {'status': 'satisfied', 'reason': 'All tasks have been completed.', 'unmet': [], 'evidence_task_ids': ['research']}
        unsupported = {**invalid, 'marker_checks': [{'marker_id': 'comparison', 'met': True, 'observed': 10,
                       'evidence_task_ids': ['research'], 'reason': 'Only ten activated users are demonstrated.'}]}
        valid = {'status': 'not_satisfied', 'reason': 'Research does not establish actual user acquisition.',
                 'unmet': ['Provide activation data establishing 1,000 qualifying users.'], 'evidence_task_ids': ['research'],
                 'marker_checks': [{'marker_id': 'comparison', 'met': False, 'observed': None, 'evidence_task_ids': [],
                                    'reason': 'Actual user count has not been established.'}]}
        model = ScriptedToolModel(responses=[call('CheckedGoal', invalid, 1), call('CheckedGoal', unsupported, 2), call('CheckedGoal', valid, 3)])
        check = await assess_goal(model, state)
        self.assertEqual(check.status, 'not_satisfied')
        self.assertFalse(model.responses)
        state['goal_check'] = check.model_dump()
        self.assertIn('Observed: unknown', objective_markdown(state))

    def test_marker_evidence_cannot_be_missing_or_invented(self):
        state = self.state()
        for evidence in ([], ['missing']):
            check = MarkerCheck(marker_id='comparison', met=True, observed=1, evidence_task_ids=evidence, reason='The target is allegedly met.')
            with self.assertRaises(ValueError):
                validate_marker_checks(state, [check], True)
        state['results'] = {'research': {'status': 'completed'}}
        check = MarkerCheck(marker_id='comparison', met=True, observed=1, evidence_task_ids=['research'], reason='The accepted report supplies the required comparison.')
        validate_marker_checks(state, [check], True)

    def test_growth_marker_cannot_be_proved_by_a_research_report(self):
        state = self.state()
        state['plan']['objective']['area'] = 'users'
        state['results'] = {'research': {'status': 'completed'}}
        check = MarkerCheck(marker_id='comparison', met=True, observed=1, evidence_task_ids=['research'], reason='The research includes an example user count.')
        with self.assertRaises(ValueError):
            validate_marker_checks(state, [check], True)
        state['plan']['steps'][2]['specialist'] = 'human'
        state['results']['recommend'] = {'status': 'completed', 'evaluation': {'status': 'accepted'}}
        check.evidence_task_ids = ['recommend']
        validate_marker_checks(state, [check], True)
