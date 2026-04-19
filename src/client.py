"""
Terminal UI client built on prompt_toolkit.

Layout (full-screen):

    +------------------------------------------------------------+
    |  [alice]  *group:dev  user:bob (3)  group:qa (1) | /help   |  <- sessions bar (1 line)
    +------------------------------------------------------------+
    |  [12:04:17] <alice>  hello everyone                        |
    |  [12:04:22] <bob>    ello alice                            |
    |  ...                                                       |  <- message area (flex)
    +------------------------------------------------------------+
    | > _                                                        |  <- input (1 line)
    +------------------------------------------------------------+

Key design decisions
--------------------
* ONE asyncio event loop.  We `await app.run_async()` instead of the
  common `app.run()`, because the latter creates its own loop and would
  orphan the background receive task.  See LESSONS.md #1.
* Window.content must be a BufferControl / FormattedTextControl, never
  a raw Buffer.  See LESSONS.md #2.
* Logging goes to client.log, NEVER stdout — the TUI owns the terminal
  and any stray print would scramble the screen.  See LESSONS.md #7.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

from .codec import read_frame, write_frame
from .protocol_types import ContentType, MsgType, TargetType

HOST = "127.0.0.1"
PORT = 9999

logging.basicConfig(
    level=logging.INFO,
    filename="client.log",
    filemode="a",
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- state


@dataclass
class ChatState:
    """Local UI state.  Transient; not persisted across client restarts
    in this version (persistence would be a straightforward extension:
    dump/restore `sessions` to a JSON file on disk)."""

    current_user: Optional[str] = None
    current_session: Optional[str] = None          # "group:dev" or "user:bob"
    sessions: dict[str, list[dict]] = field(default_factory=dict)
    unread: dict[str, int] = field(default_factory=dict)

    def add_message(self, session_key: str, msg: dict) -> None:
        self.sessions.setdefault(session_key, []).append(msg)
        if session_key != self.current_session:
            self.unread[session_key] = self.unread.get(session_key, 0) + 1

    def switch(self, session_key: str) -> None:
        self.sessions.setdefault(session_key, [])
        self.current_session = session_key
        self.unread[session_key] = 0


# -------------------------------------------------------------------- render


def _render_sessions_bar(s: ChatState) -> str:
    if s.current_user is None:
        return "[offline]  /register <u> <p>  |  /login <u> <p>  |  /help"

    parts: list[str] = []
    for key in sorted(s.sessions):
        marker = "*" if key == s.current_session else " "
        unread = s.unread.get(key, 0)
        badge = f" ({unread})" if unread else ""
        parts.append(f"{marker}{key}{badge}")
    middle = "  ".join(parts) if parts else "(no sessions yet — /join <group>)"
    return f"[{s.current_user}]  {middle}  |  /help"


def _render_messages(s: ChatState) -> str:
    if s.current_session is None:
        return "Use /switch group:<name> or /switch user:<name> to open a conversation."
    lines: list[str] = []
    for m in s.sessions.get(s.current_session, []):
        kind = m.get("type")
        if kind == MsgType.RECV_MSG:
            ts_ms = m.get("ts", 0)
            t = time.strftime("%H:%M:%S", time.localtime(ts_ms / 1000))
            sender = m.get("from", "?")
            if m.get("content_type") == ContentType.IMAGE:
                body = f"[image: {len(m.get('content', ''))} bytes base64]"
            else:
                body = m.get("content", "")
            lines.append(f"[{t}] <{sender}> {body}")
        elif kind == "__system__":
            lines.append(m.get("content", ""))
        elif kind == "__error__":
            lines.append(f"! {m.get('content', '')}")
    return "\n".join(lines)


# ----------------------------------------------------------------- the client


class ChatClient:
    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self._host = host
        self._port = port
        self.state = ChatState()
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.app: Optional[Application] = None
        self._input = Buffer(multiline=False, accept_handler=self._on_enter)
        self._recv_task: Optional[asyncio.Task] = None

    # ---------- UI plumbing

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def _system(self, text: str) -> None:
        """Append a system-level notice to the current session (or creates
        one if none yet)."""
        msg = {"type": "__system__", "content": text}
        key = self.state.current_session or "system"
        self.state.sessions.setdefault(key, []).append(msg)
        if self.state.current_session is None:
            self.state.current_session = key
        self._invalidate()

    def _error_line(self, text: str) -> None:
        msg = {"type": "__error__", "content": text}
        key = self.state.current_session or "system"
        self.state.sessions.setdefault(key, []).append(msg)
        if self.state.current_session is None:
            self.state.current_session = key
        self._invalidate()

    # ---------- input path

    def _on_enter(self, buf: Buffer) -> bool:
        line = buf.text.strip()
        buf.reset()
        if not line:
            return False
        # Accept_handler runs in the UI thread; route command work to a
        # task so we never block the event loop.
        asyncio.get_event_loop().create_task(self._dispatch_command(line))
        return False  # keep buffer alive

    async def _dispatch_command(self, line: str) -> None:
        try:
            if line.startswith("/"):
                await self._run_command(line)
            else:
                # Default: send text to current session.
                await self._cmd_send(line, ContentType.TEXT)
        except Exception as exc:  # noqa: BLE001
            logger.exception("command failed: %s", line)
            self._error_line(f"command failed: {exc}")

    async def _run_command(self, line: str) -> None:
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        table: dict[str, Callable] = {
            "/register":    lambda: self._cmd_auth(MsgType.REGISTER, args),
            "/login":       lambda: self._cmd_auth(MsgType.LOGIN, args),
            "/logout":      lambda: self._send({"type": MsgType.LOGOUT}),
            "/create":      lambda: self._cmd_group(MsgType.CREATE_GROUP, args),
            "/join":        lambda: self._cmd_group(MsgType.JOIN_GROUP, args),
            "/leave":       lambda: self._cmd_group(MsgType.LEAVE_GROUP, args),
            "/list":        lambda: self._send({"type": MsgType.LIST_GROUPS}),
            "/switch":      lambda: self._cmd_switch(args),
            "/msg":         lambda: self._cmd_send(" ".join(args), ContentType.TEXT),
            "/img":         lambda: self._cmd_send(" ".join(args), ContentType.IMAGE),
            "/help":        lambda: self._cmd_help(),
            "/quit":        lambda: self._cmd_quit(),
        }

        if cmd not in table:
            self._error_line(f"unknown command: {cmd}  (try /help)")
            return

        await table[cmd]()

    # ---------- command implementations

    async def _cmd_auth(self, msg_type: str, args: list[str]) -> None:
        if len(args) < 2:
            self._error_line(f"usage: {msg_type} <username> <password>")
            return
        await self._send({
            "type": msg_type,
            "username": args[0],
            "password": args[1],
        })

    async def _cmd_group(self, msg_type: str, args: list[str]) -> None:
        if not args:
            self._error_line(f"usage: {msg_type} <group_name>")
            return
        await self._send({"type": msg_type, "group_name": args[0]})

    async def _cmd_switch(self, args: list[str]) -> None:
        if not args or ":" not in args[0]:
            self._error_line("usage: /switch group:<name>  or  /switch user:<name>")
            return
        key = args[0]
        kind, _, _ = key.partition(":")
        if kind not in ("group", "user"):
            self._error_line("session key must start with group: or user:")
            return
        self.state.switch(key)
        self._system(f"switched to {key}")

    async def _cmd_send(self, text: str, content_type: str) -> None:
        if not text:
            self._error_line("nothing to send")
            return
        if self.state.current_session is None or ":" not in self.state.current_session:
            self._error_line("open a session first with /switch or /join")
            return
        kind, _, name = self.state.current_session.partition(":")
        await self._send({
            "type": MsgType.SEND_MSG,
            "target_type": TargetType.GROUP if kind == "group" else TargetType.USER,
            "target": name,
            "content_type": content_type,
            "content": text,
        })

    async def _cmd_help(self) -> None:
        self._system(
            "commands:\n"
            "  /register <u> <p>  /login <u> <p>  /logout  /quit\n"
            "  /create <g>  /join <g>  /leave <g>  /list\n"
            "  /switch group:<g>  /switch user:<u>\n"
            "  /msg <text>  /img <base64>\n"
            "  (plain text is shorthand for /msg)"
        )

    async def _cmd_quit(self) -> None:
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass
        if self.app is not None:
            self.app.exit()

    # ---------- network

    async def _send(self, frame: dict) -> None:
        if self.writer is None:
            self._error_line("not connected")
            return
        ok = await write_frame(self.writer, frame)
        if not ok:
            self._error_line("send failed (connection broken?)")

    async def _receive_loop(self) -> None:
        assert self.reader is not None
        while True:
            frame = await read_frame(self.reader)
            if frame is None:
                self._system("connection closed by server")
                return
            self._handle_incoming(frame)

    def _handle_incoming(self, frame: dict) -> None:
        kind = frame.get("type")
        logger.info("recv %s", kind)

        if kind == MsgType.RECV_MSG:
            # Decide which local session to put this message in.
            if frame.get("target_type") == TargetType.GROUP:
                key = f"group:{frame.get('target')}"
            else:
                # DM: from me → key is the peer; else key is the sender.
                peer = frame.get("from")
                if peer == self.state.current_user:
                    peer = frame.get("target")
                key = f"user:{peer}"
            self.state.add_message(key, frame)
            if self.state.current_session is None:
                self.state.switch(key)

        elif kind == MsgType.USER_STATUS:
            who = frame.get("username")
            group = frame.get("group_name", "")
            status = frame.get("status")
            self._system(f"{who} {status} {group}".rstrip())

        elif kind == MsgType.LOGIN_RESP:
            if frame.get("ok"):
                self.state.current_user = frame.get("username")
                self._system(f"logged in as {self.state.current_user}")
                # Pull the group list so the sessions bar is populated.
                asyncio.get_event_loop().create_task(
                    self._send({"type": MsgType.LIST_GROUPS})
                )
            else:
                self._error_line(f"login failed: {frame.get('msg')}")

        elif kind == MsgType.REGISTER_RESP:
            if frame.get("ok"):
                self._system("registered. now /login.")
            else:
                self._error_line(f"register failed: {frame.get('msg')}")

        elif kind == MsgType.CREATE_GROUP_RESP:
            if frame.get("ok"):
                g = frame.get("group_name")
                self.state.switch(f"group:{g}")
                self._system(f"group '{g}' created")
            else:
                self._error_line(f"create failed: {frame.get('msg')}")

        elif kind == MsgType.JOIN_GROUP_RESP:
            if frame.get("ok"):
                g = frame.get("group_name")
                self.state.switch(f"group:{g}")
                self._system(f"joined '{g}'")
            else:
                self._error_line(f"join failed: {frame.get('msg')}")

        elif kind == MsgType.LEAVE_GROUP_RESP:
            if frame.get("ok"):
                self._system(f"left '{frame.get('group_name')}'")
            else:
                self._error_line(f"leave failed: {frame.get('msg')}")

        elif kind == MsgType.LIST_GROUPS_RESP:
            groups = frame.get("groups", [])
            for g in groups:
                key = f"group:{g['group_name']}"
                self.state.sessions.setdefault(key, [])
            names = ", ".join(g["group_name"] for g in groups) or "(none)"
            self._system(f"groups: {names}")

        elif kind == MsgType.LOGOUT_RESP:
            self.state.current_user = None
            self._system("logged out")

        elif kind == MsgType.SEND_MSG_ACK:
            if not frame.get("ok"):
                self._error_line(f"send failed: {frame.get('msg')}")
            # Successful ack: silent — the echoed recv_msg renders the line.

        elif kind == MsgType.ERROR:
            self._error_line(f"error {frame.get('code')}: {frame.get('msg')}")

        self._invalidate()

    # ---------- lifecycle

    def _build_app(self) -> Application:
        sessions_bar = Window(
            content=FormattedTextControl(
                text=lambda: _render_sessions_bar(self.state)
            ),
            height=1,
        )
        message_area = Window(
            content=FormattedTextControl(
                text=lambda: _render_messages(self.state)
            ),
            wrap_lines=True,
        )
        input_window = Window(
            content=BufferControl(buffer=self._input),
            height=1,
        )
        root = HSplit([
            sessions_bar,
            Window(height=1, char="─"),
            message_area,
            Window(height=1, char="─"),
            input_window,
        ])
        layout = Layout(root)
        layout.focus(self._input)

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-q")
        def _(event):  # noqa: ANN001
            event.app.exit()

        return Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
        )

    async def run(self) -> None:
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self._host, self._port
            )
        except OSError as exc:
            print(f"connect failed: {exc}")
            return

        self.app = self._build_app()
        self._system(f"connected to {self._host}:{self._port}")
        self._recv_task = asyncio.create_task(self._receive_loop())

        try:
            await self.app.run_async()
        finally:
            if self._recv_task is not None:
                self._recv_task.cancel()
            if self.writer is not None:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass


def main() -> None:
    try:
        asyncio.run(ChatClient().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
