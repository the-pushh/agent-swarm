"""Session orchestration and literal human commands, separate from model tools."""
from .control_loop import ChatLoop
from .tool_implementations import AgentTools

HELP = '''Ask either agent in plain language. For example:
• What emails need my reply? Scan the next page if needed.
• Include AI research newsletters and exclude sales@example.com.
• Draft a shorter reply to Alex's email.
• What commitments do I have tomorrow?
• Find 30 minutes with alex@example.com next week.
• Block an hour for focus work tomorrow morning.
• Draft a reschedule request for my design review.

I prepare proposals, then you choose:
/show <number or ref> — inspect the exact proposal
/approve <ref> — approve the reviewed version
/execute <ref> — carry out that approved action
/reject <ref> — reject a proposal
/apply filters:<id> — apply reviewed filter changes
/connect email or /connect calendar — connect directly
/help — show this help
/quit — close the TUI

Calendar invitations notify guests. Email actions only save drafts.
Proposals get short numbers: /approve 1 then /execute 1.
Chat history stays in this TUI session; Gmail/Calendar proposals are saved by their agents.'''


class ChatSession:
    def __init__(self, email, calendar, complete, progress=lambda message: None):
        self.tools = AgentTools(email, calendar, complete, progress)
        self.complete, self.progress = complete, progress
        self.messages = []
        self.accounts = self.account_signature()

    def account_signature(self):
        return (getattr(self.tools.email.gmail, 'account', None),
                getattr(self.tools.calendar.calendar, 'account', None))

    def append(self, role, content):
        self.messages.append({'role': role, 'content': content})
        self.messages = self.messages[-100:]

    def ask(self, message):
        message = message.strip()
        if not message or len(message) > 8000:
            raise ValueError('Enter a message up to 8000 characters')
        accounts = self.account_signature()
        if any(old is not None and old != new for old, new in zip(self.accounts, accounts)):
            self.messages = []
            self.tools.reviews.clear()
            self.tools.aliases.clear()
            self.append("assistant", "Connected account changed; previous chat context cleared.")
        self.tools.previews = []
        self.append('user', message)
        try:
            if message == '/help':
                answer = HELP
            elif message.startswith('/'):
                parts = message.split()
                if len(parts) != 2:
                    raise ValueError('Use a command and one exact reference; /help lists the commands')
                command, ref = parts
                if command == '/connect':
                    self.tools.require(ref)
                    answer = f'{ref.capitalize()} connected.'
                elif command == '/show':
                    self.tools.review(ref)
                    answer = 'Exact proposal shown below.'
                elif command in ('/approve', '/reject', '/execute', '/apply'):
                    answer = self.tools.human_command(command, ref)
                else:
                    raise ValueError('Unknown command. Use /help.')
            else:
                answer = ChatLoop(self.complete, self.tools.dispatch, self.progress).run(self.messages, self.tools.context)
        except Exception as error:
            answer = f'I could not complete that: {error}'
        self.append('assistant', answer)
        for preview in dict.fromkeys(self.tools.previews):
            self.append('review', preview)
        self.accounts = self.account_signature()
        return answer
