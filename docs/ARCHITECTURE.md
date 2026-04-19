# Architecture

## 1. Big picture

```
 ┌────────────────────┐         ┌────────────────────┐
 │  TUI Client A      │         │  TUI Client B      │
 │  prompt_toolkit    │         │  prompt_toolkit    │
 │  + asyncio         │         │  + asyncio         │
 └──────────┬─────────┘         └─────────┬──────────┘
            │                             │
            │  length-prefix + JSON       │
            │  over TCP                   │
            └──────────────┬──────────────┘
                           │
                 ┌─────────▼──────────┐
                 │   Chat Server      │
                 │   asyncio          │
                 │   - codec          │
                 │   - handlers       │
                 │   - session/state  │
                 └─────────┬──────────┘
                           │
                 ┌─────────▼──────────┐
                 │   SQLite (file)    │
                 │   users / groups   │
                 │   members / msgs   │
                 └────────────────────┘
```

Three layers, each testable in isolation:

| Layer | Code | Tested by |
|---|---|---|
| Wire protocol | `src/codec.py`, `src/protocol_types.py` | `tests/test_malformed.py` |
| Server logic  | `src/server.py`, `src/db.py`             | `tests/test_protocol.py`, `tests/test_stress.py` |
| TUI client    | `src/client.py`                          | manual acceptance + `tests/test_stress.py` (headless) |

## 2. Server internals

### 2.1 Connection model

* `asyncio.start_server` accepts TCP connections.
* Each connection runs its own `_handle_client` coroutine.
* Per-connection state lives in a `Session` dataclass (just `writer`
  and `username`).  All cross-connection state — the online-users
  map — lives in a single `ServerState`.

### 2.2 Dispatch

Message routing is table-driven:

```python
HANDLERS: dict[str, Handler] = {
    MsgType.REGISTER:     _h_register,
    MsgType.LOGIN:        _h_login,
    MsgType.SEND_MSG:     _h_send_msg,
    ...
}

ANONYMOUS_ALLOWED = {MsgType.REGISTER, MsgType.LOGIN, MsgType.HEARTBEAT}
```

This keeps `_handle_client` trivial: read a frame, look up the type,
authorise, call the handler, write the response.  Adding a new
message type is mechanically safe: add one enum value, one handler,
one row in the table.

### 2.3 Broadcast

`online_users: dict[username → StreamWriter]` is the single source of
truth for "who can receive pushes right now".

For a group message we:

1. Persist the message (to get `msg_id`).
2. Fetch the group's member usernames from SQLite.
3. For each member who is in `online_users` **and** is not the
   sender, push a `recv_msg`.
4. Echo `recv_msg` back to the sender exactly once via the sender's
   `writer`.
5. Return `send_msg_ack` through the normal response path.

The exclude-then-echo split matters — see LESSONS #3.

### 2.4 Persistence

SQLite is hit only through `src/db.py`, which wraps every call in
`asyncio.to_thread` and serialises through a single `asyncio.Lock`.
This costs us nothing at this scale (idle CPU measured at 0%) and
sidesteps the classic `database is locked` error that bites
asyncio + sqlite3 integrations.

Schema details are in `PROTOCOL.md`'s §7 and inline in `db.py`.

## 3. Client internals

### 3.1 One event loop, no exceptions

The most subtle correctness requirement for the client is that
the background receive task and the TUI render loop share **one**
asyncio event loop.  We do this by running the whole thing under a
single `asyncio.run(ChatClient().run())`, and starting the receive
task via `asyncio.create_task` inside that loop before awaiting
`app.run_async()`.

The common mistake — `app.run()` plus `loop.create_task(recv)` — puts
the receive task on a loop that never gets driven, which means the
client looks alive but receives nothing.  We hit this exact bug
during the interview, see LESSONS #1.

### 3.2 Layout

```
HSplit(
  Window(content=FormattedTextControl(_render_sessions_bar), height=1),
  Window(height=1, char="─"),
  Window(content=FormattedTextControl(_render_messages),   wrap=True),
  Window(height=1, char="─"),
  Window(content=BufferControl(_input_buffer),             height=1),
)
```

`Window.content` must wrap the underlying buffer/source in a
`Control` — putting a raw `Buffer` there raises
`AttributeError: 'Buffer' object has no attribute 'is_focusable'`.
See LESSONS #2.

### 3.3 Local state

`ChatState` owns:
* the current user and current-session keys,
* a per-session message list (`group:dev`, `user:bob`, etc.),
* a per-session unread counter, reset on `/switch`.

The UI re-reads `ChatState` on every render (via the callable `text=`
parameter), so mutations plus `app.invalidate()` are the full
"redraw" story.

## 4. Message flow — group chat

```
Client A                Server                     Client B
   │  send_msg            │                          │
   │─────────────────────>│                          │
   │                      │ 1. persist (msg_id)      │
   │                      │ 2. fetch group members   │
   │                      │ 3. broadcast (exclude A) │
   │                      │─────────────────────────>│  recv_msg
   │                      │ 4. echo to A             │
   │<─────────────────────│  recv_msg                │
   │                      │ 5. ack                   │
   │<─────────────────────│  send_msg_ack            │
```

## 5. Failure modes handled

| Failure | How the system reacts |
|---|---|
| Malformed frame (bad length, non-UTF8, non-JSON, non-object) | `read_frame` returns `None`; `_handle_client` breaks the loop and closes the connection.  Other connections are unaffected (`test_malformed` verifies this). |
| Peer crash / network hiccup | `StreamWriter.write` fails; `write_frame` returns `False`; the `finally` branch of `_handle_client` removes the user from `online_users` and broadcasts `offline`. |
| Database contention | Serialised through `asyncio.Lock`; no "database is locked" errors observed. |
| Duplicate login of the same user | Second login is rejected with error 1005 (existing session stays alive). |
| Server restart | `init_schema` is idempotent; users/groups/messages all persist to disk.  Client must reconnect. |

## 6. Things deliberately left simple

* **No encryption**.  Spec permitted plaintext; TLS would be a drop-in
  (wrap `asyncio.start_server` / `open_connection` with an SSL context).
* **No history replay**.  Clients only see messages that arrive after
  they connect; the schema holds every message and a `pull_history`
  RPC is an hour's work.
* **No ACK + retry at the app layer**.  TCP already guarantees in-order
  delivery; app-level ACK would defend against *our* server crashing
  mid-frame, which is out of scope here.
* **Passwords via SHA-256**, not a proper KDF.  Spec explicitly
  allowed plaintext; this is a conservative strengthening but not
  production-grade.  Switch to argon2 when you put this anywhere
  real — it's a one-line change in `db.sha256_hex`.
