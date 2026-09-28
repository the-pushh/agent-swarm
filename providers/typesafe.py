"""TypeSafe Jev transport, reusable across agents with different questions."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def decide(state: dict, questions: dict) -> dict:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise ValueError("Set TYPESAFE_API_KEY in your environment before scanning")
    payload = {"model": os.environ.get("TYPESAFE_MODEL", "").strip() or "jev-latest",
               "state": state, "questions": questions}
    request = Request("https://api.typesafe.ai/v1/systemone",
                      data=json.dumps(payload).encode(), method="POST",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"TypeSafe returned HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("TypeSafe request failed or timed out") from None
    if not isinstance(result, dict) or result.get("error") or not isinstance(result.get("answers"), dict):
        raise ValueError("TypeSafe returned no valid answers")
    return result["answers"]
