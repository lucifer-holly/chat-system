# 8 Bugs That Almost Ate The 120 Minutes

Every bug below is real.  Every fix shipped.  Together they are a
short anthology of what happens when you let an AI write code fast
and a strategy layer (or a terminal) isn't watching closely.

### 1. The receive coroutine that never ran

The first version of the client called `loop.run_until_complete(connect)`
then `app.run()` — but `app.run()` spins up its **own** asyncio loop,
orphaning the background receive task.  The UI was alive, the socket
was connected, and nothing from the server ever arrived.
**Fix:** unify under one loop with `asyncio.run(client.run_async())` +
`await app.run_async()`.

### 2. `Buffer` is not a `BufferControl`

`Window.content` expects a `Control`.  The generated client passed a
raw `Buffer`, which blew up at layout construction with
`AttributeError: 'Buffer' object has no attribute 'is_focusable'`.
**Fix:** wrap it: `Window(content=BufferControl(buffer=buf))`.

### 3. The sender who saw their own message twice

The first `handle_send_msg` both broadcast the message to "all group
members" (sender included) *and* explicitly echoed it to the sender.
Logs showed the same `msg_id` arriving twice on every send.
**Fix:** broadcast with the sender excluded, then send exactly one
echo back to them.

### 4. The doubled system-message prefix

System notices were stored with a leading `[系统] ` then rendered with
another `[系统] ` prepended, producing `[系统] [系统] switched to …` in
the UI.
**Fix:** pick one layer — I stored the prefix in the content and
rendered without adding another.

### 5. `database is locked`

Multiple asyncio coroutines called `sqlite3.connect` concurrently; the
single-writer database rejected all but one and the rest fell over
with locking errors.
**Fix:** a single `asyncio.Lock` around every DB call, plus
`timeout=5.0, isolation_level=None` on the connection — zero
concurrency cost at this scale, problem disappears.

### 6. The AI that helpfully wiped the database on every restart

Between iterations the executor decided, on its own, that
`init_db` would be tidier if it deleted the database file first.
Server log dutifully printed `删除旧数据库` on every boot.  Acceptance
test #6 (persistence across restart) would have failed silently at
demo time.
**Fix:** an explicit rule — `init_schema` is `CREATE TABLE IF NOT
EXISTS`, never `DROP`.  Documented in the code.  *Never let the
executor decide to delete things.*

### 7. Logger output bleeding into the full-screen TUI

`logging.basicConfig()` with no handler argument sends INFO lines to
stderr; full-screen TUI owns stderr too, so live log messages
scrambled the UI.
**Fix:** route logging to `client.log` via `filename=`.  The UI stays
clean; log traffic is still available with `tail -f`.

### 8. Negative end-to-end latency

First stress-test run reported `p50 = -28ms`.  Receiver and sender
were each reading `time.time()`, but one stored seconds while the
other stored milliseconds, and the subtraction was written the wrong
way round.
**Fix:** one format for the wire (`send_ts_ms`), one subtraction
(`recv_ms − send_ms`), and an assertion that all latencies are
non-negative before we computed percentiles.

---

## The pattern in all eight

Five out of eight bugs were invisible from reading the code.  They
only appeared when I ran the thing and looked at:

* the terminal output (`删除旧数据库`),
* the log line counts (same `msg_id` appearing twice),
* the numbers (`-28ms`),
* the actual screen (`[系统] [系统] …`).

*Code review alone would have shipped a broken product.*  Every one
of these was caught by the discipline in
[AI_WORKFLOW.md](AI_WORKFLOW.md) rule #4: **run it, trust output over
code**.  That's the one rule you cannot skip.
