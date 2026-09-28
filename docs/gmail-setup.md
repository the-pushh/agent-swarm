# Connect your Gmail account

This local desktop app uses Google OAuth. You do not need a service account,
deployment, public callback URL, or Gmail password. The real inbox mode is explicit:
`python -m tui --live`. The default `python -m tui` opens the same disconnected Gmail view.
Use `python -m tui --demo` only when you explicitly want fixture emails.

## What to do in Google Cloud

1. Create or select your project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Open **Google Auth platform → Branding** and configure the app name (for example
   Agent Swarm), support email, and developer contact email.
4. Under **Audience**, use **External** for a personal Gmail account, leave it in
   **Testing**, and add the Gmail address you intend to use under **Test users**.
   A Workspace-only project may use Internal when permitted by the organization.
5. Under **Data Access**, add these scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
6. Under **Clients → Create client**, select **Desktop app**. Download its JSON
   and save it in this repo as `.secrets/gmail-client.json`.

Google's [desktop quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
describes the OAuth client setup. The app handles a local loopback callback and
opens Google's account chooser. Select the same address added as a test user.

`gmail.compose` allows managing drafts **and sending**, according to Google's
[scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes).
Google does not provide a draft-create-only scope. This code implements only
reading and draft creation; it has no send, archive, delete, or mark-read operation.

## Run locally

Use Python 3.10+ for the optional Google libraries (the offline demo still supports
Python 3.9). A Python 3.12 virtual environment has been prepared on this machine.
For a fresh checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-gmail.txt
```

Fill in your existing `.env`:

```dotenv
OPENROUTER_API_KEY=your_openrouter_key
TYPESAFE_API_KEY=your_typesafe_key
GMAIL_CLIENT_SECRET_FILE=.secrets/gmail-client.json
GMAIL_TOKEN_FILE=.secrets/gmail-token.json
GMAIL_ACCOUNT=your-address@gmail.com
GMAIL_QUERY=newer_than:30d
```

`GMAIL_ACCOUNT` is an optional check that prevents using the wrong Google account.
Leave model IDs as supplied in `.env.example`. Then:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m tui --live
```

In the TUI, press **C — Connect Gmail**. It reuses saved authorization or opens
Google consent in your browser, then saves the OAuth token locally. Return to the
TUI after signing in. Connecting does not scan messages or call models.
The TUI shows the connected account; **S**
scans up to 50 emails at a time. Inbox text is sent to TypeSafe Jev and, when needed,
GLM through OpenRouter. **A** approves a proposal; **E** separately creates its
draft in your Gmail account. No-draft proposals only acknowledge the assessment.

The CLI supports the same live state:

```bash
python -m agents.email.agent --live scan --batch-size 50
python -m agents.email.agent --live status
```

The default inbox query covers the last 30 days. Set `GMAIL_QUERY=` for the whole
inbox. Account and query determine the state filename; changing either starts a
separate scan history. `.env`, `.secrets/`, and `.agent-state/` are gitignored.
OAuth tokens are written with owner-only permissions. Do not paste credentials
or tokens into chat or commit them.

## Limits and troubleshooting

- External apps in Testing generally receive refresh tokens that expire after
  seven days for these scopes. Press **C — Reconnect Gmail** when needed.
  See [Google's token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).
- If Google blocks consent, check that your exact account is a test user.
  Workspace accounts may also require administrator approval.
- Only message body text is assessed. Unread attachments force `needs_attention`.
  Replies target one Reply-To (or From) address; this is not reply-all.
- Draft creation checks that the target is still the newest non-draft message in
  its thread and preserves reply headers. If newer mail arrives, execution stops.
  This is a preflight check, not an atomic lock against Gmail changing afterward.
- Failed/ambiguous writes remain `uncertain`; inspect Gmail before reconciling
  state. The agent will not retry automatically. Drafts cannot be edited or
  re-approved through this UI yet; use Gmail directly when manual handling is needed.
- TUI reset is disabled in live mode. The demo has separate state and simulated writes.

No separate authentication command is needed. Missing client files, declined
consent, and expired authorization are reported inside the TUI. **C** can be used
again to retry or reconnect. Browser consent has a three-minute timeout.

Implementation: [OAuth](../providers/gmail_auth.py), [Gmail adapter](../providers/gmail.py).
