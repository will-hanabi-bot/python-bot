# Glossary

Terminology used across this project. Sections:

1. [Base Hanabi terms](#base-hanabi-terms) — vocabulary common to all Hanabi conventions.
2. [Reactor convention terms](#reactor-convention-terms) — jargon specific to the reactor convention (https://hanabi.wiki/en/conventions/reactor-1).
3. [Variant terms](#variant-terms) — modifiers that change how cards respond to clues. Includes the Scala regex predicate that detects each one.
4. [Scala-to-Python data model](#scala-to-python-data-model) — the core types ported from `scala-bot/src/scala_bot/basics/` and what each represents.

---

## Base Hanabi terms

- **Playable card** — A card that can legally be added to one of the play stacks on the next play (a 1 in an unplayed suit, or the next rank in an already-started suit).
- **Trash card** — A card that can never contribute to the score. Either already played, or a duplicate of a card already played, or strictly dominated by an already-discarded predecessor. Always variant-specific.
- **Critical card** — A card whose last copy is still in play (i.e., all other copies have been discarded or are bombed). Discarding it loses a point.
- **Slot** — A 1-indexed position in a player's hand, counting from the left. "Slot 1" is the newest (just-drawn) card.
- **Hand position** — Synonym for slot. In code, usually a 0-indexed `int` matching the position in the `hand` list.
- **Alice / Bob / Cathy** — Positional shorthand relative to a clue giver. **Alice** is the clue giver, **Bob** is the next player (giver + 1), **Cathy** is the player after that (giver + 2). When discussing clue interpretation, this naming is used regardless of the actual player names so that conventions can be described from a single viewpoint. In 4+ player games, **Donald** is giver + 3 and **Emily** is giver + 4. In 2-player games, "Cathy" doesn't exist.
- **Chop** — The default discard slot. In most conventions, the oldest unclued card. Special suits, chop-moves, and locked hands can shift it.
- **Focus** — The card a clue is "really about." When a clue touches multiple cards, conventions disambiguate which one carries the primary meaning.
- **Finesse** — A clue that simultaneously gives information about the focus card *and* implies that another card (in someone else's hand) is playable immediately — the recipient should blind-play it next turn.
- **Prompt** — Like a finesse, but the implied playable card is already touched by an earlier clue. The recipient promotes their understanding of that previously-clued card.
- **Bluff** — A finesse-shaped clue where the blind play actually drops a different card than was implied. Permitted but conventional; the bluffed player will re-derive what their card was after the play.
- **Good touch principle (GTP)** — Convention rule: if a clue touches a card, that card is implicitly playable (now or later) or chop-moved. Therefore, you should not clue cards that are already known trash, and you should not duplicate clue-information (touching two copies of the same card across two hands).
- **Sarcastic discard** — Discarding a card whose identity is already fully known elsewhere in the game, to signal something specific to a teammate (often: promote a teammate's holding of an identical card).
- **Double discard** — A pre-existing state where the next player has multiple potentially-critical discards and needs a clue *now*. Conventions react to this by stalling.
- **Scream/shout discard** — An emergency-stop convention: a player at 0 clues with no safe discard discards an obvious play to signal "everyone freeze, the next player must not discard." Reactor doesn't use the H-group shout, but the principle of "discard-as-emergency-signal" appears in other forms.
- **Fix-in clue / fill-in clue** — A clue that does not touch any previously-unclued cards. It's purely informational: either a color clue on a card that previously had a rank clue, or a rank clue on a card that previously had a color clue. Used to correct (fix) misinterpretations or to nail down identity.
- **Lock** — A player is *locked* when they cannot discard (typically because every card in hand has been clued and none is queued to play). The team must "unlock" them with a clue that gives them either a play or a permission-to-discard.
- **Pace** — `remaining_deck_cards - (max_score - current_score)`. A measure of how much slack the team has before running out of turns. When pace drops to `len(suits) + 1`, the `Reactor` enters the endgame solver (see below). Pace `< 0` is the standard *unwinnable state* condition: there aren't enough draws left to play every missing card.
- **BDR (bad-discard-rate)** — Heuristic measure of how risky a discard is, given the team's information about chops. Used by mid-game heuristics; the endgame solver does not consult BDR (it enumerates outcomes exactly).
- **Efficiency** — Plays per clue spent. A measure of how many bits of clue-information the team is converting into score.
- **Endgame solver** — A Monte Carlo search invoked by `Reactor.take_action` when `state.rem_score <= len(state.variant.suits) + 1`. Enumerates assignments of unseen cards to remaining draw slots (each an *arrangement*) and recursively computes the maximum-winrate action. Lives in `src/hanabi_bot/endgame/` and is a port of `scala-bot/src/scala_bot/endgame/`. Returns `(PerformAction, Fraction)` on success or an error string when it bails (timeout, too many unknowns, no winning action found).
- **Winrate** — A `fractions.Fraction` in `[0, 1]` representing the probability (over deck arrangements) that the solver believes the team can reach the max score from a given action and game state. Exact arithmetic — no floating point.
- **Arrangement** — One specific assignment of unseen-card identities to the upcoming draw slots (and to slots in our own hand whose identity we don't yet know). The endgame solver weights each arrangement by its probability and sums winrates across all arrangements.
- **Must-play** — An identity that *must* be played from a specific hand before the deck drains, because no other copy is reachable in time. The solver biases towards arrangements where must-play assignments succeed.
- **Trivially winnable** — A position the solver recognizes without full search: `rem_score == 1` with the missing card known and playable in our own hand. Returns winrate `1/1` immediately.
- **Unwinnable state** — A position where no continuation reaches the max score. Detected by `pace < 0`, or by recursive checks (`unwinnable_state` in `helper.py`) like "a critical card is in someone's known-trash slot and they must discard."

## Reactor convention terms

- **Reactive value** — For a color clue in a [rainbow-ish](#variant-terms) variant, the focus slot the clue "points at" (1..hand_size). Vanilla colors (Red=1, Yellow=2, Green=3, Blue=4, Purple=5, Teal=1 in 5-card hands; wrapped mod hand_size in 4-card hands) get fixed values; every other colourable suit (Pink, Brown, Orange, Black, ...) takes the first reactive slot not already claimed by suits earlier in the stack, scanning forward (mod hand_size) from the previous suit's value. If all slots are claimed, the special suit defaults to 1. Computed by `reactive_value_table` in `conventions/reactor/reactive_table.py`. In non-rainbow-ish variants the reactive focus is the *touched card's slot* (`focus_i + 1`), not a color-keyed value.
- **Stable clue** — A clue interpreted "statically" — it touches a card that conventionally means *something specific to its slot* (e.g., trash push, playable rank). Stable clues stand on their own.
- **Reactive clue** — A clue interpreted "reactively" — the clue isn't fully understood until the next player (the *reacter*) acts. The reacter's action (a play or discard) reveals which interpretation was meant; the *receiver* (a third player) then learns about a card in their hand based on the difference between what the reacter did and what the giver could have wanted.
- **Reacter** — The first player to act after a reactive clue. Their play/discard discriminates between interpretations.
- **Receiver** — The player whose card is ultimately identified by a reactive clue, via the reacter's response.
- **Fix clue** — A clue given to fix a bad-touched (or about-to-be-bad-touched) card — i.e., to correct a misinterpretation before it causes a misplay.
- **Stall clue** — A clue given purely to spend a clue token rather than convey new information (typically because no useful clue is available and the team needs to avoid discarding).
- **Gentleman's discard** — A discard of a card whose identity is fully known to the discarder (via inference) but not necessarily to the team — used to signal that an identical card lives in a specific slot of another player's hand.
- **Called-to-play** — A card status: the card has been signalled (by clue or finesse) as one the player should play immediately.
- **Called-to-discard** — A card status: the card has been signalled as a safe discard. Conventionally trash.
- **infoLock** — A *promise* about a card's identity, locked in by a previous interpretation (often a sarcastic discard or fix clue). Even if the inference set later widens, the promised identities cannot be removed without rewinding.
- **Waiting connection** — A pending reactive interpretation: the bot has computed a clue's meaning conditional on the reacter's next play/discard. Tracked until the reacter acts.
- **Reaction inversion** — When a reactive clue targets a non-adjacent player, the meanings flip (inverted). The bot must detect this to interpret correctly.
- **Focus slot** — Within a clue touching multiple cards, the specific slot conventions single out as carrying the meaning. Reactor's rule for focus slot varies for [pinkish](#variant-terms) and [prism](#variant-terms) variants.
- **Safe action** — A player has a safe action when *everyone* in the game knows they have either a play queued (or globally-known playable) or a discard queued (or globally-known trash). A player with a safe action does not need a clue.
- **Locked** — A reactor-specific state: a player has been told (via clue/convention) that they cannot discard until they receive an explicit play-or-discard signal.
- **Loaded** — Stronger than [safe action](#safe-action): the player specifically has a *play* queued (not just a discard). Conventions sometimes route signals to loaded vs unloaded players differently.
- **Permission to discard (PTD)** — A status indicating the player can safely discard a specific card (it's been called-to-discard, or the player is empathically-locked and a teammate has signalled release).
- **Note format** — Per-card notes the bot publishes to hanab.live. Each note is one or more `|`-separated segments. A segment is `turn N: [f] <ids>` when the card transitions to called-to-play (ids = the writer's own `me.thoughts[order].inferred` from the bot's Reactor, sorted by ordinal and formatted via `state.log_id`); `turn N: [kt]` when called-to-discard; `turn N: [reset]` when status returns to NONE after a prior signal. A new segment is also appended whenever the inferred set strictly shrinks while called-to-play. Because the segment uses the writer's own perspective, two bots with different visibility will publish different notes for the same card.

## Variant terms

Variants are modifiers that change either the suit composition, how clues touch cards, or rare gameplay rules. Each suit is classified by *regex match on its name* — the Scala source uses these patterns (`scala-bot/src/scala_bot/basics/Variant.scala` lines 9–16):

| Predicate | Scala regex | Behavior |
|---|---|---|
| **whitish** | `White\|Gray\|Light\|Null` | Suit is not touched by any color clue. |
| **rainbowish** | `Rainbow\|Omni` | Suit is touched by every color clue. |
| **pinkish** | `Pink\|Omni` | Suit is touched by every rank clue. |
| **brownish** | `Brown\|Muddy\|Cocoa\|Null` | Suit is not touched by any rank clue. |
| **dark** | `Black\|Dark\|Gray\|Cocoa` | Only one copy of each rank exists (instead of `3,2,2,2,1` for ranks 1–5). |
| **prism** | `Prism` | Rank determines color: `(rank - 1) mod (#colourable suits) == clue.value`. |
| **muddy** | `Muddy\|Cocoa` | A flavor of brownish. |
| **no-colour** | `White\|Gray\|Light\|Null\|Rainbow\|Omni\|Prism` | Not assigned a colour-clue index (skipped when enumerating colourable suits). |

Special-rank modifiers attach to a particular rank (e.g., "the 3 is special"):

- **Clue-starved** — Successful 5 plays only give back ½ a clue. Increases tempo pressure.
- **Critical rank** — A specific rank is critical regardless of suit copies (e.g., only one copy of every 4 exists).
- **Special rank** — A specific rank for which color/rank touch is altered (see below).
- **`rainbowS` / `pinkS` / `brownS` / `whiteS` / `deceptiveS`** — When a special rank is set, these flags toggle how clues of that rank/color touch it:
  - *rainbowS*: every color clue touches the special rank.
  - *pinkS*: every rank clue touches the special rank (so a non-matching rank clue still touches it).
  - *whiteS*: no color clue touches the special rank.
  - *brownS*: no rank clue touches the special rank.
  - *deceptiveS*: rank clue value is "lying" — touches based on `(suitIndex % 4) + 1or2`.
- **Scarce 1s** — Only two 1s of each suit exist instead of three.

## Scala-to-Python data model

The Python port mirrors the Scala module structure (`scala-bot/src/scala_bot/basics/`). Frozen dataclasses replace Scala case classes; `dataclasses.replace(x, ...)` replaces Scala's `x.copy(...)`; `Union` ADTs + PEP 634 `match` replace sealed traits.

### Identity

A `(suit_index, rank)` pair representing a card's logical identity, independent of which copy it is. Implemented as a `@dataclass(frozen=True, slots=True)`. Bidirectional conversion to an ordinal int via `to_ord()` / `from_ord(n)`:

```
ord = suit_index * 5 + (rank - 1)
```

Note: ranks are 1–5 in Hanabi, but ordinals are 0-indexed.

### IdentitySet

A set of `Identity` values, packed as a single Python `int` bitfield (one bit per ordinal). Equivalent to Scala's `BitSet`-backed `IdentitySet`. Supports `|` (union), `&` (intersection), `^` (symmetric difference), `~` (complement, bounded), `in` (membership), iteration in ordinal order, and `length` (popcount via `int.bit_count()`).

Subclassing `int` requires overriding every binary operator to preserve the subclass on the return value — otherwise `IdentitySet | IdentitySet` returns a plain `int` and downstream type assumptions break.

### Thought

What a player thinks about a single card. Has four notions of "what this card could be":

- `possible: IdentitySet` — Ids consistent with *clues only* (the empathy set).
- `inferred: IdentitySet` — Ids consistent with clues *plus* conventional information. Always a subset of `possible`.
- `old_inferred: IdentitySetOpt` — The previous inference, kept around in case the current inference (e.g., a finesse) gets disproved.
- `info_lock: IdentitySetOpt` — A *promise* about identity from a sarcastic discard / fix clue. Cannot be widened without rewinding.

A `Thought` exists once per `(player, card)` pair, per perspective. Player A's `Thought` about player B's card 7 is different from B's `Thought` about the same card.

### ConvData

Convention metadata common to all observers of the game (the "common perspective"). Fields like `focused`, `urgent`, `trash`, `status: CardStatus`, `signal_turn`, `reasoning` (turn-by-turn log of when this card's interpretation changed). One `ConvData` per card.

### Player (perspective)

A view of the game from one player's vantage point. Contains the `thoughts` vector (one `Thought` per drawn card order), `hypoStacks` (what play-stacks would look like after all queued plays resolve), `certainMap` (orders we're 100% sure about by identity), and convention-specific `links` (finesse chains).

Per-perspective: each `Player` instance corresponds to *what we (the bot) think this player thinks*. Player A's view differs from player B's view because each can see different hands.

### hypoStacks

Hypothetical play stacks. For each suit, the highest rank that would be played if every player executed their currently-known queued plays. Used to evaluate which cards are *eventually* playable vs only-now playable.

### certainMap

A `Map[Identity, Int]` (Python: `dict[Identity, int]`) tracking, for each identity, the number of copies whose *exact location* we know. Drives "good touch" reasoning and elimination logic.

### Connection (ADT)

A sealed type describing how one card "connects" to a play stack, used in finesse/prompt analysis:

- `Known` — the connecting card's identity is known to its holder.
- `Playable` — the card is known to be playable but its identity might not be pinned down.
- `Prompt` — the card is touched by a prior clue and conventionally promoted to playable.
- `Finesse` — the card is unclued but the holder will blind-play it.

### Action (ADT)

The actions sent *from hanab.live* describing the game's progression. Tagged-union of:

- `StatusAction(clues, score, maxScore)`
- `TurnAction(num, currentPlayerIndex)`
- `ClueAction(giver, target, list, clue)` — `list` is the orders touched
- `DrawAction(playerIndex, order, suitIndex, rank)` — `suitIndex=-1, rank=-1` if hidden from us
- `PlayAction(playerIndex, order, suitIndex, rank)`
- `DiscardAction(playerIndex, order, suitIndex, rank, failed)` — `failed=True` if from a strike
- `StrikeAction(num, turn, order)`
- `GameOverAction(endCondition, playerIndex)`
- `InterpAction(interp)` — a synthetic action used internally to force-attach an interpretation after rewinding.

### PerformAction (enum)

The actions the bot sends *to hanab.live*: `Play(target)`, `Discard(target)`, `Colour(target, value)`, `Rank(target, value)`, `Terminate(target, value)`. The wire-protocol type field matches `ACTION` in `constants.py` (0=Play, 1=Discard, 2=Colour, 3=Rank, 4=Terminate).

### link / playlink

Within a `Player`, a *link* groups a set of cards we know must collectively cover a set of identities, even when we don't know which card holds which. A *playlink* is the play-stack-specific variant: a set of cards from which exactly one will play onto a given stack, even though we can't yet identify which.
