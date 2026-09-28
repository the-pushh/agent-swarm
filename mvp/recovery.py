"""Reconcile task artifacts after interruption without replaying finished research."""
import json
from pathlib import Path

from .live import report_markdown
from .objectives import objective_markdown
from .research_agents import ResearchBrief, Verdict, accept_verdict, validate_brief

MAX_RECOVERIES = 3


def task_result(record, directory):
    attempts = record.get('attempts', [])
    brief = attempts[-1].get('brief') if attempts else None
    evidence = {}
    for finding in (brief or {}).get('findings', []):
        source = record.get('sources', {}).get(finding['source_id'], {})
        passage = source.get('passages', {}).get(finding['passage_id'])
        if passage:
            evidence[f"{finding['source_id']}/{finding['passage_id']}"] = {'url': source['url'], 'text': passage}
    return {'status': record['status'], 'reason': record['reason'],
            'brief': brief, 'evidence': evidence,
            'report': str(directory / 'report.md'),
            'sources': {key: source['url'] for key, source in record.get('sources', {}).items() if source.get('read')}}


def reconcile_task(directory, assignment):
    """An existing directory is never a license to repeat provider calls."""
    failed = {'status': 'failed', 'reason': 'Interrupted research preserved. The planner must choose a fresh task ID to retry.',
              'artifact_directory': str(directory), 'recovered': True}
    try:
        if json.loads((directory / 'assignment.json').read_text()) != assignment:
            return {**failed, 'reason': 'Saved assignment differs or is unverified; old artifacts preserved without replay.'}
        record = json.loads((directory / 'run.json').read_text())
        if record['status'] == 'completed':
            verdict = Verdict.model_validate(record['verdict'])
            if not accept_verdict(verdict, record)[0]:
                return failed
            brief = ResearchBrief.model_validate(record['attempts'][-1]['brief'])
            if validate_brief(brief, record['sources']):
                return failed
        elif record['status'] not in ('blocked', 'failed', 'cancelled'):
            return failed
        if not (directory / 'report.md').exists():
            (directory / 'report.md').write_text(report_markdown(record))
        return {**task_result(record, directory), 'recovered': True}
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return failed


def summary_markdown(state):
    lines = ['# Project result', '', state.get('goal', ''), '', f"Status: {state.get('status', 'unknown')}",
             f"Plan version: {state.get('revision', 0)}", '']
    if state.get('plan', {}).get('objective'):
        lines += ['## Measurable objective', '', objective_markdown(state), '']
    check = state.get('goal_check')
    if check:
        lines += ['## Goal review', '', check['reason'], '']
        lines += [f'- Unmet: {item}' for item in check.get('unmet', [])]
        lines += ['', 'This is a model assessment of saved evidence, not independent verification.', '']
    elif state.get('reason'):
        lines += [state['reason'], '']
    lines += ['## Saved work', '']
    active = {s['id'] for s in state.get('plan', {}).get('steps', [])}
    for key, result in state.get('results', {}).items():
        label = '' if key in active else ' (previous attempt)'
        lines.append(f"- {key}{label}: {result['status']}")
        if result.get('report'):
            lines.append(f"  [Report](<{result['report']}>)")
        elif result.get('reason'):
            lines.append(f"  {result['reason']}")
    return '\n'.join(lines) + '\n'
