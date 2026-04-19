"""
Single-connection protocol smoke test.

Exercises every request/response pair the client can issue, in a
plausible order, and prints the wire traffic for human review.

Run:
    python -m tests.test_protocol
"""

from __future__ import annotations

import asyncio
import json

from src.codec import read_frame, write_frame
from src.protocol_types import ContentType, MsgType, TargetType


HOST = "127.0.0.1"
PORT = 9999


async def _rpc(reader, writer, frame: dict) -> dict | None:
    print(f"  >> {json.dumps(frame, ensure_ascii=False)}")
    await write_frame(writer, frame)
    resp = await read_frame(reader)
    print(f"  << {resp}")
    return resp


async def main() -> None:
    print("=" * 60)
    print(" protocol smoke test")
    print("=" * 60)

    reader, writer = await asyncio.open_connection(HOST, PORT)

    try:
        print("\n[1] register alice")
        await _rpc(reader, writer, {
            "type": MsgType.REGISTER,
            "username": "alice", "password": "pwd123",
        })

        print("\n[2] login alice")
        await _rpc(reader, writer, {
            "type": MsgType.LOGIN,
            "username": "alice", "password": "pwd123",
        })

        print("\n[3] create group 'dev'")
        await _rpc(reader, writer, {
            "type": MsgType.CREATE_GROUP,
            "group_name": "dev",
        })

        print("\n[4] list groups")
        await _rpc(reader, writer, {"type": MsgType.LIST_GROUPS})

        print("\n[5] send group message")
        # Because we are the only member of 'dev', the server broadcasts
        # to zero peers and echoes the recv_msg back to us.  So we get
        # TWO frames: the echoed recv_msg and the ack.  Read both.
        await write_frame(writer, {
            "type": MsgType.SEND_MSG,
            "target_type": TargetType.GROUP,
            "target": "dev",
            "content_type": ContentType.TEXT,
            "content": "hello world",
        })
        echo = await read_frame(reader)
        print(f"  << (recv echo) {echo}")
        ack = await read_frame(reader)
        print(f"  << (ack)        {ack}")

        print("\n[6] heartbeat")
        await _rpc(reader, writer, {"type": MsgType.HEARTBEAT})

        print("\n[7] leave group 'dev'")
        await _rpc(reader, writer, {
            "type": MsgType.LEAVE_GROUP,
            "group_name": "dev",
        })

        print("\n[8] logout")
        await _rpc(reader, writer, {"type": MsgType.LOGOUT})

    finally:
        writer.close()
        await writer.wait_closed()

    print("\ndone.")


if __name__ == "__main__":
    asyncio.run(main())
