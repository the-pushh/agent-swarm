"""Separate Calendar OAuth token; Gmail agent authorization is left intact."""
import json
import os
import tempfile
from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly',
          'https://www.googleapis.com/auth/calendar.events',
          'https://www.googleapis.com/auth/gmail.compose']


def credentials(progress=lambda message: None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    path = Path(os.environ.get('CALENDAR_TOKEN_FILE') or '.secrets/calendar-token.json').expanduser()
    creds = None
    if path.exists():
        creds = Credentials.from_authorized_user_file(str(path))
        if not creds.has_scopes(SCOPES):
            creds = None
    if creds and not creds.valid and creds.refresh_token:
        try:
            progress('Refreshing Calendar authorization…')
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        client = Path(os.environ.get('CALENDAR_CLIENT_SECRET_FILE') or
                      os.environ.get('GMAIL_CLIENT_SECRET_FILE') or '.secrets/gmail-client.json').expanduser()
        if not client.exists():
            raise ValueError('Save a Desktop OAuth client JSON first; see docs/calendar-setup.md')
        config = json.loads(client.read_text())
        if 'installed' not in config:
            raise ValueError('Calendar requires a Desktop OAuth client')
        flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
        progress('Authorize Calendar and Gmail draft access in your browser; waiting up to 3 minutes…')
        try:
            creds = flow.run_local_server(port=0, timeout_seconds=180, prompt='consent', access_type='offline',
                authorization_prompt_message=None, success_message='Calendar authorized. Return to the Agent Swarm TUI.')
        except Exception:
            raise RuntimeError('Calendar authorization cancelled or failed. Press Connect to retry.') from None
    return creds


def save_credentials(creds):
    path = Path(os.environ.get('CALENDAR_TOKEN_FILE') or '.secrets/calendar-token.json').expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            file.write(creds.to_json())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
