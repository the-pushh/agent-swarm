"""Validated initial plans and a persisted review gate over the existing research runner."""
from graphlib import TopologicalSorter, CycleError
from typing import Literal, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research_agents import structured_result
from .objectives import Objective, validate_marker_checks

Specialist = Literal['research', 'web_search', 'browser_use', 'computer_use', 'email', 'calendar', 'cron', 'human']
SUPPORTED = {'research', 'human'}


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    title: str = Field(min_length=3, max_length=160)
    kind: Literal['group', 'task']
    parent_id: str | None = Field(description="Containing group ID, or null for a root step")
    owner: str = Field(min_length=3, max_length=120, description='Responsible agent role, accountable for the outcome')
    specialist: Specialist | None = Field(description="Required for tasks; null for groups")
    instruction: str = Field(min_length=10, max_length=2000)
    done_when: str = Field(min_length=10, max_length=1000)
    marker_ids: list[str] = Field(default_factory=list, max_length=6, description='Objective markers this task advances')
    depends_on: list[str] = Field(max_length=12, description="IDs whose results this task needs; explicitly [] if independent")


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    objective: Objective | None = None  # Older saved plans have no objective contract.
    success_criteria: str = Field(min_length=10, max_length=1500)
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    steps: list[Step] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def valid_graph(self):
        by_id = {step.id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ValueError('Step IDs must be unique')
        leaves = {s.id for s in self.steps if s.kind == 'task'}
        if not leaves:
            raise ValueError('Plan needs at least one task')
        for step in self.steps:
            if step.parent_id is not None:
                parent = by_id.get(step.parent_id)
                if parent is None or parent.kind != 'group':
                    raise ValueError('parent_id must refer to a group in this plan')
            if step.kind == 'group':
                if step.specialist is not None or step.depends_on:
                    raise ValueError('Groups organize tasks; assign specialists and dependencies to tasks only')
                if not any(child.parent_id == step.id for child in self.steps):
                    raise ValueError('Groups must have children')
            elif step.specialist is None:
                raise ValueError('Every task needs a specialist')
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError('Duplicate dependency')
            if any(dep not in leaves for dep in step.depends_on):
                raise ValueError('Dependencies must refer to task IDs in this plan')
        try:
            tuple(TopologicalSorter({s.id: [s.parent_id] if s.parent_id else [] for s in self.steps}).static_order())
            tuple(TopologicalSorter({s.id: s.depends_on for s in self.steps}).static_order())
        except CycleError as error:
            raise ValueError('Hierarchy and dependencies must not contain cycles') from error
        if self.objective:
            markers = {m.id for m in self.objective.markers}
            assigned = set()
            for step in self.steps:
                if step.kind == 'task' and (not step.marker_ids or not set(step.marker_ids) <= markers):
                    raise ValueError('Every task must reference valid objective markers')
                assigned.update(step.marker_ids if step.kind == 'task' else [])
            if assigned != markers:
                raise ValueError('Every objective marker needs work assigned to it')
        return self


class InitialPlan(Plan):
    objective: Objective


PLANNER_PROMPT = '''Create a small, practical INITIAL plan for the supplied goal. Do not interview
or replace the user's goal. Propose a definite objective yourself, within its scope, for review.
This product helps users achieve personal growth, revenue (including their first dollar), or
user growth (including their first 1,000 users). Honor an explicit user goal; do not force a
research-only question into a revenue project. For a vague growth goal choose a modest concrete
first outcome and disclose assumptions. Never invent a user's baseline, business, currency,
deadline, or access to metrics: unknown baseline is null; proposed details are assumptions.
The objective must have 1-6 measurable markers: metric, numeric target, unit, exact counting
rule, time window and required evidence. Preserve explicit quantities such as 1,000 users.
Define "users" (e.g. unique activated people, excluding test accounts and bots), "revenue"
(e.g. received customer payments in a stated currency, excluding test payments/refunds), and
personal growth via observable actions or demonstrated skill rather than vague improvement.
Markers can include intermediate milestones, but must include the actual requested outcome.
A research document, plan or outreach attempt is not earned money or an acquired user.
Every task references marker_ids it advances. Markers measure outcomes, not task count.
For research-only goals use a verifiable deliverable count with explicit quality criteria.
Return InitialPlan with only as many actionable tasks as the goal needs (12 total entries
maximum). There is no desired task count or ratio of automated to human work. Do not pad a plan
with human assignments or impose a research-then-human template. Choose the task mix from the
actual goal, evidence, dependencies and connected capabilities. Use groups only when they clarify a hierarchy. parent_id is containment, depends_on
is execution order: these are different. Dependencies point only to task IDs, never groups.
Give every task a responsible owner role, an explicit specialist, an actionable instruction,
and observable completion criteria. The owner delegates to the specialist and checks the result.
Supported NOW: research, which searches and reads PUBLIC HTML/text sources and produces a cited
brief comparing evidence and making recommendations. It cannot access private accounts, PDFs,
operate apps, build software, send email, book events, or schedule recurring jobs.
Assign human work only when it requires the user: a decision, private access, real-world action,
or execution beyond the connected tools. Automate work that the connected tools can perform,
including additional research, comparisons and source checking after earlier work. A task coming
after research is not automatically human. Human assignments depend on the relevant research
for verification. The USER performs those assignments. Starting a human task waits for the user's
submission, then a dedicated evaluator compares submitted evidence to the research and done_when.
Evaluation is an automatic gate on each human task: do not create a separate research task to
simulate evaluation. Human tasks count as completed only after acceptance. Research-only goals
need no artificial human task. Every human task must depend directly or transitively on research.
Other valid specialists (NOT executable yet): web_search, browser_use, computer_use, email,
calendar, cron. Use research for web research (it already includes search); use human
for work outside the registered capabilities. Represent needed unavailable work honestly;
do not replace a requested real-world action with researching how to do it.
Keep the original goal's full scope. A research brief is not a completed booking, shipped
product or sent message. State assumptions, do not invent user facts or research findings.
Tasks that synthesize, compare prior findings, or say "based on" earlier work MUST list
those earlier task IDs in depends_on. Only those listed results are passed to the task.
Research returns a brief with 2-5 findings, a recommendation and limitations; it can read
at most 8 pages and search at most 4 times per task. Scope each task to fit these limits;
do not promise tables, files, or large surveys beyond this output. Avoid unnecessary dependencies.
This is planning only: no tools can execute the project during this call.'''


async def generate_plan(model, goal):
    planner = create_agent(model, tools=[], system_prompt=PLANNER_PROMPT,
                           response_format=ToolStrategy(InitialPlan),
                           middleware=[ModelCallLimitMiddleware(run_limit=3, exit_behavior='error')])
    result = await planner.ainvoke({'messages': [{'role': 'user', 'content': goal}]},
                                   config={'recursion_limit': 12})
    return structured_result(result, InitialPlan)


class ProjectState(TypedDict):
    goal: str
    plan: dict
    revision: int
    status: str
    results: dict
    history: list[str]
    living: bool
    constraints: list[str]
    last_event: dict
    review_after_replan: bool
    reviews: int
    patches: list[dict]
    known_ids: list[str]
    reason: str
    recoveries: int
    verify_goal: bool
    goal_check: dict
    goal_checks: int
    single_step: bool
    selected_task: str | None
    human_task: str | None
    human_reviews: list[dict]
    objective_locked: bool


def build_project(checkpointer, planner, runner, replanner=None, goal_checker=None, evaluator=None):
    async def draft(state):
        plan = await planner(state['goal'])
        plan = Plan.model_validate(plan)
        return {'plan': plan.model_dump(), 'revision': 1, 'status': 'awaiting_review',
                'results': {}, 'history': ['Created initial plan v1; no tasks have run.'],
                'known_ids': [s.id for s in plan.steps], 'constraints': [], 'reviews': 0, 'patches': []}

    def review(state):
        decision = interrupt({'kind': 'plan_review', 'revision': state['revision'],
                              'message': 'Review or edit the plan, then Start.'})
        if decision['revision'] != state['revision']:
            raise ValueError('Plan changed; review its current revision')
        if decision['action'] == 'edit':
            plan = Plan.model_validate(decision['plan'])
            if state.get('results') or state.get('objective_locked'):
                from .replanning import validate_replacement
                plan = validate_replacement(state, plan)
            return {'plan': plan.model_dump(), 'revision': state['revision'] + 1,
                    'status': 'awaiting_review',
                    'known_ids': sorted(set(state.get('known_ids', [])) | {s.id for s in plan.steps}),
                    'history': state['history'] + [f"Saved edited plan v{state['revision'] + 1}."]}
        if decision['action'] != 'start':
            raise ValueError('Expected edit or start')
        return {'status': 'running', 'objective_locked': True, 'single_step': decision.get('single_step', False),
                'selected_task': decision.get('task_id'), 'review_after_replan': False, 'reason': '', 'history': state['history'] + [f"Started reviewed plan v{state['revision']}."]}

    async def work(state):
        plan = Plan.model_validate(state['plan'])
        results = dict(state['results'])
        pending = [s for s in plan.steps if s.kind == 'task' and s.id not in results]
        ready = next((s for s in pending if (not state.get('selected_task') or s.id == state['selected_task']) and all(results.get(dep, {}).get('status') == 'completed'
                                               for dep in s.depends_on)), None)
        if ready is None:
            if not state.get('living'):
                for step in pending:
                    results[step.id] = {'status': 'blocked', 'reason': 'Required earlier task did not complete.'}
            active = {s.id for s in plan.steps if s.kind == 'task'}
            blocked = pending or any(results.get(key, {}).get('status') != 'completed' for key in active)
            return {'results': results, 'status': 'blocked' if blocked else 'tasks_completed'}
        if state.get('living'):
            from .replanning import MAX_ATTEMPTS
            if len(results) >= MAX_ATTEMPTS:
                return {'status': 'paused', 'reason': 'Task attempt limit reached; saved work is preserved.'}
        if ready.specialist == 'human':
            return {'status': 'awaiting_human', 'human_task': ready.id, 'selected_task': None,
                    'reason': 'Complete the assigned work, then submit evidence for evaluation.'}
        if ready.specialist not in SUPPORTED:
            result = {'status': 'blocked', 'reason': f'{ready.specialist} is not connected in this stage.'}
        else:
            context = {dep: results[dep] for dep in ready.depends_on}
            goal_context = state['goal']
            if state.get('constraints'):
                goal_context += '\nUser constraints:\n' + '\n'.join(state['constraints'])
            result = await runner(ready, goal_context, context)
            if result.get('status') not in ('completed', 'blocked', 'failed', 'cancelled'):
                raise ValueError('Runner returned an invalid task status')
        results[ready.id] = result
        return {'results': results, 'selected_task': None, 'last_event': {'kind': 'task_result', 'task_id': ready.id, 'status': result['status']}}

    async def human_review(state):
        from .evaluation import WorkEvaluation, research_context
        step = next(s for s in state['plan']['steps'] if s['id'] == state['human_task'])
        submission = interrupt({'kind': 'human_work', 'task_id': step['id'], 'revision': state['revision'],
                                'instruction': step['instruction'], 'done_when': step['done_when']})
        if submission.get('task_id') != step['id'] or submission.get('revision') != state['revision']:
            raise ValueError('Human assignment changed; inspect the current task')
        evidence = submission.get('evidence', '').strip()
        research = research_context(state, step)
        if len(evidence) < 20 or not research:
            verdict = WorkEvaluation(status='insufficient_evidence', summary='Work cannot be verified with the evidence available.',
                                     corrections=['Provide concrete work evidence and completed research dependencies.'], research_task_ids=[])
        else:
            try:
                if evaluator is None:
                    raise ValueError('Evaluator unavailable')
                verdict = await evaluator({'goal': state['goal'], 'constraints': state.get('constraints', []),
                                           'step': step, 'objective': state['plan'].get('objective'), 'research': research, 'submission': evidence})
            except Exception as error:
                verdict = WorkEvaluation(status='insufficient_evidence', summary=f'Evaluation could not finish ({type(error).__name__}). Your work remains pending.',
                                         corrections=['Submit again to retry evaluation.'], research_task_ids=[])
        entry = {'task_id': step['id'], 'submission': evidence, **verdict.model_dump()}
        update = {'human_reviews': state.get('human_reviews', []) + [entry], 'reason': verdict.summary,
                  'status': 'awaiting_human'}
        if verdict.status == 'accepted':
            update.update(status='running', human_task=None, results={**state.get('results', {}), step['id']: {
                'status': 'completed', 'reason': verdict.summary, 'submission': evidence,
                'evaluation': verdict.model_dump(), 'verification_scope': 'Review of user-submitted evidence; no independent external inspection.'}},
                last_event={'kind': 'task_result', 'task_id': step['id'], 'status': 'completed'})
        return update

    async def assess(state):
        checks = state.get('goal_checks', 0)
        if checks >= 3:
            return {'status': 'needs_attention', 'reason': 'Goal review limit reached.'}
        try:
            check = await goal_checker(state)
            validate_marker_checks(state, check.marker_checks, check.status == 'satisfied')
            return {'goal_check': check.model_dump(), 'goal_checks': checks + 1,
                    'status': 'goal_satisfied' if check.status == 'satisfied' else 'needs_attention',
                    'reason': check.reason}
        except Exception as error:
            reason = f'Goal review failed ({type(error).__name__}); saved research remains available.'
            return {'status': 'needs_attention', 'goal_checks': checks + 1, 'reason': reason,
                    'goal_check': {'status': 'not_satisfied', 'reason': reason,
                                   'unmet': ['Final goal review could not finish.'], 'evidence_task_ids': []}}

    def after_work(state):
        if state['status'] == 'awaiting_human':
            return 'human_review'
        if state['status'] == 'running':
            if replanner is not None and state.get('living'):
                return 'reflect'
            return 'step_review' if state.get('single_step') and remaining(state) else 'work'
        if state['status'] == 'tasks_completed' and goal_checker is not None and state.get('verify_goal'):
            return 'assess'
        return END

    def remaining(state):
        return any(s['kind'] == 'task' and s['id'] not in state.get('results', {})
                   for s in state['plan']['steps'])

    graph = StateGraph(ProjectState)
    graph.add_node('step_review', lambda state: {'status': 'awaiting_review'})
    graph.add_edge('step_review', 'review')
    graph.add_node('draft', draft)
    graph.add_node('review', review)
    graph.add_node('work', work)
    graph.add_node('human_review', human_review)
    graph.add_conditional_edges('human_review', after_work)
    graph.add_edge(START, 'draft')
    graph.add_edge('draft', 'review')
    graph.add_conditional_edges('review', lambda s: 'review' if s['status'] == 'awaiting_review' else 'work')
    if replanner is not None:
        from .replanning import reflect
        async def reflect_node(state):
            update = await reflect(state, replanner)
            if state.get('single_step') and update.get('status') == 'running' and remaining({**state, **update}):
                update['status'] = 'awaiting_review'
            return update
        graph.add_node('reflect', reflect_node)
        graph.add_node('direction', lambda state: {})
        graph.add_edge('direction', 'reflect')
        graph.add_conditional_edges('reflect', lambda s: 'review' if s['status'] == 'awaiting_review' else ('work' if s['status'] == 'running' else END))
    if goal_checker is not None:
        graph.add_node('assess', assess)
        graph.add_node('assessment_request', lambda state: {})
        graph.add_edge('assessment_request', 'assess')
        graph.add_edge('assess', END)
    graph.add_conditional_edges('work', after_work)
    return graph.compile(checkpointer=checkpointer)


def review_command(snapshot, action, revision, plan=None, *, task_id=None, single_step=False):
    if snapshot.values.get('status') != 'awaiting_review' or not snapshot.interrupts:
        raise ValueError('This project is not waiting for initial plan review')
    if revision != snapshot.values['revision']:
        raise ValueError('Plan revision changed; show the plan before starting or editing')
    if action not in ('start', 'edit'):
        raise ValueError('Expected start or edit')
    payload = {'action': action, 'revision': revision}
    if action == 'start' and (single_step or task_id):
        steps = Plan.model_validate(snapshot.values['plan']).steps
        results = snapshot.values.get('results', {})
        pending = [s for s in steps if s.kind == 'task' and s.id not in results]
        selected = next((s for s in pending if s.id == task_id), None) if task_id else next(
            (s for s in pending if s.specialist in SUPPORTED and all(results.get(d, {}).get('status') == 'completed' for d in s.depends_on)), None)
        if selected is None:
            raise ValueError('That task is not pending.' if task_id else 'No connected task is ready. Inspect dependencies or change the plan.')
        if selected.specialist not in SUPPORTED:
            raise ValueError(f'{selected.specialist} is not connected yet.')
        missing = [d for d in selected.depends_on if results.get(d, {}).get('status') != 'completed']
        if missing:
            raise ValueError('That task must wait for: ' + ', '.join(missing))
        payload.update(task_id=selected.id, single_step=True)
    if action == 'edit':
        proposed = Plan.model_validate(plan)
        if snapshot.values.get('results') or snapshot.values.get('objective_locked'):
            from .replanning import validate_replacement
            proposed = validate_replacement(snapshot.values, proposed)
        payload['plan'] = proposed.model_dump()
    return Command(resume=payload)
