"""Command dispatcher: maps hanab.live messages to bot actions.

Owns the per-table Reactor instances. On gameAction, parses the action into our
Action ADT, runs it through reactor.handle_action, and if it becomes our turn,
calls take_action() and queues the response.

Port of scala-bot/src/scala_bot/command.scala + the dispatcher half of bot.scala.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Awaitable
from typing import Any

from hanabi_bot.basics.action import (
    PerformTerminate,
    TurnAction,
    action_from_json,
)
from hanabi_bot.basics.game import Note
from hanabi_bot.basics.options import TableOptions
from hanabi_bot.basics.state import State
from hanabi_bot.basics.variant import get_variant
from hanabi_bot.conventions.reactor import Reactor
from hanabi_bot.settings import BotConfig

from .notes import compute_note_segments
from .ws_transport import BotTransport

log = logging.getLogger("hanabi_bot.client")


class BotClient:
    """Per-connection bot client.

    Maintains `self.games: dict[table_id, Reactor]` and dispatches WS messages.
    """

    def __init__(self, transport: BotTransport, config: BotConfig) -> None:
        self.transport = transport
        self.config = config
        self.username = ""
        self.games: dict[int, Reactor] = {}
        # Track tables we see in the lobby (for /join).
        self.tables: dict[int, dict[str, Any]] = {}
        # Per-table flags: action_time True between TurnActions, everyone_connected from `connected` msg.
        self._action_time: dict[int, bool] = {}
        self._everyone_connected: dict[int, bool] = {}

    # --- Top-level dispatch ---

    async def handle_message(self, command: str, payload: Any) -> None:
        """Dispatch a decoded WebSocket message. Payload may be dict or list."""
        log.debug("dispatch %s", command)
        handler = getattr(self, f"_on_{command}", None)
        if handler is None:
            log.debug("no handler for %s; ignoring", command)
            return
        try:
            result = handler(payload)
            if isinstance(result, Awaitable):
                await result
        except Exception:
            log.exception("handler for %r raised", command)
            print(f"!! handler for {command!r} raised:")
            traceback.print_exc()

    # --- Lobby ---

    def _on_welcome(self, data: dict[str, Any]) -> None:
        self.username = data.get("username", "")
        log.info("welcomed as %r", self.username)
        if self.config.bot_to_join == "create":
            self._chat_create_table()

    def _on_error(self, data: dict[str, Any]) -> None:
        log.warning("server error: %s", data)
        print(f"server error: {data}")

    def _on_warning(self, data: dict[str, Any]) -> None:
        log.warning("server warning: %s", data)
        print(f"server warning: {data}")

    def _on_chat(self, data: dict[str, Any]) -> None:
        msg = data.get("msg", "")
        if not msg.startswith("/"):
            return
        recipient = data.get("recipient", "") or ""
        room = data.get("room", "") or ""
        in_pm = recipient == self.username
        in_room = recipient == "" and room.startswith("table")
        if not in_pm and not in_room:
            return
        args = msg[1:].split(" ")
        cmd = args[0]

        # Commands available from both PM and in-table chat.
        if cmd == "leaveall":
            self._chat_leaveall(room)
            return
        if cmd == "settings":
            self._chat_settings(room)
            return

        # Remaining commands are PM-only (legacy behavior).
        if not in_pm:
            return
        if cmd == "join":
            target = args[1] if len(args) > 1 else None
            self._chat_join(data, target)
        elif cmd == "create":
            self._chat_create_table()
        elif cmd == "start":
            self._chat_start()
        elif cmd in {"setvariant", "set_variant"}:
            variant = args[1].replace("_", " ").replace("+", " & ") if len(args) > 1 else None
            self._chat_set_variant(variant)
        elif cmd == "terminate":
            table_id = int(args[1]) if len(args) > 1 else None
            self._chat_terminate(table_id)
        else:
            self._chat_reply(f"Unknown command: /{cmd}", data.get("who", ""))

    def _on_table(self, data: dict[str, Any]) -> None:
        tid = int(data.get("id", -1))
        if tid != -1:
            self.tables[tid] = data

    def _on_tableList(self, data: Any) -> None:
        """Server's snapshot of all visible tables on connect (sent as a JSON array)."""
        entries = data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []
        for entry in entries:
            tid = entry.get("id")
            if tid is not None:
                self.tables[int(tid)] = entry
        log.info("saw %d open table(s) in lobby", len(self.tables))
        print(f"saw {len(self.tables)} open table(s) in lobby")

    def _on_tableGone(self, data: dict[str, Any]) -> None:
        tid = int(data.get("tableID", -1))
        self.tables.pop(tid, None)

    def _on_tableStart(self, data: dict[str, Any]) -> None:
        tid = int(data["tableID"])
        # Server tells us a game we're in started; request the init info.
        self.transport.queue_send("getGameInfo1", {"tableID": tid})

    # --- Game lifecycle ---

    def _on_init(self, data: dict[str, Any]) -> None:
        """Server sent us game-state init. Build a Reactor + request gameActionList."""
        table_id = int(data["tableID"])
        opts = data.get("options", {})
        variant_name = opts.get("variantName", "No Variant")
        names = tuple(data["playerNames"])
        our_idx = int(data["ourPlayerIndex"])

        log.info(
            "init table %d: variant=%r names=%s our_index=%d replay=%s",
            table_id, variant_name, names, our_idx, data.get("replay", False),
        )

        variant = get_variant(variant_name)
        table_options = TableOptions.from_json(opts) if opts else TableOptions(len(names), variant_name)
        state = State.create(
            names=names,
            our_player_index=our_idx,
            variant=variant,
            options=table_options,
        )
        reactor = Reactor.create(table_id, state, in_progress=not data.get("replay", False))
        reactor = reactor.copy_with(catchup=True)
        self.games[table_id] = reactor
        self._action_time[table_id] = False
        self._everyone_connected[table_id] = False
        self.transport.queue_send("getGameInfo2", {"tableID": table_id})

    def _on_gameAction(self, data: dict[str, Any]) -> None:
        table_id = int(data["tableID"])
        self._apply_action(table_id, data["action"])
        self._maybe_take_turn(table_id)

    def _on_gameActionList(self, data: dict[str, Any]) -> None:
        table_id = int(data["tableID"])
        for raw_action in data.get("list", []):
            self._apply_action(table_id, raw_action)
        # We finished loading; tell server.
        if table_id in self.games:
            g = self.games[table_id].copy_with(catchup=False)
            self.games[table_id] = g
            # Seed action_time from state: gameActionList doesn't always include an
            # explicit TurnAction for the very first turn — state.current_player_index
            # carries that info, so trust it here.
            our_turn = g.state.current_player_index == g.state.our_player_index
            self._action_time[table_id] = our_turn
            log.info(
                "gameActionList loaded@%d: cpi=%d our=%d our_turn=%s",
                table_id, g.state.current_player_index, g.state.our_player_index, our_turn,
            )
        self.transport.queue_send("loaded", {"tableID": table_id})
        self._maybe_take_turn(table_id)

    def _on_databaseID(self, data: dict[str, Any]) -> None:
        pass  # informational

    def _on_connected(self, data: dict[str, Any]) -> None:
        table_id = int(data.get("tableID", -1))
        connected_list = data.get("list", [])
        all_connected = isinstance(connected_list, list) and all(connected_list)
        self._everyone_connected[table_id] = all_connected
        log.debug("connected@%d list=%s -> everyone_connected=%s", table_id, connected_list, all_connected)
        # If we now have everyone + it's our turn, fire.
        self._maybe_take_turn(table_id)

    def _on_clock(self, data: dict[str, Any]) -> None:
        pass

    def _on_user(self, data: dict[str, Any]) -> None:
        pass

    def _on_gameOver(self, data: dict[str, Any]) -> None:
        table_id = int(data.get("tableID", -1))
        if table_id in self.games:
            score = self.games[table_id].state.score
            log.info("game over at table %d: score %d", table_id, score)
            print(f"game over at table {table_id}: score {score}")
            if self.config.disconnect_on_game_end:
                self.transport.queue_send("tableUnattend", {"tableID": table_id})

    # --- Action handling ---

    def _apply_action(self, table_id: int, raw_action: dict[str, Any]) -> None:
        if table_id not in self.games:
            log.warning("apply_action for unknown table %d: %s", table_id, raw_action)
            return
        action = action_from_json(raw_action)
        if action is None:
            log.debug("apply_action: skipping unknown action %s", raw_action)
            return
        log.debug("apply_action@%d: %s", table_id, action)
        prev_game = self.games[table_id]
        try:
            new_game = prev_game.handle_action(action)
            assert isinstance(new_game, Reactor)
        except Exception:
            log.exception("handle_action failed for table %d", table_id)
            print(f"!! handle_action failed for table {table_id}:")
            traceback.print_exc()
            return
        # Diff note-worthy state changes, accumulate segments, queue server notes.
        segments = compute_note_segments(prev_game, new_game)
        if segments:
            new_notes = dict(new_game.notes)
            for order, seg in segments:
                existing = new_notes.get(order)
                full = f"{existing.full} | {seg}" if existing else seg
                new_notes[order] = Note(turn=new_game.state.turn_count, last=seg, full=full)
            new_game = new_game.copy_with(notes=new_notes)
            if not new_game.catchup and new_game.in_progress:
                for order, seg in segments:
                    log.debug("note@%d order=%d: %s", table_id, order, seg)
                    self.transport.queue_send(
                        "note",
                        {"tableID": table_id, "order": order, "note": new_notes[order].full},
                    )
        self.games[table_id] = new_game
        if isinstance(action, TurnAction):
            our_turn = (
                action.current_player_index == self.games[table_id].state.our_player_index
            )
            self._action_time[table_id] = our_turn
            log.info(
                "turn %d: current_player=%d our_index=%d our_turn=%s",
                action.num, action.current_player_index,
                self.games[table_id].state.our_player_index, our_turn,
            )

    def _maybe_take_turn(self, table_id: int) -> None:
        if table_id not in self.games:
            log.debug("maybe_take_turn@%d: no game; skip", table_id)
            return
        game = self.games[table_id]
        state = game.state

        # Log all the gating conditions in one place so it's obvious which one closes the gate.
        catchup = game.catchup
        in_progress = game.in_progress
        cpi = state.current_player_index
        our_idx = state.our_player_index
        everyone = self._everyone_connected.get(table_id, False)
        action_time = self._action_time.get(table_id, False)

        if catchup or not in_progress or cpi != our_idx or not everyone or not action_time:
            log.debug(
                "maybe_take_turn@%d: NOT acting (catchup=%s in_progress=%s cpi=%d our=%d everyone=%s action_time=%s)",
                table_id, catchup, in_progress, cpi, our_idx, everyone, action_time,
            )
            return

        log.info("maybe_take_turn@%d: computing action", table_id)
        try:
            perform = game.take_action()
        except Exception:
            log.exception("take_action failed for table %d", table_id)
            print(f"!! take_action failed for table {table_id}:")
            traceback.print_exc()
            return
        log.info("-> action %s", perform)
        print(f"-> action {perform}")
        self.transport.queue_send("action", perform.to_json(table_id))
        self._action_time[table_id] = False

    # --- Chat commands (outbound) ---

    def _chat_reply(self, message: str, who: str) -> None:
        self.transport.queue_send(
            "chatPM", {"msg": message, "recipient": who, "room": "lobby"}
        )

    def _chat_join(self, data: dict[str, Any], target: str | None) -> None:
        """Join an open table. Default target = the sender (so DMing /join joins their table)."""
        sender = data.get("who", "")
        if target is None:
            target = sender

        for tid, table in self.tables.items():
            if table.get("joined", False):
                continue
            if table.get("running", False):
                continue  # game already started; can't join
            players = table.get("players", []) or []
            if target not in players:
                continue
            self.transport.queue_send("tableJoin", {"tableID": tid})
            return
        self._chat_reply(
            f"No open table containing {target!r} to join", sender
        )

    def _chat_create_table(self) -> None:
        self.transport.queue_send(
            "tableCreate",
            {
                "name": self.config.table_name,
                "options": {
                    "variantName": "No Variant",
                    "speedrun": False,
                    "deckPlays": False,
                    "emptyClues": False,
                    "oneExtraCard": False,
                    "oneLessCard": False,
                    "detrimentalCharacters": False,
                },
                "password": "",
                "maxPlayers": self.config.max_num_players,
            },
        )

    def _chat_start(self) -> None:
        # Find a table we're in but isn't running.
        for tid, table in self.tables.items():
            if table.get("joined") and not table.get("running"):
                self.transport.queue_send("tableStart", {"tableID": tid})
                return

    def _chat_set_variant(self, variant: str | None) -> None:
        if variant is None:
            return
        for tid, table in self.tables.items():
            if table.get("joined") and not table.get("running"):
                self.transport.queue_send(
                    "tableSetVariant", {"tableID": tid, "options": {"variantName": variant}}
                )
                return

    def _chat_terminate(self, table_id: int | None) -> None:
        targets = [table_id] if table_id is not None else list(self.games.keys())
        for tid in targets:
            self.transport.queue_send(
                "action",
                PerformTerminate(target=0, value=0).to_json(tid),
            )

    def _resolve_target_table(self, room: str) -> int | None:
        """For commands that can come from PM or a table room, figure out which table to act on.

        From a room (`tableN`): use the suffix. From a PM (`room` empty): use the single
        currently-tracked table if there's exactly one, else None.
        """
        if room.startswith("table"):
            try:
                return int(room.removeprefix("table"))
            except ValueError:
                return None
        # PM context.
        if len(self.games) == 1:
            return next(iter(self.games))
        if len(self.tables) == 1:
            return int(next(iter(self.tables)))
        return None

    def _chat_leaveall(self, room: str) -> None:
        """Leave the table: `tableLeave` pregame, `tableUnattend` if the game has started.

        Mirrors scala-bot/.../command.scala:357-365 (`leaveRoom`). `tableLeave` vacates
        the bot's player seat in pregame; once the game has started the server doesn't
        allow leaving, so we fall back to `tableUnattend` (stop spectating).
        """
        table_id = self._resolve_target_table(room)
        if table_id is None:
            return
        game = self.games.get(table_id)
        game_started = game is not None and game.in_progress
        cmd = "tableUnattend" if game_started else "tableLeave"
        self.transport.queue_send(cmd, {"tableID": table_id})

    def _chat_settings(self, room: str) -> None:
        from hanabi_bot.basics.state import HAND_SIZE
        from hanabi_bot.basics.variant import get_variant
        from hanabi_bot.conventions.reactor.reactive_table import format_reactive_settings

        table_id = self._resolve_target_table(room)
        if table_id is None:
            return

        game = self.games.get(table_id)
        if game is not None:
            variant = game.state.variant
            hand_size = HAND_SIZE[game.state.num_players]
        else:
            table = self.tables.get(table_id)
            if table is None:
                return
            variant_name = (table.get("options") or {}).get("variantName") or table.get("variant")
            if not variant_name:
                return
            variant = get_variant(variant_name)
            num_players = int((table.get("options") or {}).get("numPlayers") or table.get("numPlayers") or 3)
            if num_players < 2 or num_players >= len(HAND_SIZE):
                num_players = 3
            hand_size = HAND_SIZE[num_players]

        msg = format_reactive_settings(variant, hand_size)
        self.transport.queue_send(
            "chat",
            {"msg": msg, "recipient": "", "room": f"table{table_id}"},
        )
