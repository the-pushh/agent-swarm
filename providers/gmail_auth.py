"""Desktop OAuth helper used by the TUI's Connect Gmail action."""

import json
import os
import tempfile
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose"]


def token_path():
    return Path(os.environ.get("GMAIL_TOKEN_FILE") or ".secrets/gmail-token.json").expanduser()


def save_credentials(credentials):
    path = token_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(credentials.to_json())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_credentials(progress=lambda message: None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path()
    progress("Checking saved Google authorization…")
    if not path.exists():
        raise ValueError("Press C (Connect Gmail) in the TUI to authorize your account.")
    credentials = Credentials.from_authorized_user_file(str(path))
    if not credentials.has_scopes(SCOPES):
        raise ValueError("Gmail permissions are missing. Reconnect Gmail in the TUI.")
    if not credentials.valid:
        if not credentials.refresh_token:
            raise ValueError("Reconnect Gmail in the TUI.")
        try:
            progress("Refreshing the expired Google access token…")
            credentials.refresh(Request())
        except Exception:
            raise RuntimeError("Gmail authorization expired or failed. Reconnect Gmail in the TUI.") from None
        save_credentials(credentials)
    return credentials


def authorize(progress=lambda message: None):
    """Open browser consent without printing over curses; return the connected account."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    path = Path(os.environ.get("GMAIL_CLIENT_SECRET_FILE") or ".secrets/gmail-client.json").expanduser()
    progress("Reading the Google Desktop OAuth client configuration…")
    if not path.exists():
        raise ValueError(f"Save your GCP Desktop OAuth client JSON at {path}. See docs/gmail-setup.md.")
    config = json.loads(path.read_text())
    if "installed" not in config:
        raise ValueError("Create a Desktop app OAuth client, not a Web app or service account.")
    # Use Google's OAuth library for state verification and the loopback callback.
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    try:
        progress("Waiting for Google sign-in in your browser (up to 3 minutes)…")
        credentials = flow.run_local_server(port=0, timeout_seconds=180, prompt="consent",
                                           access_type="offline", authorization_prompt_message=None,
                                           success_message="Gmail authorization received. Return to the Agent Swarm TUI.")
    except Exception:
        raise RuntimeError("Google consent was cancelled, timed out, or failed. Press C to try again.") from None
    progress("Google consent received; verifying the selected Gmail account…")
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    account = service.users().getProfile(userId="me").execute()["emailAddress"]
    expected = os.environ.get("GMAIL_ACCOUNT", "").strip()
    if expected and account.casefold() != expected.casefold():
        raise ValueError("Selected Gmail account does not match GMAIL_ACCOUNT; existing token was not replaced.")
    progress("Saving Google authorization securely on this Mac…")
    save_credentials(credentials)
    return account


def main():
    # Optional compatibility entry point; normal setup happens inside the TUI.
    print(f"Connected Gmail: {authorize()}. Start the TUI and press C to connect.")


if __name__ == "__main__":
    main()
