"""Evaluator grounding and required corrections through the actual agent schema."""
import importlib.util
import unittest

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from mvp.evaluation import evaluate_work, research_context
    from test_mvp_research import ScriptedToolModel, call


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt')
class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_invented_research_and_acceptance_omitting_research(self):
        payload = {'research': {'research-a': {'status': 'completed'}, 'research-b': {'status': 'completed'}},
                   'step': {'done_when': 'Code matches the research.'}, 'submission': 'Submitted implementation code and test output.'}
        invalid = {'status': 'accepted', 'summary': 'The evidence meets all of the requirements.', 'corrections': [], 'research_task_ids': ['invented']}
        partial = {**invalid, 'research_task_ids': ['research-a']}
        valid = {**invalid, 'research_task_ids': ['research-a', 'research-b']}
        model = ScriptedToolModel(responses=[call('CheckedWork', invalid, 1), call('CheckedWork', partial, 2), call('CheckedWork', valid, 3)])
        result = await evaluate_work(model, payload)
        self.assertEqual(result.status, 'accepted')
        self.assertFalse(model.responses)

    async def test_rejected_work_requires_actionable_feedback(self):
        invalid = {'status': 'insufficient_evidence', 'summary': 'There is no inspectable output to evaluate.', 'corrections': [], 'research_task_ids': []}
        valid = {**invalid, 'corrections': ['Provide the code and relevant test output.']}
        model = ScriptedToolModel(responses=[call('CheckedWork', invalid, 1), call('CheckedWork', valid, 2)])
        result = await evaluate_work(model, {'research': {}, 'submission': 'I have done all of the work.'})
        self.assertEqual(result.status, 'insufficient_evidence')
        self.assertFalse(model.responses)

    def test_collects_transitive_completed_research_only(self):
        steps = [dict(id='source', specialist='research', depends_on=[]),
                 dict(id='implementation', specialist='human', depends_on=['source']),
                 dict(id='deployment', specialist='human', depends_on=['implementation']),
                 dict(id='unrelated', specialist='research', depends_on=[])]
        state = {'plan': {'steps': steps}, 'results': {key: {'status': 'completed'} for key in ('source', 'implementation', 'unrelated')}}
        self.assertEqual(set(research_context(state, steps[2])), {'source'})
