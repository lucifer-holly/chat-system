"""
Async chat server.

One asyncio task per TCP connection.  Per-connection state (the logged-in
username, if any) is carried in a Session dataclass passed to every
handler — this is simpler than the more idiomatic "stateful object per
connection" pattern and maps nicely onto the single handler loop.

The dispatch is table-driven: each incoming `type` is looked up in
`HANDLERS` and routed to the corresponding async function.  Adding a new
message type means adding one row to the table and one handler function,
no parser edits.

Persistence is delegated entirely to `db.Database`; this file should not
contain any SQL except the strings passed to `db.execute/.fetchone/.fetchall`.

Broadcast is done through the `online_users` dict, which maps
username → StreamWriter for every presently-connected session.  Clean-up
on disconnect is in the `finally` block of `_handle_client`.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .codec import read_frame, write_frame
from .db import Database, sha256_hex
from .protocol_types import (
    ContentType,
    ErrorCode,
    MsgType,
    TargetType,
    UserStatus,
)

# ------------------------------------------------------------- configuration

HOST = "127.0.0.1"
PORT = 9999
DB_FILE = "chat.db"

logger = logging.getLogger(__name__)


# ------------------------------------------------------------- runtime state


@dataclass
class Session:
    """Per-connection state."""

    writer: asyncio.StreamWriter
    username: str | None = None  # None until `login` succeeds


@dataclass
class ServerState:
    """All mutable state lives here so it's easy to reason about / test."""

    db: Database
    online_users: dict[str, asyncio.StreamWriter] = field(default_factory=dict)


# A handler receives (state, session, request_frame) and returns the
# response frame to send back.  Broadcasts are done as side-effects
# inside the handler (via `state.online_users`).
Handler = Callable[
    [ServerState, Session, dict], Awaitable[dict]
]


# ------------------------------------------------------------------ helpers


def _error(code: ErrorCode, msg: str) -> dict:
    return {"type": MsgType.ERROR, "code": int(code), "msg": msg}


async def _broadcast(
    state: ServerState,
    recipients: list[str],
    frame: dict,
    *,
    exclude: str | None = None,
) -> None:
    """Send `frame` to every currently-online user in `recipients`."""
    for user in recipients:
        if user == exclude:
            continue
        w = state.online_users.get(user)
        if w is None:
            continue
        await write_frame(w, frame)


async def _group_member_names(state: ServerState, group_name: str) -> list[str]:
    rows = await state.db.fetchall(
        """
        SELECT u.username
          FROM users u
          JOIN group_members gm ON gm.user_id = u.id
          JOIN groups g         ON g.id       = gm.group_id
         WHERE g.group_name = ?
        """,
        (group_name,),
    )
    return [r[0] for r in rows]


async def _user_groups(state: ServerState, username: str) -> list[str]:
    rows = await state.db.fetchall(
        """
        SELECT g.group_name
          FROM groups g
          JOIN group_members gm ON gm.group_id = g.id
          JOIN users u          ON u.id        = gm.user_id
         WHERE u.username = ?
        """,
        (username,),
    )
    return [r[0] for r in rows]


# -------------------------------------------------------------- auth handlers


async def _h_register(state: ServerState, session: Session, req: dict) -> dict:
    username = req.get("username")
    password = req.get("password")
    if not username or not password:
        return {
            "type": MsgType.REGISTER_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "username and password are required",
        }

    existing = await state.db.fetchone(
        "SELECT id FROM users WHERE username = ?", (username,)
    )
    if existing is not None:
        return {
            "type": MsgType.REGISTER_RESP,
            "ok": False,
            "code": int(ErrorCode.USER_EXISTS),
            "msg": "user already exists",
        }

    await state.db.execute(
        "INSERT INTO users(username, password_sha256) VALUES(?, ?)",
        (username, sha256_hex(password)),
    )
    return {"type": MsgType.REGISTER_RESP, "ok": True}


async def _h_login(state: ServerState, session: Session, req: dict) -> dict:
    username = req.get("username")
    password = req.get("password")
    if not username or not password:
        return {
            "type": MsgType.LOGIN_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "username and password are required",
        }

    row = await state.db.fetchone(
        "SELECT password_sha256 FROM users WHERE username = ?", (username,)
    )
    if row is None or row[0] != sha256_hex(password):
        return {
            "type": MsgType.LOGIN_RESP,
            "ok": False,
            "code": int(ErrorCode.WRONG_PASSWORD),
            "msg": "incorrect username or password",
        }

    if username in state.online_users:
        return {
            "type": MsgType.LOGIN_RESP,
            "ok": False,
            "code": int(ErrorCode.ALREADY_LOGGED_IN),
            "msg": "this account is already logged in elsewhere",
        }

    state.online_users[username] = session.writer
    session.username = username

    # Broadcast presence to everyone who shares a group with this user.
    groups = await _user_groups(state, username)
    for g in groups:
        members = await _group_member_names(state, g)
        await _broadcast(
            state,
            members,
            {
                "type": MsgType.USER_STATUS,
                "username": username,
                "group_name": g,
                "status": UserStatus.ONLINE,
            },
            exclude=username,
        )

    logger.info("login ok: %s", username)
    return {"type": MsgType.LOGIN_RESP, "ok": True, "username": username}


async def _h_logout(state: ServerState, session: Session, req: dict) -> dict:
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")
    await _disconnect_user(state, session.username)
    session.username = None
    return {"type": MsgType.LOGOUT_RESP, "ok": True}


async def _disconnect_user(state: ServerState, username: str) -> None:
    """Shared code path for graceful logout and abrupt disconnect."""
    state.online_users.pop(username, None)
    groups = await _user_groups(state, username)
    for g in groups:
        members = await _group_member_names(state, g)
        await _broadcast(
            state,
            members,
            {
                "type": MsgType.USER_STATUS,
                "username": username,
                "group_name": g,
                "status": UserStatus.OFFLINE,
            },
            exclude=username,
        )


# ----------------------------------------------------------- group handlers


async def _h_create_group(state: ServerState, session: Session, req: dict) -> dict:
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")
    group_name = req.get("group_name")
    if not group_name:
        return {
            "type": MsgType.CREATE_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "group_name required",
        }

    existing = await state.db.fetchone(
        "SELECT id FROM groups WHERE group_name = ?", (group_name,)
    )
    if existing is not None:
        return {
            "type": MsgType.CREATE_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "group already exists",
        }

    user_row = await state.db.fetchone(
        "SELECT id FROM users WHERE username = ?", (session.username,)
    )
    user_id = user_row[0]
    group_id = await state.db.execute(
        "INSERT INTO groups(group_name, creator_id) VALUES(?, ?)",
        (group_name, user_id),
    )
    await state.db.execute(
        "INSERT INTO group_members(group_id, user_id) VALUES(?, ?)",
        (group_id, user_id),
    )
    return {
        "type": MsgType.CREATE_GROUP_RESP,
        "ok": True,
        "group_id": group_id,
        "group_name": group_name,
    }


async def _h_join_group(state: ServerState, session: Session, req: dict) -> dict:
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")
    group_name = req.get("group_name")
    if not group_name:
        return {
            "type": MsgType.JOIN_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "group_name required",
        }

    g = await state.db.fetchone(
        "SELECT id FROM groups WHERE group_name = ?", (group_name,)
    )
    if g is None:
        return {
            "type": MsgType.JOIN_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.GROUP_NOT_FOUND),
            "msg": "group not found",
        }

    u = await state.db.fetchone(
        "SELECT id FROM users WHERE username = ?", (session.username,)
    )
    try:
        await state.db.execute(
            "INSERT INTO group_members(group_id, user_id) VALUES(?, ?)",
            (g[0], u[0]),
        )
    except Exception:  # noqa: BLE001
        # UNIQUE(group_id,user_id) → already a member, treat as no-op.
        pass

    members = await _group_member_names(state, group_name)
    await _broadcast(
        state,
        members,
        {
            "type": MsgType.USER_STATUS,
            "username": session.username,
            "group_name": group_name,
            "status": UserStatus.JOINED,
        },
        exclude=session.username,
    )
    return {
        "type": MsgType.JOIN_GROUP_RESP,
        "ok": True,
        "group_name": group_name,
    }


async def _h_leave_group(state: ServerState, session: Session, req: dict) -> dict:
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")
    group_name = req.get("group_name")
    if not group_name:
        return {
            "type": MsgType.LEAVE_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "group_name required",
        }

    g = await state.db.fetchone(
        "SELECT id FROM groups WHERE group_name = ?", (group_name,)
    )
    if g is None:
        return {
            "type": MsgType.LEAVE_GROUP_RESP,
            "ok": False,
            "code": int(ErrorCode.GROUP_NOT_FOUND),
            "msg": "group not found",
        }

    u = await state.db.fetchone(
        "SELECT id FROM users WHERE username = ?", (session.username,)
    )
    await state.db.execute(
        "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
        (g[0], u[0]),
    )
    members = await _group_member_names(state, group_name)
    await _broadcast(
        state,
        members,
        {
            "type": MsgType.USER_STATUS,
            "username": session.username,
            "group_name": group_name,
            "status": UserStatus.LEFT,
        },
    )
    return {
        "type": MsgType.LEAVE_GROUP_RESP,
        "ok": True,
        "group_name": group_name,
    }


async def _h_list_groups(state: ServerState, session: Session, req: dict) -> dict:
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")

    rows = await state.db.fetchall(
        """
        SELECT g.group_name, COUNT(gm2.user_id) AS member_count
          FROM groups g
          JOIN group_members gm  ON gm.group_id = g.id
          JOIN users u           ON u.id        = gm.user_id
          JOIN group_members gm2 ON gm2.group_id = g.id
         WHERE u.username = ?
         GROUP BY g.id
        """,
        (session.username,),
    )
    return {
        "type": MsgType.LIST_GROUPS_RESP,
        "groups": [{"group_name": r[0], "member_count": r[1]} for r in rows],
    }


# --------------------------------------------------------- messaging handler


async def _h_send_msg(state: ServerState, session: Session, req: dict) -> dict:
    """Deliver a chat message to its recipients, persist it, and ack.

    IMPORTANT:  the broadcast MUST exclude the sender, because we then
    deliver to the sender via a single explicit write_frame.  If we did
    both, the sender would see their own message twice — this was the
    single most confusing bug during the interview.  See LESSONS.md #3.
    """
    if session.username is None:
        return _error(ErrorCode.NOT_LOGGED_IN, "not logged in")

    target_type = req.get("target_type")
    target = req.get("target")
    content_type = req.get("content_type", ContentType.TEXT)
    content = req.get("content", "")

    if target_type not in (TargetType.GROUP, TargetType.USER) or not target:
        return {
            "type": MsgType.SEND_MSG_ACK,
            "ok": False,
            "code": int(ErrorCode.INVALID_PARAMS),
            "msg": "target_type and target required",
        }

    # Persist first so `msg_id` is available to echo in the ACK.
    u = await state.db.fetchone(
        "SELECT id FROM users WHERE username = ?", (session.username,)
    )
    msg_id = await state.db.execute(
        """
        INSERT INTO messages(
            sender_id, target_type, target_name, content_type, content
        ) VALUES(?, ?, ?, ?, ?)
        """,
        (u[0], target_type, target, content_type, content),
    )

    ts_ms = int(time.time() * 1000)
    recv_frame = {
        "type": MsgType.RECV_MSG,
        "msg_id": msg_id,
        "from": session.username,
        "target_type": target_type,
        "target": target,
        "content_type": content_type,
        "content": content,
        "ts": ts_ms,
    }

    if target_type == TargetType.GROUP:
        members = await _group_member_names(state, target)
        await _broadcast(state, members, recv_frame, exclude=session.username)
    else:  # direct message to a single user
        peer_writer = state.online_users.get(target)
        if peer_writer is not None:
            await write_frame(peer_writer, recv_frame)
        # If the peer is offline we still persist; see LESSONS.md for the
        # "offline inbox" extension.

    # Echo to the sender so their UI renders their own line.
    await write_frame(session.writer, recv_frame)

    return {"type": MsgType.SEND_MSG_ACK, "ok": True, "msg_id": msg_id}


# --------------------------------------------------------- keep-alive handler


async def _h_heartbeat(state: ServerState, session: Session, req: dict) -> dict:
    return {"type": MsgType.HEARTBEAT_RESP, "ok": True}


# ----------------------------------------------------------- dispatch table


HANDLERS: dict[str, Handler] = {
    MsgType.REGISTER: _h_register,
    MsgType.LOGIN: _h_login,
    MsgType.LOGOUT: _h_logout,
    MsgType.CREATE_GROUP: _h_create_group,
    MsgType.JOIN_GROUP: _h_join_group,
    MsgType.LEAVE_GROUP: _h_leave_group,
    MsgType.LIST_GROUPS: _h_list_groups,
    MsgType.SEND_MSG: _h_send_msg,
    MsgType.HEARTBEAT: _h_heartbeat,
}

# These three don't require authentication.
ANONYMOUS_ALLOWED = {MsgType.REGISTER, MsgType.LOGIN, MsgType.HEARTBEAT}


# ------------------------------------------------------- connection handler


async def _handle_client(
    state: ServerState,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    peer = writer.get_extra_info("peername")
    logger.info("connection from %s", peer)
    session = Session(writer=writer)

    try:
        while True:
            frame = await read_frame(reader)
            if frame is None:
                logger.info("peer %s closed / malformed", peer)
                break

            msg_type = frame.get("type")
            handler = HANDLERS.get(msg_type) if isinstance(msg_type, str) else None

            if handler is None:
                await write_frame(
                    writer,
                    _error(ErrorCode.INVALID_PARAMS, f"unknown type: {msg_type!r}"),
                )
                continue

            if session.username is None and msg_type not in ANONYMOUS_ALLOWED:
                await write_frame(
                    writer, _error(ErrorCode.NOT_LOGGED_IN, "login first")
                )
                continue

            try:
                response = await handler(state, session, frame)
            except Exception as exc:  # noqa: BLE001
                logger.exception("handler %s crashed", msg_type)
                response = _error(ErrorCode.UNKNOWN, str(exc))

            await write_frame(writer, response)

    except Exception as exc:  # noqa: BLE001
        logger.exception("connection loop error for %s: %s", peer, exc)
    finally:
        # Symmetric cleanup regardless of how the loop exited.
        if session.username is not None:
            await _disconnect_user(state, session.username)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        logger.info("disconnected %s", peer)


# ----------------------------------------------------------------- entrypoint


async def run_server(
    host: str = HOST,
    port: int = PORT,
    db_file: str | Path = DB_FILE,
) -> None:
    db = Database(db_file)
    await db.init_schema()
    state = ServerState(db=db)

    server = await asyncio.start_server(
        lambda r, w: _handle_client(state, r, w),
        host,
        port,
        reuse_address=True,
    )
    addr = server.sockets[0].getsockname()
    logger.info("listening on %s", addr)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows or similar — we'll rely on KeyboardInterrupt instead.
            pass

    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop_event.wait()
        logger.info("shutting down")
        server.close()
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
