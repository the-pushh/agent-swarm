"""LangGraph owns branching, checkpointing, interrupts and resume.

This stage uses scripted decisions to isolate infrastructure from model quality.
Only task-specific policy and the demonstration revision are implemented here.
"""
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt


def merge_tasks(previous, updates):
    return {**previous, **updates}


class ProjectState(TypedDict):
    goal: str
    mode: str
    revision: int
    status: str
    tasks: Annotated[dict, merge_tasks]
    history: Annotated[list[str], add]
    events: Annotated[list[str], add]


class OwnerState(TypedDict):
    task: dict
    result: dict
    tasks: dict
    events: Annotated[list[str], add]


def build_owner(research):
    def delegate(state):
        task = state['task']
        if task['specialist'] != 'research':
            raise ValueError('Stage 1 only registers the research fixture specialist')
        return {'events': [f"{task['id']}: {task['owner']} delegated to Research (fixture)"]}

    def specialist(state):
        return {'result': research(dict(state['task']))}

    def verify(state):
        result = state['result']
        task = {**state['task'], 'attempts': state['task']['attempts'] + 1}
        if result.get('status') == 'completed' and result.get('artifact') and result.get('evidence'):
            task.update(status='completed', artifact=result['artifact'], evidence=result['evidence'],
                        limitations=result.get('limitations', []))
            task.pop('reason', None)
        else:
            task.update(status='blocked', reason=result.get('reason') or 'Result lacks artifact or evidence')
        return {'tasks': {task['id']: task},
                'events': [f"{task['id']}: owner verdict = {task['status']}"]}

    graph = StateGraph(OwnerState)
    graph.add_node('delegate', delegate)
    graph.add_node('research', specialist)
    graph.add_node('verify', verify)
    graph.add_edge(START, 'delegate')
    graph.add_edge('delegate', 'research')
    graph.add_edge('research', 'verify')
    graph.add_edge('verify', END)
    return graph.compile()


def build_graph(checkpointer, research):
    def review(state):
        decision = interrupt({'kind': 'plan_review', 'message': 'Review the fixture plan before workers start.',
                              'choices': ['start', 'stop']})
        if decision not in ('start', 'stop'):
            raise ValueError('Expected start or stop')
        return {'status': 'running' if decision == 'start' else 'stopped'}

    def dispatch(state):
        if state['status'] == 'stopped':
            return END
        return [Send('owner', {'task': task, 'events': []})
                for task in state['tasks'].values() if task['status'] == 'pending']

    def assess(state):
        blocked = [task for task in state['tasks'].values() if task['status'] == 'blocked']
        return {'status': 'blocked' if blocked else 'completed'}

    def revise(state):
        decision = interrupt({'kind': 'blocker', 'message': 'Fixture wholesale route failed. Try local printers?',
                              'choices': ['local-printers', 'stop']})
        if decision == 'stop':
            return {'status': 'stopped'}
        if decision != 'local-printers':
            raise ValueError('Expected local-printers or stop')
        task = state['tasks']['production']
        if task['status'] != 'blocked' or task['approach'] != 'wholesale':
            raise ValueError('No supported fixture revision remains; inspect the blocker')
        return {'tasks': {'production': {**task, 'status': 'pending', 'approach': 'local-printers'}},
                'revision': state['revision'] + 1, 'status': 'running',
                'history': ['Plan v2: replace wholesale with local printers; keep completed positioning result.']}

    graph = StateGraph(ProjectState)
    graph.add_node('review_plan', review)
    graph.add_node('owner', build_owner(research))
    graph.add_node('assess', assess)
    graph.add_node('revise_plan', revise)
    graph.add_edge(START, 'review_plan')
    graph.add_conditional_edges('review_plan', dispatch, ['owner', END])
    graph.add_edge('owner', 'assess')
    graph.add_conditional_edges('assess', lambda state: 'revise_plan' if state['status'] == 'blocked' else END)
    graph.add_conditional_edges('revise_plan', dispatch, ['owner', END])
    return graph.compile(checkpointer=checkpointer)


def resume_command(snapshot, choice):
    """Reject invalid/stale CLI input before consuming a saved interrupt."""
    pending = [item for task in snapshot.tasks for item in task.interrupts]
    if not pending:
        raise ValueError('This run is not waiting for input')
    if len(pending) != 1 or choice not in pending[0].value['choices']:
        raise ValueError('Choose one of: ' + ', '.join(pending[0].value['choices']))
    return Command(resume=choice)
