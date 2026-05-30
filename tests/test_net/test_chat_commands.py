"""Tests for the `/leaveall` and `/settings` chat commands.

`format_reactive_settings` produces the user-spec output for the variant's reactive
clue table. The dispatcher accepts both commands from PM and in-table chat.
"""

from __future__ import annotations

from typing import Any

from hanabi_bot.basics.variant import get_variant
from hanabi_bot.conventions.reactor.reactive_table import format_reactive_settings
from hanabi_bot.net.commands import BotClient
from hanabi_bot.settings import BotConfig

# --- format_reactive_settings ---


def test_format_no_variant_is_slot_focus_both_sides() -> None:
    v = get_variant("No Variant")
    assert format_reactive_settings(v, hand_size=5) == (
        "odd plays: {slot focus}, even plays: {slot focus}"
    )


def test_format_pink_5_suits_is_rank_value_list() -> None:
    """Pinkish but not rainbowy → odd=slot focus, even=ranks 1..5."""
    v = get_variant("Pink (5 Suits)")
    assert format_reactive_settings(v, hand_size=5) == (
        "odd plays: {slot focus}, even plays: {1, 2, 3, 4, 5}"
    )


def test_format_pink_ones_blocks_rank_1() -> None:
    """Pink-Ones & Null (3 Suits): rank-1 is the special_rank with pink_s set
    → rank 1 can't be a clue value → list shows '-' at slot 1."""
    v = get_variant("Pink-Ones & Null (3 Suits)")
    assert format_reactive_settings(v, hand_size=5) == (
        "odd plays: {slot focus}, even plays: {-, 2, 3, 4, 5}"
    )


def test_format_rainbow_plus_pink_fives_lists_both_sides() -> None:
    """Pink-Fives & Rainbow (4 Suits): suits=[R, G, B, Rainbow], specialRank=5 (pink_s).
    Colourable = [R, G, B] → reactive table = (1, 3, 4). Rainbow makes color use value;
    Pink-Fives makes rank use value and blocks rank 5.

    Expected: odd plays: {r, -, g, b, -}, even plays: {1, 2, 3, 4, -}.
    """
    v = get_variant("Pink-Fives & Rainbow (4 Suits)")
    assert format_reactive_settings(v, hand_size=5) == (
        "odd plays: {r, -, g, b, -}, even plays: {1, 2, 3, 4, -}"
    )


def test_format_hand_size_4_truncates_lists() -> None:
    """4-5p (hand_size=4): the lists shrink to 4 entries."""
    v = get_variant("Pink (5 Suits)")
    assert format_reactive_settings(v, hand_size=4) == (
        "odd plays: {slot focus}, even plays: {1, 2, 3, 4}"
    )


# --- Dispatcher smoke tests ---


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def queue_send(self, command: str, payload: dict[str, Any] | None = None) -> None:
        self.sent.append((command, payload or {}))


def _make_client(username: str = "will-bot") -> tuple[BotClient, _FakeTransport]:
    transport = _FakeTransport()
    config = BotConfig(username=username, password="", host="localhost", index=0)
    client = BotClient(transport, config)  # type: ignore[arg-type]
    client.username = username
    return client, transport


def test_settings_in_table_room_sends_room_chat_with_formatted_string() -> None:
    client, transport = _make_client()
    # Register a pregame table whose variant is known.
    client.tables[42] = {
        "id": 42,
        "options": {"variantName": "Pink (5 Suits)", "numPlayers": 3},
    }
    client._on_chat({"msg": "/settings", "who": "someone", "room": "table42", "recipient": ""})
    assert transport.sent, "expected the bot to send a chat message"
    cmd, payload = transport.sent[-1]
    assert cmd == "chat"
    assert payload["room"] == "table42"
    assert payload["recipient"] == ""
    assert payload["msg"] == "odd plays: {slot focus}, even plays: {1, 2, 3, 4, 5}"


def test_leaveall_pregame_sends_tableLeave() -> None:
    """Pregame (no in-progress Reactor yet): cleanly vacate the seat via tableLeave."""
    client, transport = _make_client()
    client.tables[7] = {"id": 7, "options": {"variantName": "No Variant"}}
    client._on_chat({"msg": "/leaveall", "who": "someone", "room": "table7", "recipient": ""})
    assert ("tableLeave", {"tableID": 7}) in transport.sent
    assert not any(cmd == "tableUnattend" for cmd, _ in transport.sent)


def test_leaveall_in_progress_game_sends_tableUnattend() -> None:
    """Once the game has started, the server doesn't accept tableLeave — fall back
    to tableUnattend (stop spectating without forfeiting)."""
    from hanabi_bot.basics.options import TableOptions
    from hanabi_bot.basics.state import State
    from hanabi_bot.basics.variant import get_variant
    from hanabi_bot.conventions.reactor import Reactor

    client, transport = _make_client()
    variant = get_variant("No Variant")
    state = State.create(
        names=("alice", "bob", "cathy"),
        our_player_index=0,
        variant=variant,
        options=TableOptions(num_players=3, variant_name=variant.name),
    )
    client.games[11] = Reactor.create(11, state, in_progress=True)
    client._on_chat({"msg": "/leaveall", "who": "someone", "room": "table11", "recipient": ""})
    assert ("tableUnattend", {"tableID": 11}) in transport.sent
    assert not any(cmd == "tableLeave" for cmd, _ in transport.sent)


def test_legacy_pm_commands_still_pm_only() -> None:
    """A `/join` typed in an in-table room should NOT trigger _chat_join."""
    client, transport = _make_client()
    client._on_chat({"msg": "/join", "who": "someone", "room": "table9", "recipient": ""})
    assert transport.sent == [], (
        f"in-room /join should be ignored; got sends={transport.sent}"
    )


def test_non_command_chat_is_ignored() -> None:
    client, transport = _make_client()
    client._on_chat({"msg": "hello world", "who": "someone", "room": "table9", "recipient": ""})
    assert transport.sent == []


def test_settings_in_pm_uses_only_tracked_table() -> None:
    """In PM, with exactly one tracked table, /settings should target that table."""
    client, transport = _make_client(username="will-bot")
    client.tables[5] = {"id": 5, "options": {"variantName": "No Variant", "numPlayers": 3}}
    client._on_chat({"msg": "/settings", "who": "someone", "room": "lobby", "recipient": "will-bot"})
    assert any(cmd == "chat" and payload["room"] == "table5" for cmd, payload in transport.sent)
