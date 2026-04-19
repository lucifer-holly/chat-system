# Test Report

## Environment

| Item | Value |
|---|---|
| Python | 3.11.5 |
| OS | Linux (exam sandbox) |
| Endpoint | 127.0.0.1:9999 (loopback) |
| Date | 2026-04-19 |

## Acceptance matrix

The interview spec listed seven acceptance dimensions.  All seven pass
in the live sandbox.

| # | Dimension | Method | Command | Result |
|---:|---|---|---|:---:|
| 1 | Basic flow (register / login / group / DM / text + image) | Two-client manual walkthrough + automated smoke | `python -m tests.test_protocol` | PASS |
| 2 | Multi-session switch + unread counter | Three-client TUI walkthrough | manual | PASS |
| 3 | Concurrency + message pressure | 50-client stress, 100 messages, percentile latencies | `python -m tests.test_stress` | PASS |
| 4 | Disconnect detection + notification | Ctrl-C one client; other clients see `offline` broadcast | manual | PASS |
| 5 | Protocol fuzzing | 6 kinds of malformed frames; server must survive | `python -m tests.test_malformed` | PASS |
| 6 | Persistence across restart | create users/groups → stop server → restart → re-login | manual | PASS |
| 7 | TUI responsiveness under load | Run stress test while a human-driven TUI is open in another terminal; UI stays smooth | manual | PASS |

## Stress test — numbers

Running `tests/test_stress.py` produced the following numbers during the
interview:

| Metric | Target | Measured | Result |
|---|---:|---:|:---:|
| Concurrent connections | ≥ 50 | 50 / 50 | PASS |
| End-to-end latency, p50 | ≤ 500 ms | 22 ms | PASS |
| End-to-end latency, p95 | ≤ 500 ms | 32 ms | PASS |
| End-to-end latency, p99 | ≤ 500 ms | 49 ms | PASS |
| End-to-end latency, max | ≤ 500 ms | 79 ms | PASS |
| Idle CPU (50 connections, 30 s) | ≤ 5 % | 0.0 % | PASS |

Parameters used:

* 50 clients, all joined to one group `stress_group`
* Single sender (`user0001`) pushing 100 messages at 50 ms interval
* Latency samples = 100 × 50 = **5000** receiver-side measurements
* CPU sampled once per second for 30 s via `psutil.Process.cpu_percent`

## Protocol fuzz — what was sent

| Sub-test | Payload | Expected | Measured |
|---|---|---|---|
| A | 4-byte length + raw `\xff\xfe\xfd\xfc` | server closes our conn, keeps running | PASS |
| B | 4-byte length + ASCII `this is not json` | same | PASS |
| C | length prefix of 20 MB (no payload) | server enforces 10 MB cap, closes conn | PASS |
| D | length prefix = 0 | rejected, conn closed | PASS |
| E | valid JSON but not an object (`[1,2,3]`) | rejected, conn closed | PASS |
| F | a fresh connection issues `heartbeat` after A–E | `heartbeat_resp` returned normally | PASS |

F is the critical one — it verifies the server's error handling does
not leak into global state.

## Known limitations (deferred, not bugs)

* Chat history on reconnect is not replayed — messages persist, but the
  pull RPC isn't wired up yet.  `docs/PROTOCOL.md` §7 reserves the
  message type.
* Offline inbox (deliver when recipient comes back online) — same
  story; the storage is already there.
* Per-message ACK with retry is not implemented; `msg_id` is already
  returned so the extension is additive.
* Protocol version negotiation: the field is reserved but not checked.
* Admin actions (kick / mute / list online): no admin role yet;
  schema-friendly addition.

## Conclusion

Every must-have acceptance test passes with substantial headroom
(p99 is **~10×** under the 500 ms target; idle CPU sits at 0 %).
The system is ready for demo.
