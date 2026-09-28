"""Measurable, evidence-backed finish lines proposed before execution."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class Marker(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    name: str = Field(min_length=3, max_length=120)
    metric: str = Field(min_length=3, max_length=200)
    baseline: float | None = Field(default=None, ge=0, allow_inf_nan=False, description='Known starting measurement; null if unknown. Never invent.')
    target: float = Field(gt=0, allow_inf_nan=False, description='Minimum measured threshold to reach; express reduction goals as positive progress achieved')
    unit: str = Field(min_length=1, max_length=80)
    counting_rule: str = Field(min_length=10, max_length=700, description='Exactly what counts, including exclusions and scope')
    evidence_required: str = Field(min_length=10, max_length=700)
    time_window: str = Field(min_length=3, max_length=200, description='Measurement period or explicit proposed timeframe')


class Objective(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: str = Field(min_length=10, max_length=800)
    area: Literal['personal_growth', 'revenue', 'users', 'other']
    markers: list[Marker] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def unique(self):
        if len({m.id for m in self.markers}) != len(self.markers):
            raise ValueError('Marker IDs must be unique')
        return self


class MarkerCheck(BaseModel):
    marker_id: str
    met: bool
    observed: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_task_ids: list[str] = Field(max_length=12)
    reason: str = Field(min_length=10, max_length=1200)


def validate_marker_checks(state, checks, satisfied):
    objective = state.get('plan', {}).get('objective')
    if not objective:
        return
    markers = {m['id']: m for m in objective['markers']}
    if len(checks) != len(markers) or {c.marker_id for c in checks} != set(markers):
        raise ValueError('Evaluate every objective marker exactly once')
    steps = {s['id']: s for s in state['plan']['steps']}
    completed = {key for key, value in state.get('results', {}).items() if value.get('status') == 'completed'}
    for check in checks:
        if any(key not in completed for key in check.evidence_task_ids):
            raise ValueError('Marker evidence must refer to completed tasks')
        if check.met and (check.observed is None or check.observed < markers[check.marker_id]['target'] or not check.evidence_task_ids):
            raise ValueError('A met marker requires evidence and a measured value meeting its target')
        if check.met and objective['area'] in ('revenue', 'users', 'personal_growth'):
            demonstrated = [key for key in check.evidence_task_ids
                            if steps.get(key, {}).get('specialist') == 'human'
                            and state['results'][key].get('evaluation', {}).get('status') == 'accepted'
                            and check.marker_id in steps[key].get('marker_ids', [])]
            if not demonstrated:
                raise ValueError('Growth markers require evaluator-accepted human evidence, not research alone')
    if satisfied and not all(c.met for c in checks):
        raise ValueError('The goal cannot be satisfied until every marker is met')


def objective_markdown(state):
    objective = state.get('plan', {}).get('objective')
    if not objective:
        return ''
    checks = {c['marker_id']: c for c in (state.get('goal_check') or {}).get('marker_checks', [])}
    lines = ['**Finish line:** ' + objective['outcome'], '']
    for marker in objective['markers']:
        check = checks.get(marker['id'])
        progress = 'not yet verified' if not check else ('met' if check['met'] else 'not yet met')
        baseline = 'unknown' if marker.get('baseline') is None else str(marker['baseline'])
        lines += [f"- **{marker['name']}: {marker['target']:g} {marker['unit']}** — {progress}",
                  f"  Metric: {marker['metric']}. Baseline: {baseline}. Window: {marker['time_window']}.",
                  f"  Counts: {marker['counting_rule']}", f"  Evidence: {marker['evidence_required']}"]
        if check:
            measured = 'unknown' if check.get('observed') is None else str(check['observed'])
            lines += [f"  Observed: {measured}. {check['reason']}"]
    return '\n\n'.join(lines)
