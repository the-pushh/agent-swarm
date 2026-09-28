"""Chat with your agents. Launch with python -m mvp.terminal."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from dotenv import load_dotenv
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Markdown, RichLog, Static, TabbedContent, TabPane, Tree
from textual.worker import WorkerCancelled, WorkerFailed

from .live import ROOT
from .project import PROJECTS, project_lock, run_command
from .planning import SUPPORTED
from .objectives import objective_markdown
from .activity import research_activity


class ActivityConsole:
    """Keep tool chatter out of the conversation."""
    def __init__(self, app):
        self.app = app

    def print(self, *values, **kwargs):
        message = ' '.join(str(value) for value in values)
        if message.startswith(('Planner:', 'Plan v', 'Reviewing saved results')):
            self.app.coordinator_activity.append(message)
            if message.startswith('Planner:'):
                self.app.coordinator_phase = 'planning' if self.app.current_command == 'create' else 'reviewing plan'
            elif message.startswith('Reviewing saved results'):
                self.app.coordinator_phase = 'checking goal'
            self.app.update_agents()
        self.app.show_agent()


class GoalApp(App):
    TITLE = 'Agent Swarm'
    CSS = '''
    Screen { background: $background; }
    #heading { height: 3; padding: 0 1; }
    #brand { width: 1fr; padding: 1 0; text-style: bold; }
    #inspect-toggle { min-width: 12; }
    #workspace { height: 1fr; }
    #conversation { width: 1fr; }
    #chat { height: 1fr; padding: 1 2; }
    #status { height: 1; margin: 0 2; color: $text-muted; }
    #composer { height: 3; margin: 1 2; }
    #message { width: 1fr; }
    #send { min-width: 8; margin-left: 1; }
    #inspector { width: 45%; border-left: solid $primary; }
    #agents { height: auto; max-height: 5; padding: 1; }
    #inspect-tabs { height: 1fr; }
    TabPane { padding: 0; }
    #goal-map { height: 40%; min-height: 6; }
    #tile-scroll { height: 45%; }
    #agent-grid { layout: grid; grid-size: 2; grid-columns: 1fr 1fr; grid-rows: 8; grid-gutter: 1; height: auto; padding: 1; }
    .agent-tile { width: 100%; height: 8; min-width: 0; }
    #agent-detail-scroll { height: 1fr; }
    #agent-details { padding: 0 1; }
    #details-scroll { height: 1fr; }
    #details { padding: 0 1; }
    '''
    BINDINGS = [('ctrl+i', 'inspect', 'Inspect'), ('ctrl+n', 'new_goal', 'New chat'),
                ('ctrl+x', 'cancel', 'Stop'), ('ctrl+q', 'quit', 'Quit')]

    def __init__(self, projects=PROJECTS, initial_run=None, command_runner=run_command):
        super().__init__()
        self.projects = Path(projects)
        self.initial_run = initial_run
        self.command_runner = command_runner
        self.current_run = None
        self.state = {}
        self.selected_id = None
        self.selected_agent = None
        self.busy = False
        self.operation = None
        self.current_command = None
        self.coordinator_phase = 'idle'
        self.coordinator_activity = []
        self.messages = []
        self._tree_signature = None

    def compose(self) -> ComposeResult:
        with Horizontal(id='heading'):
            yield Static('Agent Swarm', id='brand')
            yield Button('Inspect', id='inspect-toggle')
        with Horizontal(id='workspace'):
            with Vertical(id='conversation'):
                yield RichLog(id='chat', wrap=True, markup=False, min_width=1)
                yield Static('Local · Ctrl+N new chat · Ctrl+Q quit', id='status', markup=False)
                with Horizontal(id='composer'):
                    yield Input(placeholder='What do you want to get done?', id='message')
                    yield Button('Send', id='send', variant='primary')
            with Vertical(id='inspector'):
                yield Static('No agents working.', id='agents', markup=False)
                with TabbedContent(id='inspect-tabs'):
                    with TabPane('Proposed work', id='work-tab'):
                        yield Tree('Proposed work', id='goal-map')
                        with VerticalScroll(id='details-scroll'):
                            yield Markdown('Select a step to inspect its assignment and result.', id='details')
                    with TabPane('Agents', id='agents-tab'):
                        with VerticalScroll(id='tile-scroll'):
                            with Grid(id='agent-grid'):
                                yield Button('Swarm\nCoordinator\nidle', id='coordinator-tile', classes='agent-tile')
                                yield Button('Evaluator', id='evaluator-tile', classes='agent-tile')
                                for index in range(12):
                                    yield Button('', id=f'agent-tile-{index}', classes='agent-tile')
                        with VerticalScroll(id='agent-detail-scroll'):
                            yield Markdown('Select an agent to see its work and activity.', id='agent-details')

    def on_mount(self):
        self.query_one('#inspector').display = False
        self.update_agents()
        self.say('assistant', 'What do you want to get done? I’ll propose the work before starting any agents.')
        if self.initial_run:
            self.open_project(self.initial_run)
        self.query_one('#message', Input).focus()

    def say(self, role, text):
        self.messages.append({'role': role, 'text': text})
        self.draw_message(role, text)

    def draw_message(self, role, text):
        log = self.query_one('#chat', RichLog)
        log.write(Text('You' if role == 'user' else 'Swarm', style='bold cyan' if role == 'user' else 'bold green'))
        log.write(RichMarkdown(text))
        log.write('')

    def redraw_chat(self):
        self.query_one('#chat', RichLog).clear()
        for message in self.messages:
            self.draw_message(message['role'], message['text'])

    def on_resize(self):
        self.call_after_refresh(self.redraw_chat)

    def save_chat(self, directory):
        path = directory/'chat.json'
        temporary = directory/'chat.tmp'
        temporary.write_text(json.dumps(self.messages, indent=2))
        temporary.replace(path)

    def open_project(self, run):
        if self.busy:
            return
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', run):
            self.say('assistant', 'Invalid saved chat name.')
            return
        if not (self.projects/run/'project.sqlite').exists():
            self.say('assistant', 'Saved project not found. Use /open to list saved chats.')
            return
        self.coordinator_activity = []
        self.current_run = run
        self.state = {}
        self.selected_id = None
        self.messages = []
        self.query_one('#chat', RichLog).clear()
        try:
            saved = json.loads((self.projects/run/'chat.json').read_text())
            if isinstance(saved, list):
                for message in saved:
                    if isinstance(message, dict) and message.get('role') in ('user', 'assistant') and isinstance(message.get('text'), str):
                        self.say(message['role'], message['text'])
        except (ValueError, OSError):
            pass
        self.launch('show')

    @on(Input.Submitted, '#message')
    @on(Button.Pressed, '#send')
    async def submit(self):
        field = self.query_one('#message', Input)
        text = field.value.strip()
        if not text:
            return
        field.value = ''
        field.focus()
        self.say('user', text)
        command = text.lower().rstrip('.!')
        if command in ('stop', '/stop'):
            if self.busy:
                await self.action_cancel()
            else:
                self.say('assistant', 'No agents are working right now.')
            return
        if command in ('status', '/status'):
            self.say('assistant', self.status_message())
            return
        if command in ('/goal', 'goal'):
            self.say('assistant', objective_markdown(self.state) or 'This older plan has no measurable markers yet. Start a new project to propose them.')
            return
        if command in ('inspect', '/inspect'):
            self.action_inspect()
            return
        if self.busy:
            self.say('assistant', 'Work is still running. Say “stop” first, then send your change again.')
            return
        if command == '/new':
            self.action_new_goal()
        elif command == '/open':
            runs = sorted(p.name for p in self.projects.glob('*') if (p/'project.sqlite').exists())
            self.say('assistant', 'Open a saved chat with `/open NAME`.\n\n' + ('\n'.join('- ' + r for r in runs) or 'No saved chats yet.'))
        elif text.startswith('/open '):
            self.open_project(text[6:].strip())
        elif command in ('help', '/help'):
            self.say('assistant', 'Send a goal, review the proposed work, then say **start** for one ready task, or **start 4** for task 4. Send changes here whenever work is stopped. **Inspect** shows assignments and reports. For human tasks, send **done: [evidence]** to request evaluation. Say **stop**, **resume**, or **status**. Use `/goal` to inspect measurable targets. Use `/new` for a new goal and `/open` for saved chats.')
        elif re.match(r"(?:/?done|i(?: have|'ve)? done(?: the work)?|i(?: have|'ve)? finished)\b", command):
            if self.state.get('status') != 'awaiting_human':
                self.say('assistant', 'Start a human task first; then submit what you did for evaluation.')
                return
            evidence = re.sub(r"^(?:/?done|i(?: have|'ve)? done(?: the work)?|i(?: have|'ve)? finished)\b[ :,-]*", '', text, flags=re.I).strip()
            if len(evidence) < 20:
                self.say('assistant', 'Paste evidence of your work: code, results, or the artifact text. Say **done: [evidence]**. A completion claim or link alone is not enough to verify it.')
            else:
                self.launch('submit', evidence=evidence)
        elif re.fullmatch(r'/?start(?:\s+(?:task\s+)?\d+)?', command) or command in ('go', 'go ahead'):
            if self.state.get('status') == 'awaiting_review':
                numbers = re.findall(r'\d+', command)
                tasks = self.tasks()
                if numbers and not 1 <= int(numbers[0]) <= len(tasks):
                    self.say('assistant', f'There is no task {numbers[0]}. Use the numbers in the current proposed work.')
                    return
                task = tasks[int(numbers[0]) - 1]['id'] if numbers else None
                self.launch('start', task=task, one=True)
            else:
                self.say('assistant', 'There is no plan waiting for approval. Send a goal or a change first.')
        elif re.match(r'/?start\b', command):
            self.say('assistant', 'Say **start** for the next ready task, or **start 4** for task 4. This does not change the plan.')
        elif command in ('resume', 'recover', '/resume'):
            if self.state.get('resume_available'):
                self.launch('recover')
            else:
                self.say('assistant', 'No interrupted work to resume. ' + self.status_message())
        elif command == '/check':
            if self.state.get('status') in ('tasks_completed', 'goal_satisfied', 'needs_attention'):
                self.launch('check')
            else:
                self.say('assistant', 'Finish the proposed work before checking the goal.')
        elif text.startswith('/'):
            self.say('assistant', 'Unknown command. Use /help for the short list.')
        elif not self.current_run:
            if not 10 <= len(text) <= 2000:
                self.say('assistant', 'Describe your goal in 10–2,000 characters.')
                return
            self.current_run = 'goal-' + uuid4().hex[:8]
            self.launch('create', goal=text)
        elif self.state.get('resume_available'):
            self.say('assistant', 'Say “resume” to recover the interrupted work before changing the plan.')
        elif self.state.get('status') == 'awaiting_human':
            self.say('assistant', 'Your task is waiting for evidence. Send **done: [your work, code, or results]** so the evaluator can check it.')
        elif self.state.get('status') in ('awaiting_review', 'blocked', 'paused', 'tasks_completed', 'needs_attention', 'goal_satisfied'):
            if not 10 <= len(text) <= 2000:
                self.say('assistant', 'Describe your change in 10–2,000 characters.')
                return
            self.launch('revise', constraint=text)
        else:
            self.say('assistant', 'Use /new to start a fresh goal, or /open to open saved work.')

    def launch(self, command, **kwargs):
        if self.busy:
            return
        self.busy = True
        self.current_command = command
        self.coordinator_phase = {'create': 'planning', 'revise': 'reviewing plan', 'check': 'checking goal', 'show': 'loading', 'submit': 'awaiting evaluator'}.get(command, 'coordinating')
        self.update_agents()
        self.query_one('#status', Static).update({'create': 'Thinking through the work…', 'revise': 'Updating the plan…', 'show': 'Opening saved work…'}.get(command, 'Agents are working · Say stop to pause'))
        self.operation = self.perform(command, kwargs)

    @work(exclusive=True, exit_on_error=False)
    async def perform(self, command, kwargs):
        directory = self.projects/self.current_run
        args = SimpleNamespace(command=command, json=False, revision=self.state.get('revision'), **kwargs)
        try:
            if command != 'show' and not os.getenv('OPENROUTER_API_KEY'):
                raise ValueError('Set OPENROUTER_API_KEY in .env before making model calls.')
            if command == 'create':
                directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            elif not (directory/'project.sqlite').exists():
                raise ValueError('Saved project not found.')
            with project_lock(directory):
                self.save_chat(directory)
                await self.command_runner(args, directory, ActivityConsole(self), self.apply_state)
                if command == 'show' and not self.messages and self.state.get('goal'):
                    self.say('user', self.state['goal'])
                if command == 'submit' and self.state.get('human_reviews'):
                    last = self.state['human_reviews'][-1]
                    if last['status'] == 'accepted':
                        self.say('assistant', '**Evaluation accepted.** ' + last['summary'])
                outcome = self.outcome_message()
                if command != 'show' or not self.messages or self.messages[-1] != {'role': 'assistant', 'text': outcome}:
                    self.say('assistant', outcome)
                self.save_chat(directory)
        except asyncio.CancelledError:
            self.say('assistant', 'Stopped. Saved work is kept. Say “resume” to continue.')
            if directory.exists():
                self.save_chat(directory)
            raise
        except Exception as error:
            if isinstance(error, ModelCallLimitExceededError):
                message = f'Swarm reached its local model-call budget: {error}. This is an app limit, not a provider rate limit.'
            else:
                message = str(error) if isinstance(error, (ValueError, OSError)) else f'Operation failed ({type(error).__name__}).'
            self.say('assistant', message + (' Say “resume” to continue saved work.' if self.state.get('resume_available') else ''))
            if (directory/'project.sqlite').exists():
                self.save_chat(directory)
            if command == 'create' and not (directory/'project.sqlite').exists():
                self.current_run = None
        finally:
            self.busy = False
            self.apply_state(self.state)
            self.query_one('#status', Static).update('Saved locally · /help for commands' if self.current_run else 'Local · /help for commands')

    def status_message(self, include_work=True):
        if self.busy and include_work:
            active = self.state.get('active_task')
            step = next((s for s in self.state.get('plan', {}).get('steps', []) if s['id'] == active), None)
            return f"Working on **{step['title']}** with {step['owner']}." if step else 'Working on your request. Open Inspect for assignments.'
        if self.state.get('resume_available'):
            return 'Work was interrupted. Say **resume** to continue.'
        return {'awaiting_human': 'Your turn: complete the human task, then send **done: [evidence]** for evaluation.',
                'awaiting_review': 'The plan is ready. Say **start**, or tell me what to change.',
                'goal_satisfied': 'The goal check passed. Inspect the completed steps to read the reports.',
                'needs_attention': 'The goal still needs work. Tell me what to change.',
                'blocked': 'Work is blocked. Tell me how you want to change the plan.',
                'paused': 'Work is paused. Tell me how you want to change the plan.',
                'tasks_completed': 'Tasks finished. Say /check to check the goal.'}.get(self.state.get('status'), 'Send a goal to get started.')

    def outcome_message(self):
        if self.state.get('status') == 'awaiting_human':
            step = next(s for s in self.tasks() if s['id'] == self.state['human_task'])
            text = f"Your turn: **{step['title']}**\n\n{step['instruction']}\n\n**Done when:** {step['done_when']}\n\nFollow the research in Inspect. Then send **done: [evidence of your work]**. The evaluator must accept it before the task is complete."
            reviews = [r for r in self.state.get('human_reviews', []) if r['task_id'] == step['id']]
            if reviews:
                last = reviews[-1]
                text += '\n\n**Evaluation: ' + last['status'].replace('_', ' ') + '**\n\n' + last['summary']
                text += '\n\n' + '\n'.join('- ' + c for c in last['corrections'])
            return text
        if self.state.get('status') == 'awaiting_review':
            tasks = self.tasks()
            lines = [f"{i}. **{s['title']}** — {s['owner']} → {s['specialist']} ({self.task_status(s)})" for i, s in enumerate(tasks, 1)]
            objective = self.state.get('plan', {}).get('objective')
            finish = ''
            if objective:
                finish = '**Finish line:** ' + objective['outcome'] + '\n\n'
                finish += '\n'.join(f"- {m['name']}: **{m['target']:g} {m['unit']}**" for m in objective['markers'])
                finish += '\n\nUse **/goal** for counting rules and evidence requirements. Starting approves these markers.\n\n'
            return finish + 'Here’s the proposed work:\n\n' + ('\n'.join(lines) or 'No remaining tasks.') + '\n\nSay **start** for the next ready task, or **start 4** to select task 4. You can also tell me what to change.'
        check = self.state.get('goal_check') or {}
        detail = check.get('reason') or self.state.get('reason', '')
        return self.status_message(include_work=False) + ('\n\n' + detail if detail else '') + ('\n\n' + objective_markdown(self.state) if self.state.get('goal_check') else '')

    def apply_state(self, state):
        self.state = state
        steps = state.get('plan', {}).get('steps', [])
        results = state.get('results', {})
        active = state.get('active_task') if self.busy else None
        step = next((s for s in steps if s['id'] == active and s['id'] not in results), None)
        self.query_one('#agents', Static).update(f"{'Evaluator' if state.get('evaluating') else step['owner']} working on: {step['title']}" if step else ('Swarm · ' + self.coordinator_status() if self.busy and self.current_command != 'show' else 'No agents working right now.'))
        signature = json.dumps([steps, results, active], sort_keys=True)
        if signature != self._tree_signature:
            tree = self.query_one('#goal-map', Tree)
            tree.clear()
            tree.root.set_label('Proposed work')
            nodes = {None: tree.root}
            pending = list(steps)
            while pending:
                ready = [s for s in pending if s.get('parent_id') in nodes]
                if not ready:
                    break
                for item in ready:
                    status = results.get(item['id'], {}).get('status', 'working' if item['id'] == active else 'proposed')
                    if item['kind'] == 'task' and item['specialist'] not in SUPPORTED and status == 'proposed':
                        status = 'unavailable'
                    label = item['title'] if item['kind'] == 'group' else f"{self.tasks().index(item) + 1}. [{status}] {item['title']}"
                    nodes[item['id']] = nodes[item.get('parent_id')].add(Text(label), data=item['id'], expand=True)
                    pending.remove(item)
            for key, result in results.items():
                if key not in nodes:
                    tree.root.add_leaf(Text(f"{key} · {result['status']} (previous attempt)"), data=key)
            tree.root.expand()
            self._tree_signature = signature
        self.show_selected()
        self.update_agents()

    @on(Tree.NodeSelected, '#goal-map')
    def select_step(self, event):
        self.selected_id = event.node.data
        self.show_selected()

    def show_selected(self):
        plan = self.state.get('plan', {})
        step = next((s for s in plan.get('steps', []) if s['id'] == self.selected_id), None)
        result = self.state.get('results', {}).get(self.selected_id, {})
        if step:
            details = (f"## {step['title']}\n\n**Owner:** {step['owner']}\n\n**Agent:** {step['specialist'] or 'group'}\n\n"
                       f"{step['instruction']}\n\n**Done when:** {step['done_when']}\n\n**After:** {', '.join(step['depends_on']) or 'Ready to start'}")
        else:
            details = '## Goal\n\n' + self.state.get('goal', 'Send a goal in chat.') + '\n\n' + plan.get('success_criteria', '')
            details += '\n\n' + objective_markdown(self.state)
            check = self.state.get('goal_check') or {}
            if check.get('reason'):
                details += '\n\n**Goal check:** ' + check['reason']
            details += ''.join('\n\n**Still needed:** ' + item for item in check.get('unmet', []))
        if result.get('reason'):
            details += '\n\n**Outcome:** ' + result['reason']
        # Only IDs present in validated project state can select a local report.
        if self.current_run and self.selected_id and (step or result):
            report = self.projects/self.current_run/'tasks'/self.selected_id/'report.md'
            try:
                if report.exists():
                    details += '\n\n---\n\n' + report.read_text()
            except OSError:
                details += '\n\nThe saved report could not be read.'
        self.query_one('#details', Markdown).update(details)

    def tasks(self):
        return [s for s in self.state.get('plan', {}).get('steps', []) if s['kind'] == 'task']

    def task_status(self, step):
        result = self.state.get('results', {}).get(step['id'])
        if result:
            return result['status']
        if self.busy and self.state.get('active_task') == step['id']:
            return 'evaluating' if self.state.get('evaluating') else 'working'
        if self.state.get('human_task') == step['id']:
            return 'your turn'
        if step['specialist'] not in SUPPORTED:
            return 'unavailable'
        if any(self.state.get('results', {}).get(d, {}).get('status') != 'completed' for d in step['depends_on']):
            return 'waiting'
        return 'ready'

    def coordinator_status(self):
        if self.busy:
            if self.state.get('active_task') and self.current_command != 'show':
                return 'awaiting evaluator' if self.state.get('evaluating') else 'coordinating'
            return self.coordinator_phase
        if self.state.get('resume_available'):
            return 'interrupted'
        return {'awaiting_review': 'waiting for you', 'awaiting_human': 'waiting for your work',
                'goal_satisfied': 'goal verified', 'needs_attention': 'needs attention',
                'blocked': 'blocked', 'paused': 'paused'}.get(self.state.get('status'), 'idle')

    def update_agents(self):
        coordinator = self.query_one('#coordinator-tile', Button)
        coordinator.label = Text(f'Swarm\nCoordinator\n{self.coordinator_status()}')
        coordinator.variant = 'success' if self.busy and self.current_command != 'show' else 'default'
        tasks = self.tasks()
        evaluator = self.query_one('#evaluator-tile', Button)
        evaluator.display = any(s['specialist'] == 'human' for s in tasks)
        evaluating = self.busy and self.state.get('evaluating')
        evaluation_status = 'evaluating' if evaluating else ('review saved' if self.state.get('human_reviews') else 'waiting for evidence')
        evaluator.label = Text(f'Evaluator\nWork verification\n{evaluation_status}')
        evaluator.variant = 'success' if evaluating else 'default'
        for index in range(12):
            tile = self.query_one(f'#agent-tile-{index}', Button)
            tile.display = index < len(tasks) and tasks[index]['specialist'] != 'human'
            if index < len(tasks):
                step = tasks[index]
                status = self.task_status(step)
                role = step['owner'][:20]
                tile.label = Text(f"{index + 1}. {role}\n{step['specialist']}\n{status}")
                tile.variant = 'success' if status in ('working', 'evaluating') else 'default'
        self.show_agent()

    @on(Button.Pressed, '.agent-tile')
    def select_agent(self, event):
        if event.button.id in ('coordinator-tile', 'evaluator-tile'):
            self.selected_agent = '@swarm' if event.button.id == 'coordinator-tile' else '@evaluator'
            self.show_agent()
            return
        index = int(event.button.id.rsplit('-', 1)[1])
        self.selected_agent = self.tasks()[index]['id']
        self.show_agent()

    def show_agent(self):
        if self.selected_agent == '@swarm':
            details = '## Swarm · Coordinator\n\n**' + self.coordinator_status() + '**\n\nProposes the goal and plan, delegates work, reviews results, and keeps the project aligned with its markers.'
            if self.state.get('goal'):
                details += '\n\n**Goal:** ' + self.state['goal']
            if self.coordinator_activity:
                details += '\n\n### Current session\n\n' + '\n'.join('- ' + item for item in self.coordinator_activity[-30:])
            if self.state.get('history'):
                details += '\n\n### Saved planning summaries\n\n' + '\n'.join('- ' + item for item in self.state['history'])
            if self.state.get('reason'):
                details += '\n\n**Latest decision:** ' + self.state['reason']
            self.query_one('#agent-details', Markdown).update(details)
            return
        selected = self.selected_agent
        if selected == '@evaluator':
            reviews = self.state.get('human_reviews', [])
            selected = self.state.get('human_task') or (reviews[-1]['task_id'] if reviews else None)
            if not selected:
                selected = next((s['id'] for s in self.tasks() if s['specialist'] == 'human'), None)
        step = next((s for s in self.tasks() if s['id'] == selected), None)
        if step is None:
            self.query_one('#agent-details', Markdown).update('Select an agent tile to see its assignment, tool activity and review summaries.')
            return
        heading = 'Evaluator · Work verification' if step['specialist'] == 'human' else step['owner']
        status = self.task_status(step)
        if step['specialist'] == 'human':
            reviews = [r for r in self.state.get('human_reviews', []) if r['task_id'] == step['id']]
            status = 'evaluating' if self.busy and self.state.get('evaluating') else (reviews[-1]['status'].replace('_', ' ') if reviews else 'waiting for evidence')
        details = (f"## {heading}\n\n**{status}**\n\n"
                   f"**Task:** {step['title']}\n\n{step['instruction']}\n\n**Done when:** {step['done_when']}")
        result = self.state.get('results', {}).get(step['id'], {})
        for review in self.state.get('human_reviews', []):
            if review['task_id'] == step['id']:
                details += '\n\n### Evaluation: ' + review['status'].replace('_', ' ') + '\n\n' + review['summary']
                details += '\n\n' + '\n'.join('- ' + c for c in review['corrections'])
                details += '\n\n**Submitted evidence:**\n\n' + review['submission']
        if self.current_run:
            path = self.projects/self.current_run/'tasks'/step['id']/'run.json'
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                record = {}
            events = research_activity(record, step['title'])
            if events:
                details += '\n\n### Activity\n\n' + '\n'.join('- ' + event for event in events)
            reason = result.get('reason') or record.get('reason')
            if reason:
                details += '\n\n**Review summary:** ' + reason
            report = path.with_name('report.md')
            try:
                if report.exists():
                    details += '\n\n---\n\n' + report.read_text()
            except OSError:
                pass
        self.query_one('#agent-details', Markdown).update(details)

    @on(Button.Pressed, '#inspect-toggle')
    def action_inspect(self):
        pane = self.query_one('#inspector')
        pane.display = not pane.display
        self.call_after_refresh(self.redraw_chat)
        self.query_one('#inspect-toggle', Button).label = 'Close' if pane.display else 'Inspect'

    def action_new_goal(self):
        if self.busy:
            self.say('assistant', 'Say “stop” before starting a new chat.')
            return
        self.current_run = None
        self.coordinator_activity = []
        self.selected_id = None
        self.messages = []
        self.query_one('#chat', RichLog).clear()
        self.apply_state({})
        self.say('assistant', 'What do you want to get done?')
        self.query_one('#message', Input).focus()

    async def action_cancel(self):
        if self.operation and self.busy:
            self.operation.cancel()
            try:
                await self.operation.wait()
            except (WorkerCancelled, WorkerFailed):
                pass

    async def action_quit(self):
        await self.action_cancel()
        self.exit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', help='Open a saved project')
    args = parser.parse_args()
    load_dotenv(ROOT/'.env', override=False)
    GoalApp(initial_run=args.run).run()


if __name__ == '__main__':
    main()
