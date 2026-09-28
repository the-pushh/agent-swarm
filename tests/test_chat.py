import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import curses

from agents.chat.control_loop import ChatLoop
from agents.chat.agent import ChatSession
from agents.chat.tool_implementations import AgentTools
from agents.chat.demo import complete
from tui.email import EmailTab
from tui.calendar import CalendarTab
from tui.chat import ChatTab
from tui.__main__ import TerminalApp


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.email, self.calendar = EmailTab(root/'email.json'), CalendarTab(root/'calendar.json')
        self.session = ChatSession(self.email, self.calendar, complete)
        self.tools = self.session.tools

    def test_model_cannot_approve_execute_or_call_arbitrary_tools(self):
        for name in ('execute', 'approve', 'human_command', 'shell', 'send'):
            dispatch = Mock()
            model = Mock(return_value=json.dumps({'type': 'tool', 'name': name, 'arguments': {}}))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unsupported tool'):
                ChatLoop(model, dispatch).run([{'role': 'user', 'content': 'Hello'}], {})
            dispatch.assert_not_called()

    def test_agent_reads_mail_and_calendar_on_attention_request(self):
        answer = self.session.ask('What needs my attention?')
        self.assertIn('receipt confirmation', answer)
        self.assertIn('Design review', answer)
        self.assertEqual(len(self.email.items), 4)
        self.assertEqual(len(self.calendar.state['events']), 3)
        self.assertTrue(all(item['status'] == 'pending' for item in self.email.items.values()))

    def test_email_draft_revision_is_local_and_reviewed_exactly(self):
        self.tools.dispatch('email_scan', {})
        result = self.tools.dispatch('email_draft', {'id': 'm1', 'instructions': 'Keep it short'})
        self.assertIn('/approve 1', result['review'])
        self.assertIn('Reference: email:m1', result['review'])
        self.assertIsNone(self.email.items['m1']['provider_draft_id'])
        self.session.ask('/approve 1')
        self.assertEqual(self.email.items['m1']['status'], 'approved')
        self.session.ask('/execute 1')
        self.assertEqual(self.email.items['m1']['status'], 'done')
        self.assertTrue(self.email.items['m1']['provider_draft_id'].startswith('demo-draft'))

    def test_changed_proposal_cannot_use_old_chat_approval(self):
        self.tools.dispatch('email_scan', {})
        self.tools.dispatch('inspect', {'ref': 'email:m1'})
        with self.email.open() as agent:
            agent.state['items']['m1']['draft']['body'] = 'Changed after review'
            agent.checkpoint()
        answer = self.session.ask('/approve email:m1')
        self.assertIn('changed since it was shown', answer)
        self.email.refresh()
        self.assertEqual(self.email.items['m1']['status'], 'pending')

    def test_unreviewed_ref_and_plain_yes_do_not_authorize(self):
        self.tools.dispatch('email_scan', {})
        self.assertIn('show this proposal first', self.session.ask('/approve email:m1'))
        self.session.ask('yes, go ahead')
        self.assertEqual(self.email.items['m1']['status'], 'pending')

    def test_filters_require_apply_and_preserve_existing_lists(self):
        self.email.save_filters({'include_topics': ['AI']})
        result = self.tools.dispatch('email_filters', {'add': {'exclude_senders': ['sales@example.com']}})
        self.assertEqual(self.email.newsletter_filters['exclude_senders'], [])
        self.session.ask('/show 1')
        self.assertEqual(self.session.messages[-1]['role'], 'review')
        shown = self.session.messages[-1]['content']
        self.assertIn('sales@example.com', shown)
        self.assertIn('/apply 1', shown)
        self.session.ask('/apply 1')
        self.assertEqual(self.email.newsletter_filters['exclude_senders'], ['sales@example.com'])
        self.assertEqual(self.email.newsletter_filters['include_topics'], ['AI'])

    def test_stale_filter_proposal_cannot_overwrite_manual_changes(self):
        result = self.tools.dispatch('email_filters', {'add': {'include_topics': ['AI']}})
        self.email.save_filters({'include_topics': ['Security']})
        answer = self.session.ask('/apply ' + result['ref'])
        self.assertIn('changed since review', answer)
        self.assertEqual(self.email.newsletter_filters['include_topics'], ['Security'])

    def test_calendar_proposal_does_not_book_until_explicit_human_commands(self):
        before = len(self.calendar.calendar.events)
        result = self.tools.dispatch('calendar_plan', {'kind': 'block', 'title': 'Focus', 'duration': 60, 'days': 7})
        ref = result['options'][0]['ref']
        self.assertEqual(len(self.calendar.calendar.events), before)
        self.session.ask('/approve ' + ref)
        self.assertEqual(len(self.calendar.calendar.events), before)
        self.session.ask('/execute ' + ref)
        self.assertEqual(len(self.calendar.calendar.events), before + 1)

    def test_calendar_unknown_attendee_availability_visible_in_review(self):
        result = self.tools.dispatch('calendar_plan', {'kind': 'meeting', 'title': 'Meet', 'attendees': 'alex@example.com', 'duration': 30, 'days': 7})
        self.assertIn('Unknown availability: alex@example.com', result['options'][0]['review'])
        self.assertIn('invitations/updates', result['options'][0]['review'])

    def test_bounded_loop_and_history(self):
        model = Mock(return_value=json.dumps({'type': 'tool', 'name': 'email_list', 'arguments': {}}))
        dispatch = Mock(return_value={'items': []})
        result = ChatLoop(model, dispatch, max_steps=2).run([{'role': 'user', 'content': 'x'*9000}]*30, {})
        self.assertIn('tool-step limit', result)
        self.assertEqual(dispatch.call_count, 2)
        payload = json.loads(model.call_args.args[1])
        self.assertEqual(len(payload['conversation']), 12)
        self.assertEqual(len(payload['conversation'][0]['content']), 7000)

    def test_provider_error_is_visible_and_does_not_claim_success(self):
        self.session.complete = Mock(side_effect=RuntimeError('OpenRouter unavailable'))
        self.assertIn('could not complete', self.session.ask('What is next?'))

    def test_unicode_editing_does_not_trigger_agent_hotkeys(self):
        tab = ChatTab(self.email, self.calendar, live=False)
        app = TerminalApp([tab])
        self.addCleanup(app.executor.shutdown, wait=True)
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        for letter in 'schedule café':
            self.assertTrue(app.chat_key(letter, screen))
        self.assertEqual(tab.input, 'schedule café')
        self.assertIsNone(app.job)
        app.chat_key(curses.KEY_LEFT, screen)
        app.chat_key(curses.KEY_BACKSPACE, screen)
        self.assertEqual(tab.input, 'schedule caé')
        app.draw(screen)

    def test_chat_worker_publishes_reply_and_keeps_next_input(self):
        tab = ChatTab(self.email, self.calendar, live=False)
        app = TerminalApp([tab])
        self.addCleanup(app.executor.shutdown, wait=True)
        app.submit('chat', None, payload={'message': 'Show my commitments'})
        app.job.result(timeout=2)
        app.collect_progress()
        tab.consume_updates()
        self.assertTrue(any('Design review' in m['content'] for m in tab.messages))
        screen = Mock()
        screen.getmaxyx.return_value = (24, 80)
        app.draw(screen)

    def test_review_refs_bound_to_account_state(self):
        self.tools.dispatch('email_scan', {})
        self.tools.review('email:m1')
        self.email.state_path = self.email.state_path.with_name('other-account.json')
        with self.assertRaisesRegex(ValueError, 'Account changed'):
            self.tools.human_command('/approve', 'email:m1')

    def test_context_refreshes_after_connection_changes(self):
        context = {'timezone': 'unknown'}
        model = Mock(side_effect=[json.dumps({'type': 'tool', 'name': 'connect', 'arguments': {'agent': 'calendar'}}),
                                  json.dumps({'type': 'reply', 'text': 'Connected'})])
        def dispatch(*_):
            context['timezone'] = 'America/New_York'
            return {'connected': True}
        ChatLoop(model, dispatch).run([{'role': 'user', 'content': 'Connect calendar'}], lambda: context)
        self.assertEqual(json.loads(model.call_args.args[1])['context']['timezone'], 'America/New_York')
