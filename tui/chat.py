"""Chat tab: transcript + input, backed by the shared conversational agent."""
from copy import deepcopy
from queue import SimpleQueue, Empty
from agents.chat.agent import ChatSession, HELP


class ChatTab:
    name = 'Chat'
    is_chat = True
    toolbar = [('g', 'Connect Gmail'), ('k', 'Connect Calendar')]
    live = True
    action_messages = {'chat': 'Working on your request…', 'g': 'Connecting Gmail…', 'k': 'Connecting Calendar…'}

    def __init__(self, email, calendar, live=True):
        from providers.openrouter import complete_json
        from agents.chat.demo import complete
        self.email, self.calendar, self.live = email, calendar, live
        self.session = ChatSession(email, calendar, complete_json if live else complete)
        self.messages = [{'role': 'assistant', 'content': 'Ask about your mail or calendar. I can read, summarize, draft, and plan.\n'
                         'Try “What needs my attention?” or “Find an hour for focus work tomorrow.”\n'
                         'External changes require /approve and /execute. Type /help for examples.'}]
        self.input = ''
        self.cursor = 0
        self.chat_scroll = 0
        self.updates = SimpleQueue()

    def refresh(self):
        pass

    def rows(self, view='signal'):
        return []

    def consume_updates(self):
        latest = None
        while True:
            try:
                latest = self.updates.get_nowait()
            except Empty:
                break
        if latest is not None:
            self.messages = latest
        return latest is not None

    def run(self, action, identifier=None, progress=lambda message: None, payload=None):
        self.session.progress = self.session.tools.progress = progress
        if action == 'g':
            message = '/connect email'
        elif action == 'k':
            message = '/connect calendar'
        elif action == 'chat':
            message = (payload or {}).get('message', '')
        else:
            raise ValueError('Unknown chat action')
        # Publish the typed message promptly without sharing mutable worker history.
        self.updates.put(deepcopy(self.session.messages + [{'role': 'user', 'content': message}]))
        result = self.session.ask(message)
        self.updates.put(deepcopy(self.session.messages))
        return 'Reply ready.' if not result.startswith('I could not') else result

    def status(self):
        email = self.email.gmail
        calendar = self.calendar.calendar
        return ('Gmail: ' + (email.account if email else 'demo' if not self.live else 'not connected') + '  ·  Calendar: ' +
                (calendar.account if self.live and calendar else 'demo' if not self.live else 'not connected'))
