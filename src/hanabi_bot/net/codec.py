"""Wire-format codec for hanab.live WebSocket messages.

Each message is `COMMAND_NAME JSON_BODY` (single space separator). JSON_BODY may be
an object (most commands) or an array (e.g. `tableList`).

Reference: scala-bot/src/scala_bot/bot.scala + old-python-bot/hanabi_client.py:91-123.
"""

from __future__ import annotations

import json
from typing import Any


def decode(message: str) -> tuple[str, Any]:
    """Parse 'COMMAND_NAME {...json...}' into (command, payload).

    Payload type depends on the command: most are dicts; `tableList` is a list.

    Raises ValueError on malformed input.
    """
    parts = message.split(" ", 1)
    if len(parts) == 1:
        return parts[0], {}
    command, body = parts
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON body for command {command!r}: {body!r}") from e
    return command, payload


def encode(command: str, payload: dict[str, Any] | None = None) -> str:
    """Serialize (command, payload) back to wire format."""
    if payload is None or not payload:
        return command
    return f"{command} {json.dumps(payload, separators=(',', ':'))}"
