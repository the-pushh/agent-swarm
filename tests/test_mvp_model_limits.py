"""Structured planning can repair more than two schema errors without looping forever."""
import unittest
import os
from unittest.mock import patch
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from test_mvp_planning import objective_plan_data, plan_data
from test_mvp_research import ScriptedToolModel, call
from mvp.planning import generate_plan


class ModelLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_can_finish_after_three_schema_corrections(self):
        model = ScriptedToolModel(responses=[
            call('InitialPlan', plan_data(), i) for i in range(1, 4)
        ] + [call('InitialPlan', objective_plan_data(), 4)])
        plan = await generate_plan(model, 'Produce a sourced comparison.')
        self.assertIsNotNone(plan.objective)
        self.assertFalse(model.responses)

    async def test_explicit_call_limit_still_stops_invalid_output(self):
        model = ScriptedToolModel(responses=[call('InitialPlan', plan_data(), i) for i in range(3)])
        with patch.dict(os.environ, {'MVP_PLANNER_MAX_CALLS': '2'}):
            with self.assertRaises(ModelCallLimitExceededError):
                await generate_plan(model, 'Produce a sourced comparison.')
        self.assertEqual(len(model.responses), 1)
