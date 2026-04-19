# Application Protocol

Version 1.0 · Transport: TCP · 127.0.0.1:9999 by default

---

## 1. Frame format

Every message on the wire has the same envelope:

```
+--------------------+----------------------------+
|  4 bytes, BE u32   |   payload, N bytes         |
|  payload length N  |   UTF-8 JSON object        |
+--------------------+----------------------------+
```

* `length`: big-endian unsigned 32-bit integer, number of payload bytes.
  Hard cap of **10 MB** per frame — the receiver closes the connection
  on anything larger.
* `payload`: a UTF-8-encoded JSON object (not an array, not a scalar).
  Top-level type MUST be an object.

All numeric fields are JSON numbers.  Timestamps are Unix epoch in
**milliseconds** unless otherwise noted.

### Why length-prefix + JSON?

* No delimiter-escaping.  TCP is a byte stream; length-prefix is the
  simplest framing scheme that survives adversarial payloads.
* JSON is debuggable — you can tail-and-eyeball traffic, which was
  invaluable when chasing bugs during development.
* At this scale, the CPU cost of JSON is invisible next to I/O.

---

## 2. Message types

Every frame has a top-level `type` string.  The full set:

| Type | Direction | Purpose |
|---|---|---|
| `register`        | C → S | create an account |
| `register_resp`   | S → C | |
| `login`           | C → S | authenticate |
| `login_resp`      | S → C | |
| `logout`          | C → S | end the session |
| `logout_resp`     | S → C | |
| `create_group`    | C → S | create a new group (creator auto-joins) |
| `create_group_resp` | S → C | |
| `join_group`      | C → S | join an existing group |
| `join_group_resp` | S → C | |
| `leave_group`     | C → S | leave a group |
| `leave_group_resp`| S → C | |
| `list_groups`     | C → S | list the groups *I* am a member of |
| `list_groups_resp`| S → C | |
| `send_msg`        | C → S | send chat message (group or DM) |
| `send_msg_ack`    | S → C | server acknowledges persistence |
| `recv_msg`        | S → C | incoming chat message (broadcast) |
| `user_status`     | S → C | peer went online/offline/joined/left |
| `heartbeat`       | C → S | keep-alive |
| `heartbeat_resp`  | S → C | |
| `error`           | S → C | generic error envelope |

---

## 3. Request / response schemas

### 3.1 register

**Request**
```json
{ "type": "register", "username": "alice", "password": "secret" }
```

**Response**
```json
{ "type": "register_resp", "ok": true }
```
On failure:
```json
{ "type": "register_resp", "ok": false, "code": 1001, "msg": "user already exists" }
```

### 3.2 login

**Request**
```json
{ "type": "login", "username": "alice", "password": "secret" }
```
**Response**
```json
{ "type": "login_resp", "ok": true, "username": "alice" }
```
Failure codes: `1002` wrong password, `1005` already logged in elsewhere.

Side effect on success: the server broadcasts `user_status` with
`status: "online"` to everyone who shares a group with the new user.

### 3.3 logout

**Request**
```json
{ "type": "logout" }
```
**Response**
```json
{ "type": "logout_resp", "ok": true }
```
Side effect: broadcasts `user_status` with `status: "offline"`.

### 3.4 create_group / join_group / leave_group

Same shape for all three:

**Request**
```json
{ "type": "create_group", "group_name": "dev" }
```
**Response**
```json
{ "type": "create_group_resp", "ok": true, "group_id": 42, "group_name": "dev" }
```

`join_group` broadcasts `user_status {status:"joined"}` to the group;
`leave_group` broadcasts `status:"left"`.

### 3.5 list_groups

**Request**
```json
{ "type": "list_groups" }
```
**Response**
```json
{
  "type": "list_groups_resp",
  "groups": [
    { "group_name": "dev", "member_count": 3 },
    { "group_name": "qa",  "member_count": 2 }
  ]
}
```

### 3.6 send_msg

**Request**
```json
{
  "type": "send_msg",
  "target_type": "group",       // "group" or "user"
  "target":      "dev",         // group name or username
  "content_type": "text",       // "text" or "image"
  "content":     "hello world"  // text body, or base64 for image
}
```

**Response — ack**
```json
{ "type": "send_msg_ack", "ok": true, "msg_id": 128 }
```

**Response — broadcast**

The same message is wrapped in `recv_msg` and delivered to every
online recipient AND echoed back to the sender (so their own UI can
render the line).  The broadcast itself **excludes** the sender to
avoid duplicate delivery; the sender receives it via an explicit
separate write.  See LESSONS #3 for why this subtle split matters.

```json
{
  "type": "recv_msg",
  "msg_id":      128,
  "from":        "alice",
  "target_type": "group",
  "target":      "dev",
  "content_type": "text",
  "content":     "hello world",
  "ts":          1776585417480
}
```

### 3.7 user_status

Pushed by the server, never requested by the client.

```json
{
  "type": "user_status",
  "username":   "bob",
  "group_name": "dev",      // present for presence inside a group
  "status":     "online"    // online | offline | joined | left
}
```

### 3.8 heartbeat

```json
{ "type": "heartbeat" }
// -->
{ "type": "heartbeat_resp", "ok": true }
```

### 3.9 error (generic)

Whenever the server cannot map a request onto a typed response:
```json
{ "type": "error", "code": 1003, "msg": "not logged in" }
```

---

## 4. Error codes

| Code | Meaning |
|---:|---|
| 1001 | user already exists |
| 1002 | incorrect username or password |
| 1003 | not logged in |
| 1004 | group not found |
| 1005 | user already logged in from another session |
| 1006 | invalid or missing parameters |
| 9999 | unexpected server error |

---

## 5. Authentication gate

Before `login_resp.ok = true`, the only frames the server will accept
on a connection are `register`, `login`, and `heartbeat`.  Any other
type returns `error` with code `1003`.

---

## 6. Connection lifecycle

```
Client                                    Server
  |                                          |
  | ---- TCP connect ---------------------->  |
  |                                          | (connection accepted, no greeting)
  | ---- register ------------------------->  |
  | <--- register_resp ---------------------  |
  | ---- login ---------------------------->  |
  | <--- login_resp ------------------------  |
  | <--- (user_status broadcasts from peers) -|
  |                                          |
  | ...  normal message traffic ...          |
  |                                          |
  | ---- TCP FIN or socket error ----------   |  (abrupt or graceful)
  |                                          | server deletes session,
  |                                          | broadcasts offline status
```

---

## 7. Forward-compatible extensions (reserved)

The following are **not** implemented in v1.0 but the wire format
reserves space for them:

* `version` field at the top of every frame — for protocol-version
  negotiation.
* `ack` field on `recv_msg` — for a future end-to-end ACK + retry
  mechanism.  `send_msg_ack` already carries `msg_id`, which is the
  anchor for retry logic.
* `pull_history { group_name, before_ts, limit }` — for fetching
  persisted messages on reconnect.
* `admin_kick / admin_mute { target }` — for the administrator
  extensions.  Adding a `role` column to `users` is a one-migration
  change.
