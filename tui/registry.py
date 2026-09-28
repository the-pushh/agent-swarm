"""Register modular agents and share them with the conversational interface."""
from .email import EmailTab
from .calendar import CalendarTab
from .chat import ChatTab


def agent_tabs(state_dir, live=True):
    email = EmailTab(state_dir / "email-tui-demo.json", live=live)
    calendar = CalendarTab(state_dir / "calendar-demo.json", live=live)
    return [email, calendar, ChatTab(email, calendar, live=live)]
