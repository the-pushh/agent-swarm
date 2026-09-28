import base64
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock

from agents.email.agent import open_agent
from agents.email.tools import Draft
from agents.email.tool_implementations import JsonModelTools, demo_completion
from providers.gmail import GmailTools


class GmailTests(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.gmail = GmailTools(self.service, "me@example.com")
        self.users = self.service.users.return_value
        self.mime = EmailMessage()
        self.mime["From"] = "Sender <sender@example.com>"
        self.mime["Reply-To"] = "reply@example.com"
        self.mime["Subject"] = "Hello"
        self.mime["Message-ID"] = "<original@example.com>"
        self.mime["References"] = "<earlier@example.com>"
        self.mime.set_content("Hello from the inbox")
        self.set_raw()
        self.users.threads.return_value.get.return_value.execute.return_value = {
            "messages": [{"id": "m1", "labelIds": ["INBOX"]}]}
        self.users.drafts.return_value.create.return_value.execute.return_value = {"id": "d1"}

    def set_raw(self):
        self.users.messages.return_value.get.return_value.execute.return_value = {
            "id": "m1", "threadId": "t1",
            "internalDate": "1750000000000",
            "raw": base64.urlsafe_b64encode(self.mime.as_bytes()).decode().rstrip("=")}

    def test_title_discovery_requests_only_metadata(self):
        self.users.messages.return_value.get.return_value.execute.return_value = {
            "id": "m1", "payload": {"headers": [
                {"name": "From", "value": "Editor <news@example.com>"},
                {"name": "Subject", "value": "AI research this week"}]}}
        title = self.gmail.read_title("m1")
        self.assertEqual(title.subject, "AI research this week")
        self.assertEqual(title.sender, "Editor <news@example.com>")
        self.users.messages.return_value.get.assert_called_once_with(
            userId="me", id="m1", format="metadata", metadataHeaders=["From", "Subject"])
        self.users.messages.return_value.modify.assert_not_called()

    def test_title_batch_uses_one_transport_execution_and_keeps_order(self):
        batch = self.service.new_batch_http_request.return_value
        def execute():
            callback = self.service.new_batch_http_request.call_args.kwargs["callback"]
            for call in reversed(batch.add.call_args_list):
                identifier = call.kwargs["request_id"]
                callback(identifier, {"id": identifier, "payload": {"headers": []}}, None)
        batch.execute.side_effect = execute
        identifiers = [f"m{i}" for i in range(50)]
        self.assertEqual([t.id for t in self.gmail.read_titles(identifiers)], identifiers)
        batch.execute.assert_called_once_with()
        self.users.messages.return_value.get.return_value.execute.assert_not_called()
        self.assertEqual(batch.add.call_count, 50)
        for call in self.users.messages.return_value.get.call_args_list:
            self.assertEqual(call.kwargs["format"], "metadata")

    def test_partial_title_batch_fails_without_returning_incomplete_sample(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete title batch"):
            self.gmail.read_titles(["m1"])

    def test_paginated_inbox_read_does_not_modify_mail(self):
        self.users.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "m1"}], "nextPageToken": "page2"}
        self.assertEqual(self.gmail.list_inbox("page1", 2).next_cursor, "page2")
        self.users.messages.return_value.list.assert_called_once_with(
            userId="me", labelIds=["INBOX"], q="newer_than:30d", maxResults=2, pageToken="page1")
        message = self.gmail.read_message("m1")
        self.assertEqual(message.sender, "reply@example.com")
        self.assertIn("Hello from the inbox", message.body)
        self.assertFalse(message.context_incomplete)
        self.assertEqual(message.received_at, "2025-06-15T15:06:40+00:00")
        self.users.messages.return_value.modify.assert_not_called()

    def test_approved_draft_payload_preserves_reply_headers(self):
        draft = Draft("m1", "t1", "reply@example.com", "Re: Hello", "Thanks!")
        self.assertEqual(self.gmail.save_draft(draft), "d1")
        body = self.users.drafts.return_value.create.call_args.kwargs["body"]["message"]
        self.assertEqual(body["threadId"], "t1")
        mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(body["raw"]))
        self.assertEqual(mime["In-Reply-To"], "<original@example.com>")
        self.assertEqual(mime["References"], "<earlier@example.com> <original@example.com>")
        self.assertEqual(mime["To"], "reply@example.com")
        self.users.drafts.return_value.create.return_value.execute.assert_called_once_with(num_retries=0)
        self.users.messages.return_value.send.assert_not_called()
        self.users.drafts.return_value.send.assert_not_called()

    def test_newer_reply_blocks_draft_creation(self):
        self.users.threads.return_value.get.return_value.execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}]}
        with self.assertRaisesRegex(ValueError, "newer mail"):
            self.gmail.save_draft(Draft("m1", "t1", "reply@example.com", "Re: Hello", "Thanks"))
        self.users.drafts.return_value.create.assert_not_called()

    def test_changed_destination_blocks_write(self):
        with self.assertRaises(ValueError):
            self.gmail.save_draft(Draft("m1", "t1", "other@example.com", "Re: Hello", "Thanks"))
        self.users.drafts.return_value.create.assert_not_called()

    def test_attachments_flag_incomplete_context(self):
        self.mime.add_attachment(b"contents", maintype="application", subtype="pdf", filename="budget.pdf")
        self.set_raw()
        self.assertTrue(self.gmail.read_message("m1").context_incomplete)

    def test_html_only_body_is_decoded(self):
        self.mime.set_content("<p>Hello &amp; welcome</p><script>hidden()</script>", subtype="html")
        self.set_raw()
        body = self.gmail.read_message("m1").body
        self.assertIn("Hello & welcome", body)
        self.assertNotIn("hidden", body)

    def test_account_state_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with open_agent(path, self.gmail, JsonModelTools(demo_completion)) as agent:
                agent.checkpoint()
            other = GmailTools(self.service, "someone-else@example.com")
            with self.assertRaisesRegex(ValueError, "another account"):
                with open_agent(path, other, JsonModelTools(demo_completion)):
                    pass
