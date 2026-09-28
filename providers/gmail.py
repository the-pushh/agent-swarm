"""Gmail adapter: inbox reads and reply-draft creation. No send operation."""

import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

from agents.email.tools import Draft, InboxPage, Message, MessageTitle


class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def reply_address(mime):
    value = str(mime.get("Reply-To") or mime.get("From") or "")
    addresses = getaddresses([value])
    if len(addresses) != 1 or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+", addresses[0][1]):
        raise ValueError("Email has no single valid reply address; manual review required")
    return addresses[0][1]


class GmailTools:
    def __init__(self, service, account, query="newer_than:30d", progress=lambda message: None):
        self.service, self.account, self.query = service, account, query
        self.progress = progress
        self.state_identity = {"gmail_account": account.casefold(), "query": query}
        self.state_key = hashlib.sha256(f"{account.casefold()}\n{query}".encode()).hexdigest()[:16]

    @classmethod
    def connect(cls, interactive=False, progress=lambda message: None):
        from googleapiclient.discovery import build
        from .gmail_auth import authorize, load_credentials

        try:
            credentials = load_credentials(progress)
        except (ValueError, RuntimeError):
            if not interactive:
                raise
            authorize(progress)
            credentials = load_credentials(progress)
        progress("Connecting to Gmail and checking the account profile…")
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        account = cls._execute(service.users().getProfile(userId="me"))["emailAddress"]
        expected = os.environ.get("GMAIL_ACCOUNT", "").strip()
        if expected and account.casefold() != expected.casefold():
            raise ValueError("Connected Gmail account does not match GMAIL_ACCOUNT")
        return cls(service, account, os.environ.get("GMAIL_QUERY", "newer_than:30d"), progress)

    @staticmethod
    def _execute(request):
        try:
            # In particular, never automatically retry draft creation.
            return request.execute(num_retries=0)
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            raise RuntimeError(f"Gmail request failed (HTTP {status or 'unknown'}). Check connection and authorization.") from None

    def list_inbox(self, cursor, limit):
        result = self._execute(self.service.users().messages().list(
            userId="me", labelIds=["INBOX"], q=self.query, maxResults=limit, pageToken=cursor))
        return InboxPage([m["id"] for m in result.get("messages", [])], result.get("nextPageToken"))

    def read_title(self, message_id):
        result = self._execute(self.service.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject"]))
        return self._title_from_metadata(result)

    @staticmethod
    def _title_from_metadata(result):
        headers = {h["name"].casefold(): h["value"] for h in result.get("payload", {}).get("headers", [])}
        return MessageTitle(result["id"], headers.get("from", ""), headers.get("subject", ""),
                            datetime.fromtimestamp(int(result["internalDate"]) / 1000, timezone.utc).isoformat()
                            if result.get("internalDate") else None)

    def read_titles(self, message_ids):
        """One HTTP batch, at most 50 metadata requests; bodies are never requested.

        https://developers.google.com/workspace/gmail/api/guides/batch
        """
        if len(message_ids) > 50 or len(set(message_ids)) != len(message_ids):
            raise ValueError("Title batch must contain at most 50 unique IDs")
        if not message_ids:
            return []
        results, failures = {}, []

        def collect(request_id, response, exception):
            if exception is not None or not response or response.get("id") != request_id:
                failures.append(request_id)
            else:
                results[request_id] = response

        batch = self.service.new_batch_http_request(callback=collect)
        for identifier in message_ids:
            batch.add(self.service.users().messages().get(
                userId="me", id=identifier, format="metadata", metadataHeaders=["From", "Subject"]),
                request_id=identifier)
        try:
            batch.execute()
        except Exception:
            raise RuntimeError("Gmail title batch failed; retry Analyze or Scan.") from None
        if failures or set(results) != set(message_ids):
            raise RuntimeError("Gmail returned an incomplete title batch; scan position unchanged.")
        return [self._title_from_metadata(results[identifier]) for identifier in message_ids]

    def _read_raw(self, message_id):
        result = self._execute(self.service.users().messages().get(userId="me", id=message_id, format="raw"))
        raw = result["raw"]
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return result, mime

    def read_message(self, message_id):
        result, mime = self._read_raw(message_id)
        part = mime.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part else "[No readable message body]"
        incomplete = part is None or any(mime.iter_attachments())
        if not isinstance(body, str):
            body, incomplete = "[Unsupported message body]", True
        if part and part.get_content_type() == "text/html":
            parser = HtmlText()
            parser.feed(body)
            body = "".join(parser.parts)
        if incomplete:
            body += "\n[Attachments or other content were not read; context may be incomplete.]"
        authors = getaddresses([str(mime.get("From", ""))])
        actual_sender = authors[0][1] if len(authors) == 1 and authors[0][1] else reply_address(mime)
        return Message(result["id"], result["threadId"], reply_address(mime),
                       str(mime.get("Subject", "")), body, incomplete,
                       datetime.fromtimestamp(int(result["internalDate"]) / 1000, timezone.utc).isoformat()
                       if result.get("internalDate") else None, actual_sender)

    def save_draft(self, draft: Draft):
        self.progress("Re-reading the original email to verify the reply address…")
        source, mime = self._read_raw(draft.message_id)
        subject = str(mime.get("Subject", ""))
        expected_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if source["threadId"] != draft.thread_id or reply_address(mime) != draft.to or expected_subject != draft.subject:
            raise ValueError("Reply target differs from the approved proposal")
        self.progress("Checking the Gmail thread for newer replies…")
        thread = self._execute(self.service.users().threads().get(
            userId="me", id=draft.thread_id, format="metadata"))
        messages = [m for m in thread.get("messages", []) if "DRAFT" not in m.get("labelIds", [])]
        if not messages or messages[-1]["id"] != draft.message_id:
            raise ValueError("This thread has newer mail. Review it in Gmail before drafting.")
        message_id = str(mime.get("Message-ID", ""))
        if not re.fullmatch(r"<[^<>\s]+>", message_id):
            raise ValueError("Source lacks a valid Message-ID; cannot safely thread the reply")
        reply = EmailMessage()
        reply["To"], reply["From"], reply["Subject"] = draft.to, self.account, draft.subject
        reply["In-Reply-To"] = message_id
        references = re.findall(r"<[^<>\s]+>", str(mime.get("References", "")))
        reply["References"] = " ".join(references + [message_id])
        reply.set_content(draft.body)
        raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
        self.progress("Creating the approved draft in Gmail; waiting for confirmation…")
        result = self._execute(self.service.users().drafts().create(
            userId="me", body={"message": {"threadId": draft.thread_id, "raw": raw}}))
        return result["id"]
