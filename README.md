# hanabi-bot

A Python bot that plays the [Hanabi](https://en.wikipedia.org/wiki/Hanabi_(card_game)) "reactor" convention on [hanab.live](https://hanab.live). Port of the working Scala implementation at `~/gm/git_repos/scala-bot`, faithful to the same conventions and supporting the same variants.

> **Status:** Early-stage port. Stage 0 (scaffolding) and Stage 1 (basics primitives) are in place. The bot does not yet connect to hanab.live — see [Porting roadmap](#porting-roadmap) below.

## Prerequisites

- Python 3.11+ (3.13 tested)
- [uv](https://docs.astral.sh/uv/) for dependency management. Install with `pip install uv`.

> **Note (Windows / pip-installed uv):** Installing via `pip install uv` (especially under the Microsoft Store Python) doesn't always put a `uv` shim on PATH — only the Python module is installed. All examples below use `python -m uv` so they work regardless. If you'd rather type plain `uv`, install via the standalone installer at https://astral.sh/uv instead.

## Install

```bash
git clone <this-repo>
cd python-bot
python -m uv sync --extra dev
```

This creates `.venv/`, installs runtime dependencies (`websockets`, `httpx`, `python-dotenv`) and dev dependencies (`pytest`, `ruff`, `mypy`).

## Configure

Copy `.env.example` to `.env` and fill in your hanab.live credentials:

```env
HANABI_USERNAME0=your-username
HANABI_PASSWORD0=your-password
HANABI_HOST=hanab.live
```

Multiple accounts can be configured by numeric suffix (`HANABI_USERNAME0`, `HANABI_USERNAME1`, …). Pass `index=N` on the command line to select which one to log in as.

## Run

```bash
# Log in as account 0 and idle in the lobby.
python -m uv run python -m hanabi_bot index=0

# Log in and immediately create a table.
python -m uv run python -m hanabi_bot index=0 bot_to_join=create

# Log in and try to join a teammate's open table.
python -m uv run python -m hanabi_bot index=0 bot_to_join=their_username
```

CLI args (`key=value` format, matching the Scala bot):
- `index=<N>` — which `HANABI_USERNAME<N>` / `HANABI_PASSWORD<N>` to use (default `0`)
- `bot_to_join=<name>` — auto-join the first open table containing `<name>`, or `create` to make a new one
- `convention=Reactor1` — only `Reactor1` is implemented; HGroup11/RefSieve are planned
- `table=<name>` — name to use when creating a table (default `bots`)
- `host=<host>` — overrides `HANABI_HOST` from `.env`

In the hanab.live lobby, DM the bot account to control it:
- `/join` — join the first open table you're in
- `/create` / `/start` — create or start a table
- `/setvariant <name>` — change variant
- `/terminate` — end the current game

The bot speaks the same wire protocol as the Scala bot. On its turn, it computes a `PerformAction` via the reactor convention and sends it back to the server. The endgame solver is stubbed (Stage 4 deferral), so very-late-game decisions fall back to the heuristic-only `take_action`.

## Test

```bash
# All tests
python -m uv run python -m pytest

# Just the basics layer
python -m uv run python -m pytest tests/test_basics/

# With verbose output
python -m uv run python -m pytest -v
```

> Use `python -m pytest` rather than `pytest` directly — on some Windows setups (e.g. with AppLocker / Application Control policies) the `pytest.exe` shim in `.venv\Scripts\` can be blocked, while `python -m pytest` always works.

## Lint / typecheck

```bash
python -m uv run python -m ruff check src tests
python -m uv run python -m mypy src
```

## Refresh variant catalog

The canonical [`variants.json`](src/hanabi_bot/data/variants.json) and [`suits.json`](src/hanabi_bot/data/suits.json) are vendored from upstream hanabi-live. To refresh:

```bash
python -m uv run python scripts/update_variants.py
```

## Repository layout

```
src/hanabi_bot/
  basics/        Game-data primitives: Identity, Variant, Card, Thought, Action, Clue, State, Player
  conventions/   Plugin convention implementations (currently: reactor/)
  net/           hanab.live WebSocket client + auth (Stage 5)
  cli/           Self-play / replay / interactive console (Stage 6)
  data/          Vendored variants.json + suits.json from hanabi-live
tests/           pytest suite
scripts/         Maintenance helpers (variant refresh)
```

## Documentation

- **[GLOSSARY.md](GLOSSARY.md)** — Hanabi terminology, reactor-convention jargon, variant terms, and Scala-to-Python data model glossary. Start here if any of the code below feels opaque.
- **[PORT_NOTES.md](PORT_NOTES.md)** — File-by-file mapping from the Scala source to Python, and the reuse verdicts for the older `old-python-bot` attempt.

## Porting roadmap

| Stage | Scope | Status |
|---|---|---|
| 0 | Scaffolding, docs, vendored variants/suits | **Done** |
| 1 | `basics/` primitives: identity, variant, card, action, clue | **Done** |
| 2 | State machine + empathy: `state.py`, `player.py`, `player_elim.py`, `game.py`, `connection.py`, `sarcastic.py`, `fix.py`, `eval.py`, `clue_result.py` | **Done** |
| 3 | Test harness — port `test/util.scala` to `tests/conftest.py` fixtures | **Done** |
| 4 | Reactor convention — `conventions/reactor/` (5 modules); reactor test scenarios | **Done** (2 variant-edge-case tests deferred) |
| 5 | hanab.live network client — login, WebSocket transport, lobby + game dispatch, entry point | **Done** |
| 6 | CLI tools — self-play, replay, analyze, interactive console | **Done** |

**Stage 4 deferrals:** The Scala bot's `EndgameSolver` (Monte Carlo, 624 LOC) is stubbed — `take_action` skips the endgame branch. Two variant-edge-case test scenarios are `pytest.mark.skip`'d pending further iteration.

## CLI subcommands

```bash
# Self-play: simulate N games offline. Writes each game's seed file to ./seeds/.
python -m uv run python -m hanabi_bot self-play games=10 seed=0 variant="No Variant" players=3

# Replay a finished game from hanab.live.
python -m uv run python -m hanabi_bot replay id=1234567 index=0

# Replay a local seed file (e.g. one written by self-play).
python -m uv run python -m hanabi_bot replay file=seeds/0.json index=0

# Analyze: replay + emit per-turn comments where the bot would have picked differently.
python -m uv run python -m hanabi_bot analyze id=1234567
python -m uv run python -m hanabi_bot analyze file=seeds/0.json
```

## References

- Reactor convention spec: https://hanabi.wiki/en/conventions/reactor-1
- Scala source of truth: `~/gm/git_repos/scala-bot/src/scala_bot/`
- hanabi-live server (Go): `~/gm/git_repos/hanabi-live/`
