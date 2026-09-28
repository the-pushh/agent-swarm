"""Stage 2 contracts through the actual LangChain loop, without paid calls."""
import importlib.util
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

AVAILABLE = importlib.util.find_spec('langchain_openrouter') is not None
if AVAILABLE:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from mvp.research_agents import (IncompleteResult, ResearchBrief, Verdict, accept_verdict,
                                     build_agents, structured_result, validate_brief)
    from mvp.research_tools import ResearchTools, public_url
    from mvp.live import report_markdown, execute

    class ScriptedToolModel(BaseChatModel):
        responses: list

        @property
        def _llm_type(self):
            return 'stage2-test-model'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if not self.responses:
                raise AssertionError('Unexpected model call')
            return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def call(name, args, number):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': str(number), 'type': 'tool_call'}])


def brief_data():
    return {'title': 'Checkpoint comparison', 'findings': [
        {'claim': 'Checkpoints retain the current task state.', 'source_id': 'S1',
         'passage_id': 'P1'},
        {'claim': 'JSON files contain serialized application data.', 'source_id': 'S2',
         'passage_id': 'P1'},
    ], 'recommendation': 'Use checkpointing for this resumable workflow.',
        'limitations': ['These are test sources.']}


def new_record():
    return {'run': 'test', 'question': 'Compare checkpointing and JSON files using sources.',
            'attempts': [], 'sources': {}, 'search_calls': 0, 'page_reads': 0}


@unittest.skipUnless(AVAILABLE, 'Install requirements-mvp.txt in Python 3.12')
class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_or_unstructured_answers_are_not_accepted(self):
        with self.assertRaisesRegex(IncompleteResult, 'truncated'):
            structured_result({'messages': [AIMessage(content='', response_metadata={'finish_reason': 'length'})]}, ResearchBrief)
        with self.assertRaisesRegex(IncompleteResult, 'no valid structured'):
            structured_result({'messages': [AIMessage(content='An ordinary answer')]}, ResearchBrief)

    def tools(self, record):
        def search(query):
            return [{'href': 'https://example.com/checkpoints', 'title': 'Checkpoints', 'body': 'A snippet'},
                    {'href': 'https://example.com/json', 'title': 'JSON', 'body': 'Another snippet'}]
        async def fetch(url):
            return {'url': url, 'text': ('Checkpoints retain the current task state. '
                                        'JSON files contain serialized application data. ' * 3),
                    'truncated': False}
        return ResearchTools(record, lambda: None, lambda *args, **kwargs: None, search=search, fetch=fetch)

    async def test_actual_framework_delegates_reads_sources_and_accepts(self):
        record = new_record()
        model = ScriptedToolModel(responses=[
            call('delegate_research', {'assignment': record['question']}, 1),
            call('search_web', {'query': 'checkpoint official documentation'}, 2),
            call('read_source', {'source_id': 'S1'}, 3),
            call('read_source', {'source_id': 'S2'}, 4),
            call('ResearchBrief', brief_data(), 5),
            call('OwnerVerdict', {'status': 'accepted', 'attempt': 1, 'reason': 'Both claims are grounded in the source text.'}, 6),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with AsyncSqliteSaver.from_conn_string(str(Path(directory) / 'state.sqlite')) as saver:
                owner = build_agents(model, self.tools(record), record, lambda: None,
                                     lambda *args, **kwargs: None, saver)
                result = await owner.ainvoke({'messages': [{'role': 'user', 'content': record['question']}]},
                                             {'configurable': {'thread_id': 'test:owner'}})
        self.assertTrue(accept_verdict(result['structured_response'], record)[0])
        self.assertEqual(len(record['attempts']), 1)
        self.assertEqual(record['search_calls'], 1)
        self.assertEqual(record['page_reads'], 2)

    async def test_owner_can_request_one_correction(self):
        record = new_record()
        bad = brief_data()
        bad['findings'][0]['passage_id'] = 'P999'
        model = ScriptedToolModel(responses=[
            call('delegate_research', {'assignment': record['question']}, 1),
            call('search_web', {'query': 'checkpoint docs'}, 2),
            call('read_source', {'source_id': 'S1'}, 3),
            call('read_source', {'source_id': 'S2'}, 4),
            call('ResearchBrief', bad, 5),
            call('OwnerVerdict', {'status': 'accepted', 'attempt': 1, 'reason': 'Incorrect acceptance should trigger schema feedback.'}, 50),
            call('delegate_research', {'assignment': 'Correct the invented passage ID using the retrieved pages.'}, 6),
            call('ResearchBrief', brief_data(), 7),
            call('OwnerVerdict', {'status': 'accepted', 'attempt': 2, 'reason': 'The corrected evidence supports the report.'}, 8),
        ])
        with tempfile.TemporaryDirectory() as directory:
            async with AsyncSqliteSaver.from_conn_string(str(Path(directory) / 'state.sqlite')) as saver:
                owner = build_agents(model, self.tools(record), record, lambda: None,
                                     lambda *args, **kwargs: None, saver)
                result = await owner.ainvoke({'messages': [{'role': 'user', 'content': record['question']}]},
                                             {'configurable': {'thread_id': 'correction:owner'}})
        self.assertTrue(record['attempts'][0]['issues'])
        self.assertEqual(record['attempts'][1]['issues'], [])
        self.assertTrue(accept_verdict(result['structured_response'], record)[0])

    async def test_search_snippets_and_invented_passages_cannot_pass(self):
        record = new_record()
        tools = self.tools(record)
        await tools.search_web('test search')
        brief = ResearchBrief(**brief_data())
        self.assertTrue(validate_brief(brief, record['sources']))
        await tools.read_source('S1')
        await tools.read_source('S2')
        self.assertEqual(validate_brief(brief, record['sources']), [])
        brief.findings[0].passage_id = 'P999'
        self.assertTrue(validate_brief(brief, record['sources']))

    async def test_acceptance_requires_latest_successful_delegation(self):
        record = new_record()
        verdict = Verdict(status='accepted', attempt=0, reason='I already know the answer.')
        self.assertFalse(accept_verdict(verdict, record)[0])
        record['attempts'] = [{'brief': brief_data(), 'issues': []}, {'brief': None, 'issues': ['failed']}]
        verdict.attempt = 1
        self.assertFalse(accept_verdict(verdict, record)[0])
        verdict.attempt = 2
        self.assertFalse(accept_verdict(verdict, record)[0])

    async def test_failed_page_and_budget_exhaustion_are_visible(self):
        record = new_record()
        tools = self.tools(record)
        async def fail(url):
            raise RuntimeError('Sensitive exception detail must not leak')
        tools.fetch = fail
        await tools.search_web('source query')
        failed = await tools.read_source('S1')
        self.assertIn('error', failed)
        self.assertNotIn('Sensitive', str(failed))
        self.assertFalse(record['sources']['S1']['read'])
        record['search_calls'] = 4
        self.assertIn('error', await tools.search_web('another search'))
        record['page_reads'] = 8
        self.assertIn('error', await tools.read_source('S2'))
        self.assertIn('error', await tools.read_source('S999'))

    async def test_private_urls_and_non_http_are_rejected(self):
        for url in ('file:///etc/passwd', 'http://user:secret@example.com', 'https://example.com:8000'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        with patch('mvp.research_tools.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError):
                public_url('https://example.com')

    async def test_provider_failure_saves_safe_failed_report(self):
        from rich.console import Console
        import io
        import json
        with tempfile.TemporaryDirectory() as directory:
            with patch('mvp.live.ChatOpenRouter', side_effect=RuntimeError('secret-key-must-not-leak')):
                result = await execute('A public research task', Path(directory), 'test-model', Console(file=io.StringIO()))
            saved = json.loads((Path(directory) / 'run.json').read_text())
            self.assertEqual(saved['status'], 'failed')
            self.assertNotIn('secret-key', str(saved))
            self.assertIn('NOT ACCEPTED', (Path(directory) / 'report.md').read_text())
            self.assertEqual(result['status'], 'failed')

    async def test_timeout_and_cancellation_keep_partial_record(self):
        from rich.console import Console
        import io
        import json
        class WaitingOwner:
            async def ainvoke(self, *args, **kwargs):
                await asyncio.sleep(60)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch('mvp.live.ChatOpenRouter'), patch('mvp.live.build_agents', return_value=WaitingOwner()):
                timed = await execute('A public research task', path, 'test-model', Console(file=io.StringIO()), timeout=0.02)
                self.assertEqual(timed['status'], 'failed')
                self.assertIn('time limit', timed['reason'])
                task = asyncio.create_task(execute('A public research task', path, 'test-model', Console(file=io.StringIO())))
                await asyncio.sleep(0.03)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            saved = json.loads((path / 'run.json').read_text())
            self.assertEqual(saved['status'], 'cancelled')
            self.assertIn('NOT ACCEPTED', (path / 'report.md').read_text())

    async def test_framework_model_limit_stops_invalid_tool_loop(self):
        record = new_record()
        model = ScriptedToolModel(responses=[call('unregistered_tool', {}, n) for n in range(6)])
        with tempfile.TemporaryDirectory() as directory:
            async with AsyncSqliteSaver.from_conn_string(str(Path(directory) / 'state.sqlite')) as saver:
                owner = build_agents(model, self.tools(record), record, lambda: None,
                                     lambda *args, **kwargs: None, saver)
                with self.assertRaisesRegex(Exception, 'Model call limits exceeded'):
                    await owner.ainvoke({'messages': [{'role': 'user', 'content': record['question']}]},
                                        {'configurable': {'thread_id': 'limit:owner'}})
        self.assertEqual(record['attempts'], [])


if __name__ == '__main__':
    unittest.main()
