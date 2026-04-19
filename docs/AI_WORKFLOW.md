# The AI-Coding Workflow That Shipped This Project

I wrote this document because most people who say "I use AI to code"
mean *I type prompts into ChatGPT*.  That's not a workflow — that's
tool use.  A workflow is the set of rules that keep a fast, fallible
executor from destroying your weekend.

The rules here are the ones I actually followed for this project.
They worked; every acceptance test passed inside the 120-minute
budget.  They'll port to any AI-assisted engineering task.

---

## The three-layer model

I don't work with "the AI".  I work with three roles, and I keep them
physically separated:

```
┌─────────────────────────────────────────────────────────┐
│  STRATEGY — in a browser tab                            │
│  Chat with a reasoning-heavy model (Claude/GPT/etc.)    │
│  Owns: requirement-cracking, architecture, prompt       │
│  design, code review, debugging direction, pacing.      │
│  Does NOT touch the repo directly.                      │
└─────────────────────┬───────────────────────────────────┘
                      │ hand-written prompts
                      ▼
┌─────────────────────────────────────────────────────────┐
│  EXECUTION — inside the IDE (CodeFuse / Claude Code /   │
│  Cursor / Windsurf)                                     │
│  Owns: writing files, running shells, reading the       │
│  actual state of the repo.                              │
│  Does NOT make architectural calls of its own.          │
└─────────────────────┬───────────────────────────────────┘
                      │ code, logs, errors
                      ▼
┌─────────────────────────────────────────────────────────┐
│  VERIFICATION — my own hands in a terminal              │
│  Owns: running the binary, eyeballing the output.       │
│  Nothing is "done" until I've seen it work.             │
└─────────────────────────────────────────────────────────┘
```

Why separate strategy from execution at all?  Because the same
model, pointed at the same repository, will rationalise its own
choices.  It's the AI analogue of a senior engineer reviewing their
own pull request — there are whole classes of bugs you cannot see
from inside the problem.

Splitting forces an **adversarial** relationship between the two
roles: strategy's job is to doubt what execution produced.  Those
doubts caught every one of the eight real bugs I hit.  See
[LESSONS.md](LESSONS.md) for the body count.

## The five rules that actually matter

### 1. Open a strategy chat per project, and use it from minute zero

First message in that chat is the context dump:

> I'm going to build X. Here are the constraints. Here is my budget
> in time and in tokens. You're my strategy layer; I have another AI
> in the IDE that executes. I'll paste requirements, you hand me
> prompts. I'll paste output, you tell me what's wrong.

Do this once, at the start.  It saves the same paragraph from being
implicit in every subsequent message, and sets the role contract.
Don't switch chats mid-project — rolling context is where the
strategy layer earns its keep.

### 2. Never type prompts directly into the executor

This is the hardest habit to build, because it feels slower.

```
   instinct:       me ──────────────────> executor
                        (ad-hoc prose)

   the discipline:  me ── vague ask ──> strategy ── structured ──> executor
                                             prompt
```

Forcing the detour through strategy does three things:

* it makes me articulate the ambiguous bits of my own request,
* it turns the request into a prompt with real structure (context /
  input / output / constraints / acceptance criteria),
* it gives me a textual artifact I can review before it hits the
  repo.

The strategy layer's prompts are almost always better than mine,
not because the model is smarter than I am in some cosmic sense,
but because I have exactly one working memory and it's full of the
actual problem.

### 3. Every produced artefact gets pasted back for review

The loop is:

```
  strategy ──> prompt ──> executor ──> code/result ──> strategy ──> ...
```

Every time the executor produces code, a log line, a test output —
paste it into the strategy chat before you do anything else with
it.  This is where "adversarial review" happens.  Strategy will
tell you:

* which edge case the executor missed,
* which assumption in the prompt it quietly ignored,
* which of the output numbers don't actually make sense
  (negative latency, anyone?).

The loop usually runs 10–20 times on a non-trivial project.  Each
iteration closes a tight gap.  Skipping iterations = accumulated
drift = a demo that works "most of the time".

### 4. Run the thing.  Don't trust code, trust output.

The executor will tell you the code works.  The strategy layer will
tell you the code looks correct.  Both will be wrong sometimes —
and the only ground-truth is a terminal session.

My hard rule: no task is complete until I've seen, with my own eyes,
either (a) the program produce the expected output, or (b) a test
script print PASS with numbers I could recompute by hand.

Half of the bugs in [LESSONS.md](LESSONS.md) were invisible from
reading the code — they surfaced only when I ran the thing and the
log said something like `"删除旧数据库"` that I hadn't asked for.

### 5. Compact the strategy chat when it gets woolly

A long strategy conversation slowly loses its edge, because the
model starts repeating itself and gets flooded with detail.

Symptoms: it asks you things you already told it; its prompts
start drifting from the shape of the earlier ones; you catch it
suggesting something you already rejected two hours ago.

Remedies, in order of preference:

1. `/compact` if your tool supports it.  Keeps the ongoing state,
   drops the raw transcript.
2. Start a fresh strategy chat, seeded with a short summary of
   what's decided so far, what's open, and the current state of the
   repo.

Either is fine; the important thing is to notice the degradation
and intervene.  I did exactly this once during the interview,
around the 100-minute mark.

## How to write prompts for the executor

Strategy-layer prompts to the execution layer are not conversation.
They are miniature engineering specs.  A good one has five blocks:

1. **Context** — what the repo already looks like, what worked in
   the previous step.
2. **Task** — one clear thing, e.g. "add login + broadcast", not
   "build the chat features".
3. **Interface/schema** — exact field names, exact response shapes.
   The fewer degrees of freedom the executor has, the less it
   drifts.
4. **Constraints** — hard don'ts.  "Do not modify codec.py."
   "Do not delete the database." (Yes, both of those became needed.)
5. **Acceptance** — how to know it worked.  Usually a shell command
   to run and the expected output.

Anti-patterns to avoid:

* "Please think carefully about ..." — prompting fluff; wastes
  tokens and biases the model toward flowery answers.
* Open-ended "add features" — give the executor exactly one
  atomic change at a time.
* Embedding a code block that should actually just be a file to
  overwrite.  If you want file state X, tell the executor to
  write file state X — don't show it X and hope.

## What the executor is allowed to decide

* Local naming (function names, variable names).
* Imports and small refactors inside the file it's writing.
* Which small library helper to call.

## What it is not allowed to decide

* Protocol shapes (those are strategy-level decisions).
* File structure and module boundaries.
* Database schema changes.
* Whether to delete anything (this one costs the most when it
  goes wrong — see LESSONS #6).

When the executor tries to exercise authority it doesn't have,
strategy pulls it back in the next prompt.  This is almost every
prompt for the first ten minutes.

## Why this maps to Poffices.AI's multi-agent design

The strategy/execution/verification split is the same architecture
we deploy in Poffices.AI for our enterprise clients:

| Poffices.AI agent | Role in this workflow |
|---|---|
| Analysis agent — drafts answers | Execution layer — writes the code |
| Verification agent — fact-checks | Strategy layer — reviews, flags, corrects |
| Human-in-the-loop — approves/edits | Me — runs the thing, signs off |

The discipline that works for a law firm running contract analysis
with Claude is the same discipline that shipped this chat server.
Multi-agent verification is not a product feature — it's a
methodology, and you can apply it to your own IDE in five minutes.

## Tooling recommendations, as of 2026-04

| Layer | My pick | Why |
|---|---|---|
| Strategy | Claude.ai web or desktop | long-context chat, separate from IDE, good at structured prompt output |
| Execution | Claude Code / Cursor / CodeFuse | whichever lets the AI actually write files and run shells |
| Verification | a real terminal | nothing replaces eyeballs on stdout |

Two-IDE setups (e.g. two Claude Code windows pointed at the same
repo) *do* give you role separation, but they share the same
mental model and the same blind spots.  A proper strategy layer
needs to be in a different surface entirely — browser tab beats
tab-inside-IDE every time.

## The meta-point

"AI coding" is not about replacing programmers with prompt-slinging.
It's about *running your engineering process with AI filling the
seats you used to fill yourself.*  Once you accept that, all the
rules above are the normal rules of software engineering — code
review, separation of concerns, empirical verification — applied to
a team that happens to be made of models.
