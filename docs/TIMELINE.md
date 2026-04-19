# The 120 Minutes, Minute by Minute

A condensed log of the interview.  Timestamps are elapsed minutes
since the terminal said `READY?`.

---

### t = 0 — 5   · Planning, not typing

Read the spec twice.  Did **not** touch the IDE.  Decided:

* Language: Python 3.11 — the model knows it best, asyncio fits the
  "50 concurrent connections, idle CPU" target naturally.
* Framing: 4-byte BE length + JSON — simple, debuggable, covers the
  extensibility requirement without a schema compiler.
* TUI: `prompt_toolkit` — the only thing that reliably does
  non-blocking input + message area in a terminal.
* Persistence: SQLite stdlib, no ORM.
* Time budget:

  | Slice | Goal |
  |---:|---|
  | 0–15 | Skeleton of all five files, echo-server walking |
  | 15–45 | Server core: auth, groups, messaging, broadcast |
  | 45–80 | Client TUI: full command set, multi-session |
  | 80–100 | Persistence + disconnect + stress/fuzz tests |
  | 100–120 | Docs, one-click start, final walkthrough |

### t = 10 — 15   · First files land; first lesson

AI generated `codec.py`, `server.py` (echo only), `client.py`,
`protocol.md`, `run.sh`.  **Files did not actually exist on disk** —
the chat showed code, the filesystem showed nothing.

> *Lesson*: test the executor's "does it really write files?"
> ability before pushing real work through it.

Sent a trivial `touch test.txt` prompt.  Confirmed it *does* write.
Re-sent the skeleton prompt.

### t = 15 — 25   · Echo server is alive

`python server.py` printed the listen line.  `python client.py` crashed:

```
AttributeError: 'Buffer' object has no attribute 'is_focusable'
```

First real bug — see [LESSONS #2](LESSONS.md#2-buffer-is-not-a-buffercontrol).
One targeted fix prompt, 90 seconds, done.  Client showed the echo
of its own message.  First green light.

### t = 25 — 50   · Server core

One big prompt, nine handlers: `register`, `login`, `create_group`,
`join_group`, `leave_group`, `list_groups`, `send_msg`, `logout`,
`heartbeat`.  Plus a dedicated `test_protocol.py` that walks the
full flow from a single synthetic client.

First test run failed at the `send_msg` step: responses came back
out of order.  Closer look at the output — the server was
delivering `recv_msg` to the sender **twice**, then the `ack`.
See [LESSONS #3](LESSONS.md#3-the-sender-who-saw-their-own-message-twice).

While fixing that, the next run tripped over
`database is locked`.  See [LESSONS #5](LESSONS.md#5-database-is-locked).
Two fixes in one prompt; green on retry.

### t = 50 — 65   · Client TUI, real UI

Rewrote `client.py` into a single-loop architecture, three-pane
layout, full command set, background receive task.  Opened two
terminals, registered `alice` and `bob`, sent messages.  They arrived.

### t = 65 — 80   · Polish + presence

Cleaned up the `[系统] [系统]` doubled prefix
([LESSONS #4](LESSONS.md#4-the-doubled-system-message-prefix)),
routed logging to a file so INFO lines stopped scribbling over the
UI ([LESSONS #7](LESSONS.md#7-logger-output-bleeding-into-the-full-screen-tui)).
Added `user_status` broadcasts on join/leave/online/offline.

### t = 80 — 95   · Stress test + one ugly discovery

Wrote `test_stress.py`: 50 concurrent clients, 100 messages, percentile
latencies, 30 s idle CPU sampling with `psutil`.

First run:

```
latency p50 = -28ms     p95 = -16ms     max = -14ms
```

Negative latency.  See
[LESSONS #8](LESSONS.md#8-negative-end-to-end-latency).  Fix
shipped, second run gave the numbers in [TEST_REPORT.md](TEST_REPORT.md):
p99 = 49 ms, idle CPU = 0.0 %.

Then ran `test_malformed.py` — five garbage payloads plus one sanity
heartbeat on a fresh connection.  Server closed the bad sockets and
kept serving the good one.  PASS.

### t = 95 — 100   · Persistence.  Almost.

Restarted the server to verify data survived.  Users and groups:
gone.  Log said `删除旧数据库` on every restart.  The executor had
quietly inserted `os.remove(DB_FILE)` into `init_db` between
iterations.  See [LESSONS #6](LESSONS.md#6-the-ai-that-helpfully-wiped-the-database-on-every-restart).

Removed it, re-ran, re-created a group, restarted the server,
re-logged in — group was still there.  Acceptance #6 saved.

### t = 100 — 115   · Docs

`README.md`, `ARCHITECTURE.md`, `TEST_REPORT.md`.  Pulled real
numbers from the stress-test report — no fake data in the doc.

`README.md` turned out to be write-protected on the exam machine
(it was the spec file).  Created `DELIVERY.md` with the same content.

While writing docs, spotted four empty files called things like
`编码` and `JSON` in `/home/exam/`.  Turned out the executor had
momentarily misread bits of the doc content as filenames.  `rm` and
carry on.

### t = 115 — 120   · Clean up, submit

```
ls -la /home/exam/
```

Fourteen files, no stray `__pycache__`, no empty junk.  `run.sh`
tested one more time — started cleanly, printed the usage banner.
Hit **Submit**.

---

## Retrospective, in three numbers

* **8** real bugs from AI-generated code, 8 caught before submit.
* **~15** strategy → executor round-trips.
* **0** bugs shipped into the submission.

The margin came from one habit: *after every change, run the thing
and look at the output.*  Everything else is bookkeeping.
