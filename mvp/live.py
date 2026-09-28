"""Stage 2: real API-backed delegation and research, runnable from the terminal."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_openrouter import ChatOpenRouter
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context
from openrouter import OpenRouter
from rich.console import Console
from rich.text import Text

from providers.openrouter import DEFAULT_MODEL
from .research_agents import IncompleteResult, Verdict, accept_verdict, build_agents, structured_result
from .research_tools import ResearchTools

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / '.agent-state' / 'mvp' / 'live'


def save_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


class RunLog(AsyncCallbackHandler):
    """Persist useful events and token totals, never credentials or model reasoning."""
    def __init__(self, directory, record, console):
        self.directory, self.record, self.console = directory, record, console
        self.started = time.monotonic()
        self.seen_model_runs = set()
        self.started_model_runs = set()

    def save(self):
        self.record['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
        save_json(self.directory / 'run.json', self.record)

    def event(self, message, **details):
        self.record['events'].append({'time': datetime.now(timezone.utc).isoformat(),
                                      'message': message, **details})
        self.save()
        self.console.print(Text(message))

    async def on_chat_model_start(self, serialized, messages, *, run_id, tags=None, **kwargs):
        if run_id in self.started_model_runs:
            return
        self.started_model_runs.add(run_id)
        self.record['model_calls'] += 1
        role = 'Research' if 'research' in (tags or []) else 'Owner'
        self.event(f'{role}: model call {self.record["model_calls"]}')

    async def on_llm_end(self, response, *, run_id, **kwargs):
        # Nested runnables can inherit callbacks; never count a model result twice.
        if run_id in self.seen_model_runs:
            return
        self.seen_model_runs.add(run_id)
        for group in response.generations:
            for generation in group:
                message = getattr(generation, 'message', None)
                usage = getattr(message, 'usage_metadata', None)
                if usage:
                    for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                        self.record['usage'][key] += usage.get(key, 0)
                    self.record['usage']['responses_with_usage'] += 1
        self.save()


def report_markdown(record):
    accepted = record['status'] == 'completed'
    lines = ['# Research result' if accepted else '# Research result — NOT ACCEPTED', '',
             f"Task: {record.get('display_question') or record['question']}", '',
             f"Status: {record['status']}", '',
             '## Responsible agent review', '', record.get('reason', 'No review completed.'), '']
    attempts = record['attempts']
    brief = attempts[-1].get('brief') if attempts else None
    if brief:
        lines.extend(['## Findings', ''])
        for finding in brief['findings']:
            source = record['sources'].get(finding['source_id'], {})
            url = source.get('url') if source.get('read') else None
            citation = f"[{finding['source_id']}](<{url}>)" if url else '(source not verified)'
            lines.append(f"- {finding['claim']} {citation}")
        lines.extend(['', '## Interpretation and recommendation', '', brief['recommendation'], '',
                      '## Limitations', ''])
        lines.extend(f'- {item}' for item in brief['limitations'])
        lines.extend(f'- {issue}' for issue in attempts[-1]['issues'])
    lines.extend(['', '## Run details', '',
                  f"Model: {record['model']}. Delegations: {len(attempts)}. "
                  f"Searches: {record['search_calls']}. Page requests: {record['page_reads']}.",
                  '', 'Source text, retrieval times, supporting passage IDs and activity are in run.json.',
                  'Source/passage checks verify provenance; they are not an independent guarantee of factual accuracy.', ''])
    return '\n'.join(lines)


async def execute(question, directory, model_name, console, timeout=300, display_question=None):
    record = {'run': directory.name, 'question': question, 'display_question': display_question or question, 'model': model_name,
              'status': 'running', 'created_at': datetime.now(timezone.utc).isoformat(),
              'attempts': [], 'sources': {}, 'events': [], 'search_calls': 0, 'page_reads': 0,
              'model_calls': 0, 'usage': {'input_tokens': 0, 'output_tokens': 0,
                                         'total_tokens': 0, 'responses_with_usage': 0}}
    log = RunLog(directory, record, console)
    log.event('Stage 2: starting live research (API and public web calls)')
    try:
        async with OpenRouter(api_key=os.getenv('OPENROUTER_API_KEY'),
                              timeout_ms=45000, retry_config=None) as sdk:
            model = ChatOpenRouter(model=model_name, temperature=0, max_tokens=6000,
                                   reasoning={'effort': os.getenv('MVP_REASONING_EFFORT', 'low')},
                                   timeout=45000, max_retries=0, client=sdk)
            resources = ResearchTools(record, log.save, log.event)
            with tracing_context(enabled=False):
                async with AsyncSqliteSaver.from_conn_string(str(directory / 'checkpoints.sqlite')) as saver:
                    owner = build_agents(model, resources, record, log.save, log.event, saver, [log])
                    result = await asyncio.wait_for(owner.ainvoke(
                        {'messages': [{'role': 'user', 'content': question}]},
                        config={'configurable': {'thread_id': f'{directory.name}:owner'},
                                'recursion_limit': 30, 'callbacks': [log], 'tags': ['owner']}), timeout=timeout)
                    verdict = structured_result(result, Verdict)
                    accepted, reason = accept_verdict(verdict, record)
                    record.update(status='completed' if accepted else 'blocked', reason=reason,
                                  verdict=verdict.model_dump())
    except asyncio.CancelledError:
        record.update(status='cancelled', reason='Run cancelled; partial evidence is saved.')
        raise
    except TimeoutError:
        record.update(status='failed', reason='Run time limit reached; partial evidence is saved.')
    except IncompleteResult as error:
        record.update(status='failed', reason=str(error))
    except Exception as error:
        # Provider exceptions may contain request data; persist only a safe class name.
        record.update(status='failed', reason=f'Execution failed ({type(error).__name__}); partial evidence is saved.')
    finally:
        log.event(f"Run {record['status']}")
        (directory / 'report.md').write_text(report_markdown(record))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question', nargs='?', help='One public-web research task')
    parser.add_argument('--run', help='New run name; existing names cannot be overwritten')
    parser.add_argument('--show', metavar='RUN', help='Read saved status without model/network calls')
    parser.add_argument('--timeout', type=int, default=300, help='Whole-run limit in seconds (30–600)')
    args = parser.parse_args()
    if args.show and (args.question or args.run):
        parser.error('--show cannot be combined with a new task')
    run_id = args.show or args.run or uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', run_id):
        parser.error('Run name must be 1–64 letters, digits, underscores or hyphens')
    directory = RUNS / run_id
    console = Console()
    if args.show:
        path = directory / 'run.json'
        if not path.exists():
            parser.error('Unknown run')
        record = json.loads(path.read_text())
    else:
        if not args.question or not 10 <= len(args.question.strip()) <= 4000:
            parser.error('Supply a research task of 10–4000 characters')
        if not 30 <= args.timeout <= 600:
            parser.error('Timeout must be 30–600 seconds')
        load_dotenv(ROOT / '.env', override=False)
        if not os.getenv('OPENROUTER_API_KEY', '').strip():
            parser.error('Set OPENROUTER_API_KEY in .env or the environment')
        if directory.exists():
            parser.error('Run already exists; use --show or choose a new --run name')
        directory.mkdir(parents=True, mode=0o700)
        model_name = os.getenv('MVP_MODEL') or os.getenv('OPENROUTER_MODEL') or DEFAULT_MODEL
        try:
            record = asyncio.run(execute(args.question.strip(), directory, model_name, console, args.timeout))
        except KeyboardInterrupt:
            console.print('Cancelled. Partial results are saved.')
            raise SystemExit(130)
    console.print(Text(f"Saved status: {record['status']} | Run: {run_id}"))
    console.print(Text(record.get('reason', 'No final review yet.')))
    console.print(Text(f"Report: {directory / 'report.md'}"))
    console.print(Text(f"Evidence and activity: {directory / 'run.json'}"))
    if not args.show and record['status'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
