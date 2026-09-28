import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from tui.calendar import CalendarTab
from tui.email import EmailTab
from tui.__main__ import TerminalApp
from tui.registry import agent_tabs


class CalendarTuiTests(unittest.TestCase):
    def test_registry_keeps_mail_and_adds_disconnected_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            tabs = agent_tabs(Path(directory))
            self.assertEqual([t.name for t in tabs], ['Gmail agent', 'Calendar agent', 'Chat'])
            self.assertIsNone(tabs[1].connection)
            self.assertEqual(tabs[1].toolbar, [('c', 'Connect')])

    def test_demo_scan_backlog_plan_and_render(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = CalendarTab(Path(directory) / 'calendar.json')
            tab.run('s')
            self.assertEqual(len(tab.rows('backlog')), 1)
            self.assertEqual(len(tab.rows()), 2)
            tab.run('f', payload={'kind': 'block', 'title': 'Focus', 'duration': '60', 'days': '7'})
            self.assertEqual(len([key for key, _ in tab.rows() if key.startswith('p:')]), 3)
            app = TerminalApp([tab])
            screen = Mock()
            screen.getmaxyx.return_value = (30, 120)
            try:
                app.draw(screen)
                self.assertTrue(app.rendered_selection.startswith('p:'))
                app.select_view('backlog')
                app.draw(screen)
                self.assertEqual(app.rendered_selection, 'e:past')
            finally:
                app.executor.shutdown(wait=True)

    def test_reminders_and_background_scans_do_not_switch_mail_view(self):
        with tempfile.TemporaryDirectory() as directory:
            mail = EmailTab(Path(directory) / 'email.json')
            calendar = CalendarTab(Path(directory) / 'calendar.json')
            calendar.run('s')
            app = TerminalApp([mail, calendar])
            try:
                app.tick_calendar()
                self.assertIn('Design review', app.reminder_notice)
                calendar.next_scan = 0
                app.tick_calendar()
                self.assertIs(app.job_tab, calendar)
                app.job.result(timeout=2)
                app.collect_progress()
                self.assertIs(app.tab, mail)
                self.assertFalse(app.show_backlog)
            finally:
                app.executor.shutdown(wait=True)

    def test_hours_form_saves_defaults_and_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = CalendarTab(Path(directory) / 'calendar.json')
            payload = {key: value for key, _, value in tab.form_fields('h', None)}
            payload['work_start'] = '10:00'
            tab.run('h', payload=payload)
            self.assertEqual(tab.preferences().work_start, '10:00')
            self.assertEqual(tab.preferences().protected['Lunch'], ['12:00', '13:00'])

    def test_stale_display_cannot_approve_another_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            tab = CalendarTab(Path(directory) / 'calendar.json')
            tab.run('f', payload={'kind': 'meeting', 'title': 'Meet', 'attendees': 'alex@example.com', 'days': '7'})
            key = tab.rows()[0][0]
            with tab.open() as agent:
                agent.state['proposals'][key[2:]]['proposal']['attendees'] = ['other@example.com']
                agent.checkpoint()
            with self.assertRaisesRegex(ValueError, 'changed'):
                tab.run('a', key)
