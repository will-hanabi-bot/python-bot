# Project-specific instructions for Claude

These rules apply when working in this repository. Keep them up to date as new
conventions emerge.

## Behavioral test changes — require approval

When a code change (feature, refactor, convention tweak, bug fix) causes
**existing test assertions to fail**, STOP before modifying the test. Do not
silently update assertions to match the new behavior.

Instead:

1. List every failing test by file::function.
2. For each failure, show:
   - The test's setup (hands / play stacks / discarded / variant / starting / pre-clues).
   - The original assertion (what the test was checking).
   - The new actual behavior the code now produces.
   - A short read of *why* the change caused this particular test to drift — is the
     test verifying behavior that the user *intended* to change, or did the change
     also affect a scenario the user didn't intend to touch?
3. Ask the user explicitly: for each failing test, do they want it
   - **updated** (encode the new behavior),
   - **kept** (the code change was wrong / overreaching, roll back the relevant part),
   - or **split** (preserve the old test by tweaking its setup so it still hits the
     un-changed path, AND add a new test for the changed path).

Only proceed once the user has answered. Bundle the questions into a single
`AskUserQuestion` block (one question per failing test, or one question for the set
if they all share the same disposition).

### When this rule does NOT apply

- A test fails because of a typo / import change / rename — fix it without asking.
- A test fails because the bot's *observable interface* (function signature,
  module name) changed but the *behavior* it was checking is unchanged — adjust
  the test's setup to use the new interface without prompting.
- A test you just *added in the same change* fails — that's iteration, not a
  behavioral drift. Fix it.

The distinguishing question is: "is the assertion the user wrote still meaningful?"
If yes (and only the call shape moved), fix mechanically. If no (the user's
documented expectation no longer holds), ask first.

## Scope discipline

- Don't refactor adjacent code while implementing a feature unless asked. The
  user reads diffs; surprise refactors increase review burden and risk silent
  behavior changes.
- When fixing a bug, fix only that bug. Note any related bugs you noticed but
  don't fix them in the same change unless explicitly approved.

## Glossary discipline

- When introducing or formalizing a term, add it to `GLOSSARY.md` (in the
  appropriate of the four sections: base Hanabi terms / reactor convention terms /
  variant terms / Scala-to-Python data model).
- When a term's definition in code diverges from the glossary, surface the
  divergence before "fixing" either side — they may have drifted intentionally.
