import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from providers.openrouter import DEFAULT_MODEL, complete_json


class OpenRouterTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("providers.openrouter.urlopen")
    def test_missing_key_fails_before_network(self, request):
        with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            complete_json("system", "user")
        request.assert_not_called()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True)
    @patch("providers.openrouter.urlopen")
    def test_request_and_response(self, request):
        request.return_value = io.BytesIO(json.dumps({"choices": [{
            "finish_reason": "stop", "message": {"content": '{"summary":"hello"}'},
        }]}).encode())
        self.assertEqual(complete_json("system", "user"), '{"summary":"hello"}')
        sent = request.call_args.args[0]
        body = json.loads(sent.data)
        self.assertEqual(body["model"], DEFAULT_MODEL)
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", body)
        self.assertEqual(request.call_args.kwargs["timeout"], 60)

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True)
    @patch("providers.openrouter.urlopen")
    def test_truncated_response_is_rejected(self, request):
        request.return_value = io.BytesIO(json.dumps({"choices": [{
            "finish_reason": "length", "message": {"content": "{}"},
        }]}).encode())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            complete_json("system", "user")

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True)
    @patch("providers.openrouter.urlopen")
    def test_http_error_does_not_echo_provider_body(self, request):
        request.side_effect = HTTPError("https://openrouter.ai", 401, "private", {}, None)
        with self.assertRaisesRegex(RuntimeError, "^OpenRouter returned HTTP 401$"):
            complete_json("system", "user")
