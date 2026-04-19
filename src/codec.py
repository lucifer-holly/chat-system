"""
Framing codec: length-prefix + UTF-8 JSON over TCP.

Frame format (wire):

    +--------------------+----------------------+
    | 4 bytes, BE uint32 |  payload (N bytes)   |
    |   payload length N |    UTF-8 JSON object |
    +--------------------+----------------------+

Design notes
------------
* A fixed 4-byte length prefix solves TCP's "stream of bytes, no message
  boundary" problem without any delimiter-escaping gymnastics.
* Reading is done via `StreamReader.readexactly`, which blocks until the
  full buffer is available or EOF is hit.  Both outcomes are handled
  explicitly: EOF returns None (graceful close), malformed payloads
  return None (caller decides whether to close).
* A hard 10 MB per-frame cap stops a malicious or buggy peer from
  allocating arbitrary memory before we've seen a byte of payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_FRAME_BYTES = 10 * 1024 * 1024  # 10 MB — matches the design document.
_HEADER_STRUCT = struct.Struct(">I")  # big-endian uint32


async def read_frame(reader: asyncio.StreamReader) -> Optional[dict[str, Any]]:
    """Read one frame.  Returns None on graceful EOF or any protocol error.

    The caller (the connection loop) is responsible for deciding what to do
    with a None return.  Typically it closes the connection; for the fuzz
    tests it can also just skip the frame.
    """
    try:
        header = await reader.readexactly(_HEADER_STRUCT.size)
    except asyncio.IncompleteReadError:
        # Peer closed the connection cleanly between frames.
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_frame: header read failed: %s", exc)
        return None

    (length,) = _HEADER_STRUCT.unpack(header)

    if length == 0:
        logger.warning("read_frame: received zero-length frame")
        return None
    if length > MAX_FRAME_BYTES:
        logger.error(
            "read_frame: frame length %d exceeds cap %d, dropping connection",
            length,
            MAX_FRAME_BYTES,
        )
        return None

    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        logger.warning("read_frame: peer closed mid-payload")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_frame: payload read failed: %s", exc)
        return None

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.warning("read_frame: invalid UTF-8: %s", exc)
        return None

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("read_frame: invalid JSON: %s", exc)
        return None

    if not isinstance(obj, dict):
        # Protocol-level contract: every frame body must be a JSON object.
        logger.warning("read_frame: top-level payload is not an object: %r", obj)
        return None

    return obj


async def write_frame(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> bool:
    """Serialize `obj` as JSON and write it as a single frame.

    Returns True on success, False on any failure (connection broken,
    payload too large, etc.).  The caller generally does not need to care
    — this function never raises.
    """
    try:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        logger.error("write_frame: cannot serialise %r: %s", obj, exc)
        return False

    if len(payload) > MAX_FRAME_BYTES:
        logger.error("write_frame: payload %d bytes exceeds cap", len(payload))
        return False

    frame = _HEADER_STRUCT.pack(len(payload)) + payload
    try:
        writer.write(frame)
        await writer.drain()
    except (ConnectionError, OSError) as exc:
        logger.warning("write_frame: connection error: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("write_frame: unexpected error: %s", exc)
        return False

    return True
