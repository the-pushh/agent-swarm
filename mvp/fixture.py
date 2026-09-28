"""Scripted offline data for the Stage 1 orchestration proof; no research claims."""


def research(task):
    if task['id'] == 'production' and task['approach'] == 'wholesale':
        return {'status': 'blocked', 'reason': 'Fixture wholesalers require 100 units; the goal allows 20.'}
    if task['id'] == 'production':
        return {'status': 'completed', 'artifact': 'Fixture result: compare local printers for a 20-shirt sample.',
                'evidence': ['fixture://local-printer'], 'limitations': ['Scripted data, not a verified supplier.']}
    return {'status': 'completed', 'artifact': 'Fixture result: compare three positioning options for the collection.',
            'evidence': ['fixture://positioning'], 'limitations': ['Scripted data, not market research.']}


def initial_state():
    return {
        'goal': 'Prepare a launch recommendation for a 20-shirt collection',
        'mode': 'offline-fixture', 'revision': 1, 'status': 'review',
        'tasks': {
            'positioning': {'id': 'positioning', 'title': 'Compare positioning options',
                            'owner': 'Market Lead', 'specialist': 'research', 'approach': 'compare',
                            'status': 'pending', 'attempts': 0},
            'production': {'id': 'production', 'title': 'Find a production route',
                           'owner': 'Sourcing Lead', 'specialist': 'research', 'approach': 'wholesale',
                           'status': 'pending', 'attempts': 0},
        },
        'history': ['Plan v1: investigate positioning and wholesale production independently.'],
        'events': [],
    }
