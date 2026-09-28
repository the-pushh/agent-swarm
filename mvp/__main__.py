"""Stage 1 CLI. Each command can run in a separate process."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from .fixture import initial_state, research
from .workflow import build_graph, resume_command


def render(snapshot, run_id):
    console = Console()
    state = snapshot.values
    console.print('STAGE 1 — OFFLINE FIXTURE (no model calls or live research)', style='bold yellow')
    console.print(Text(f"Run: {run_id} | plan v{state['revision']} | {state['status']}"))
    tree = Tree(Text(state['goal']))
    for task in state['tasks'].values():
        branch = tree.add(Text(f"{task['status'].upper()}  {task['title']} — {task['owner']}"))
        branch.add(Text(f"Research specialist | {task['approach']} | attempts: {task['attempts']}"))
        for key in ('reason', 'artifact'):
            if task.get(key):
                branch.add(Text(task[key]))
    console.print(tree)
    for revision in state['history']:
        console.print(Text(revision))
    for task in snapshot.tasks:
        for item in task.interrupts:
            console.print(Text(item.value['message']))
            console.print(Text('Resume choices: ' + ', '.join(item.value['choices'])))
    if state['status'] == 'completed':
        console.print('Fixture workflow finished. This is not proof of a real business outcome.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='.agent-state/mvp/stage1.sqlite')
    parser.add_argument('--json', action='store_true', help='Machine-readable saved state')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start', help='Create a scripted plan and pause before delegation')
    start.add_argument('--run', default=None)
    for name in ('status', 'resume'):
        command = commands.add_parser(name)
        command.add_argument('run')
        if name == 'resume':
            command.add_argument('choice')
    args = parser.parse_args()
    path = Path(args.db)
    if args.command != 'start' and not path.exists():
        parser.error('No saved database; start a run first')
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = args.run or uuid4().hex[:12]
    config = {'configurable': {'thread_id': run_id}, 'recursion_limit': 30, 'max_concurrency': 2}
    with SqliteSaver.from_conn_string(str(path)) as checkpointer:
        graph = build_graph(checkpointer, research)
        snapshot = graph.get_state(config)
        if args.command == 'start':
            if snapshot.values:
                parser.error('Run already exists; choose another --run or inspect its status')
            graph.invoke(initial_state(), config)
        elif not snapshot.values:
            parser.error('Unknown run ID')
        elif args.command == 'resume':
            try:
                command = resume_command(snapshot, args.choice)
            except ValueError as error:
                parser.error(str(error))
            graph.invoke(command, config)
        snapshot = graph.get_state(config)
        if args.json:
            pending = [item.value for task in snapshot.tasks for item in task.interrupts]
            print(json.dumps({'run': run_id, 'state': snapshot.values, 'pending': pending}))
        else:
            render(snapshot, run_id)


if __name__ == '__main__':
    main()
