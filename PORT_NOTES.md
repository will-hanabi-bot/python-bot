# Port Notes

How this project relates to the Scala source of truth and the earlier `old-python-bot` attempt.

## Source of truth

The behavioral source of truth is the Scala bot at `~/gm/git_repos/scala-bot/src/scala_bot/`. Every Python module in `src/hanabi_bot/` is a faithful port of one or more Scala files (see mapping table below). When in doubt about expected behavior, check the Scala source and the Scala test suite under `~/gm/git_repos/scala-bot/src/test/`.

The variant catalog (`src/hanabi_bot/data/variants.json` and `suits.json`) comes from the canonical hanabi-live repo at `~/gm/git_repos/hanabi-live/packages/game/src/json/`. Refresh via `scripts/update_variants.py`.

## Reuse verdicts (`~/gm/git_repos/old-python-bot/`)

| File | Verdict | Rationale |
|---|---|---|
| `constants.py` | **Reused as-is** → `src/hanabi_bot/constants.py` | Matches hanab.live wire protocol; trivial. |
| `variants.json` | **Discarded** | Stale; missing `newID` field; fewer variants than the canonical hanabi-live catalog. We vendor from upstream instead. |
| `hanabi_client.py` | **Reference only** | 1501 lines, monolithic. Salvageable WebSocket message-split logic (L91–123) and chat command parser (L156–276) will be extracted at Stage 5, split across `net/codec.py`, `net/ws_transport.py`, `net/commands.py`. Conventions-dispatch (L502+) and the broken `self.s` (L267) are not reused. |
| `game_state.py` | **Reference only** | 1370 lines. Data model — single mega `GameState` with `candidates` + `possibilities` + `base_filtrations` triple-tracking — conflicts with Scala's separation into `State` / `Player` / `Thought`. The reactor logic (Stage 4) needs *Scala's* shape, not a transliteration. Specifically discarded: the ~300 lines of hardcoded suit lists at L205–502 (replaced by regex predicates per `Variant.scala:9–16`). |
| `main.py` | **Will be reused with adjustment at Stage 5** | The login + cookie + WebSocket bootstrap flow is correct. Drop the `config.json` mechanism; use `.env` + `HANABI_USERNAME<N>` to match the Scala bot. |
| `conventions/reactor.py` | **Discarded** | Documented buggy at L24, L28, L49 (TODOs in clue logic). Rewriting from the Scala reactor module at Stage 4. |
| `conventions/{h_group,encoder,ref_sieve}.py` | **Discarded** | Out of scope for this port. |
| `requirements.txt`, test files | **Discarded** | Using uv + pyproject.toml; pytest suite is being rebuilt to mirror the Scala test scenarios. |

## Scala-to-Python file mapping

Module-by-module. Files marked **[Stage 1]** exist now; others land in later stages.

### `basics/`

| Scala | Python | Notes |
|---|---|---|
| `basics/Card.scala` (Identity, CardStatus) | `basics/identity.py` **[Stage 1]** | Identity dataclass, `to_ord`/`from_ord`, `prev`/`next`/`played_before` |
| `basics/IdentitySet.scala` | `basics/identity.py` **[Stage 1]** | `IdentitySet(int)` — Python int subclass; `IdentitySetOpt` modeled as `IdentitySet \| None` rather than a sentinel value |
| `basics/Card.scala` (Card, Thought, ConvData) | `basics/card.py` **[Stage 1]** | Frozen dataclasses; `Thought.id()` ported preserving `infer`/`symmetric`/`partial` flags |
| `basics/Variant.scala` | `basics/variant.py` **[Stage 1]** | Regex predicates, `id_touched`, JSON loader from `data/variants.json` + `data/suits.json` |
| `basics/Action.scala` | `basics/action.py` **[Stage 1]** | Tagged-union `Action` via frozen dataclasses + `Union`; `PerformAction` as a frozen-dataclass family |
| `basics/clue.scala` | `basics/clue.py` **[Stage 1]** | `ClueKind` enum, `BaseClue`, `CardClue`, `Clue` |
| `basics/State.scala` | `basics/state.py` *(Stage 2)* | |
| `basics/Player.scala` | `basics/player.py` *(Stage 2)* | |
| `basics/playerElim.scala` | `basics/player_elim.py` *(Stage 2)* | Hot-path port — see Risks |
| `basics/Game.scala` | `basics/game.py` *(Stage 2)* | ABC; convention subclasses extend it |
| `basics/Connection.scala` | `basics/connection.py` *(Stage 2)* | |
| `basics/sarcastic.scala` | `basics/sarcastic.py` *(Stage 2)* | |
| `basics/fix.scala` | `basics/fix.py` *(Stage 2)* | |
| `basics/eval.scala` | `basics/eval.py` *(Stage 2)* | |
| `basics/clueResult.scala` | `basics/clue_result.py` *(Stage 2)* | |

### `reactor/`

| Scala | Python | Notes |
|---|---|---|
| `reactor/reactor.scala` | `conventions/reactor/reactor.py` *(Stage 4)* | Main `Reactor(Game)`; `take_action` |
| `reactor/interpretClue.scala` | `conventions/reactor/interpret_clue.py` *(Stage 4)* | Stable/reactive/fix/stall |
| `reactor/interpretReactive.scala` | `conventions/reactor/interpret_reactive.py` *(Stage 4)* | |
| `reactor/interpretReaction.scala` | `conventions/reactor/interpret_reaction.py` *(Stage 4)* | |
| `reactor/stateEval.scala` | `conventions/reactor/state_eval.py` *(Stage 4)* | |

### Network / entry point

| Scala | Python | Notes |
|---|---|---|
| `bot.scala` | `net/ws_transport.py` + `net/codec.py` + `__main__.py` *(Stage 5)* | Split transport, codec, entry |
| `command.scala` | `net/commands.py` *(Stage 5)* | Chat-command parsing |
| `settings.scala` | `settings.py` *(Stage 5)* | `.env` loading |

### CLI

| Scala | Python | Notes |
|---|---|---|
| `console.scala` | `cli/console.py` *(Stage 6)* | |
| `replay.scala` | `cli/replay.py` *(Stage 6)* | |
| `selfPlay.scala` | `cli/self_play.py` *(Stage 6)* | |
| `analyze.scala` | `cli/analyze.py` *(Stage 6)* | |

### Tests

The Scala test harness at `~/gm/git_repos/scala-bot/src/test/util.scala` will be ported to `tests/conftest.py` at Stage 3, exposing `setup()`, `take_turn()`, `pre_clue()`, `has_infs()`, `has_status()` as pytest fixtures/helpers. Test files under `tests/test_reactor/` will match `~/gm/git_repos/scala-bot/src/test/reactor/*.scala` one-to-one for ease of cross-referencing diffs.

## Idiom translation reference

| Scala | Python |
|---|---|
| `case class Foo(...)` | `@dataclass(frozen=True, slots=True)` |
| `foo.copy(x = 1)` | `dataclasses.replace(foo, x=1)` |
| `sealed trait X { case ... }` | `X = A \| B \| C` (frozen-dataclass union), matched with `match`/`case` |
| `enum X { case A, B }` | `class X(enum.Enum): A = ...; B = ...` |
| `Vector[T].updated(i, v)` | A tuple-set helper returning a new tuple |
| `BitSet` | Python `int` (arbitrary precision, native `& \| ^ ~`, `int.bit_count()`) |
| `Option[T]` | `T \| None` |
| `IO[Unit]` | `async def` (`asyncio` + `websockets`) — only in `net/` and `cli/` |
| `Ref[IO, X]` | An attribute; no Ref needed in single-event-loop async |
| `Queue[IO, X]` | `asyncio.Queue` |
| `Fiber.start` | `asyncio.create_task` |
| `inline def` | regular `def` (Python doesn't inline; profile if it matters) |

## Known risks (deferred to later stages)

These are flagged in advance so we can address them when the relevant stage lands:

1. **`playerElim.scala` (Stage 2)** — hot path with bitset mutation + recursion (`recursiveIds`). Risk: 10–50× Python slowdown vs Scala. Mitigation: port shape-faithfully (imperative inside the function, but build new dataclass at the end), use `int` bitsets, profile early via the Stage 6 self-play harness. Don't try to make it functional.
2. **`IdentitySet(int)` subclassing** — Python returns plain `int` from `& \| ^ ~` unless every operator is overridden. We override them in Stage 1; `tests/test_basics/test_identity_set.py` enforces this.
3. **Variant quirks** (pink, prism, omni, muddy, deceptive, special rank) — easy to miss in regex predicates. Mitigated by direct port from `Variant.scala` and per-predicate test coverage in `tests/test_basics/test_variants.py`.
4. **`Game.handleAction` polymorphism (Stage 2)** — Scala trait with convention subclasses overriding behavior. Python: `Game` ABC + frozen-dataclass mixin with `_replace(**kw)` delegating to `dataclasses.replace`.
5. **Async/sync boundary** — `conventions/*` must stay sync; `net/*` is async. If reactor computation ever exceeds ~50ms, wrap in `loop.run_in_executor`.

## Cross-referencing the Scala source

If you're reading a Python file and want to consult the original:

- `src/hanabi_bot/basics/identity.py` ← `scala-bot/src/scala_bot/basics/Card.scala:33–63` (Identity) + `IdentitySet.scala`
- `src/hanabi_bot/basics/variant.py` ← `scala-bot/src/scala_bot/basics/Variant.scala`
- `src/hanabi_bot/basics/card.py` ← `scala-bot/src/scala_bot/basics/Card.scala:65–230`
- `src/hanabi_bot/basics/action.py` ← `scala-bot/src/scala_bot/basics/Action.scala`
- `src/hanabi_bot/basics/clue.py` ← `scala-bot/src/scala_bot/basics/clue.scala`

Each ported Python file has a short top-of-file comment pointing at its Scala counterpart.
