"""
Protocol fuzz / malformed-frame test.

Sends several kinds of bad data on one connection and verifies that:

  1. the server does not crash,
  2. well-formed traffic on OTHER connections continues to work.

Because the server is supposed to drop a connection the moment it sees a
malformed frame, most of the "bad" sub-tests end with the server closing
our socket.  That's the correct behaviour.

Run:
    python -m tests.test_malformed
"""

from __future__ import annotations

import asyncio
import struct

from src.codec import read_frame, write_frame
from src.protocol_types import MsgType

HOST = "127.0.0.1"
PORT = 9999


async def _send_raw(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)
    await writer.drain()


async def _step(label: str, coro) -> bool:
    print(f"  [{label}]  ", end="", flush=True)
    try:
        await coro
        print("PASS")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        return False


# ------------------------------------------------------------- sub-tests


async def test_invalid_utf8() -> None:
    """Length-prefix is fine, payload is bytes that cannot be decoded."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        payload = b"\xff\xfe\xfd\xfc"
        await _send_raw(writer, struct.pack(">I", len(payload)) + payload)
        # Server should close the connection; reader returns empty bytes.
        resp = await asyncio.wait_for(reader.read(1), timeout=2.0)
        assert resp == b"", "expected EOF after bad frame"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_non_json() -> None:
    """Valid UTF-8 but not JSON."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        payload = b"this is not json"
        await _send_raw(writer, struct.pack(">I", len(payload)) + payload)
        resp = await asyncio.wait_for(reader.read(1), timeout=2.0)
        assert resp == b"", "expected EOF after bad frame"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_oversized_length() -> None:
    """Length prefix claims 20 MB — should trigger the cap and close."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        # We only send the header; server should give up and close.
        await _send_raw(writer, struct.pack(">I", 20 * 1024 * 1024))
        resp = await asyncio.wait_for(reader.read(1), timeout=2.0)
        assert resp == b"", "expected EOF after oversized length"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_zero_length() -> None:
    """Length prefix of 0 should also be rejected."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        await _send_raw(writer, struct.pack(">I", 0))
        resp = await asyncio.wait_for(reader.read(1), timeout=2.0)
        assert resp == b"", "expected EOF after zero-length"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_json_but_not_object() -> None:
    """Valid JSON, but a top-level array — protocol says frames must be objects."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        payload = b"[1, 2, 3]"
        await _send_raw(writer, struct.pack(">I", len(payload)) + payload)
        resp = await asyncio.wait_for(reader.read(1), timeout=2.0)
        assert resp == b"", "expected EOF after non-object top-level"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_heartbeat_still_works() -> None:
    """A well-formed connection after all the malformed ones must still work."""
    reader, writer = await asyncio.open_connection(HOST, PORT)
    try:
        await write_frame(writer, {"type": MsgType.HEARTBEAT})
        resp = await asyncio.wait_for(read_frame(reader), timeout=2.0)
        assert resp is not None, "no response"
        assert resp.get("type") == MsgType.HEARTBEAT_RESP, f"wrong type: {resp}"
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------- driver


async def main() -> None:
    print("=" * 60)
    print(" protocol fuzz / malformed-frame test")
    print("=" * 60)

    results = [
        await _step("A  invalid utf-8        ", test_invalid_utf8()),
        await _step("B  non-json payload     ", test_non_json()),
        await _step("C  oversized length     ", test_oversized_length()),
        await _step("D  zero length          ", test_zero_length()),
        await _step("E  valid json, non-obj  ", test_json_but_not_object()),
        await _step("F  normal conn survives ", test_heartbeat_still_works()),
    ]

    print("-" * 60)
    if all(results):
        print(" RESULT: server survived all fuzzing attempts  ->  PASS")
    else:
        print(" RESULT: some sub-tests failed  ->  FAIL")


if __name__ == "__main__":
    asyncio.run(main())
