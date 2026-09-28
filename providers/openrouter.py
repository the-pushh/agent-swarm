"""OpenRouter JSON completion, shared by agents without agent-specific policy."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "z-ai/glm-5.3-flash"


def complete_json(system: str, user: str) -> str:
    """Read credentials from the environment; make one bounded, tool-free call."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY in your environment before scanning")
    payload = {
        "model": os.environ.get("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "max_tokens": 4096,
        "stream": False,
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as error:
        # Do not echo request headers or provider response bodies containing data.
        raise RuntimeError(f"OpenRouter returned HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("OpenRouter request failed or timed out") from None
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("OpenRouter returned an error or invalid response")
    try:
        choice = result["choices"][0]
        content = choice["message"]["content"]
        finished = choice["finish_reason"] == "stop"
        tool_calls = choice["message"].get("tool_calls")
    except (KeyError, IndexError, TypeError):
        raise ValueError("OpenRouter returned no valid completion") from None
    if not finished or tool_calls or not isinstance(content, str) or not content.strip():
        raise ValueError("OpenRouter completion was incomplete, empty, or requested tools")
    return content
