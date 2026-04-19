"""
Stress test: 50 concurrent clients, end-to-end latency and idle-CPU
measurement.

What it does
------------
1. Opens 50 TCP connections in parallel.
2. Registers users user0001..user0050 (idempotent — already-existing
   users are ignored).
3. Logs all 50 in.
4. user0001 creates group `stress_group`; everyone else joins.
5. user0001 sends 100 group messages, each carrying
   `STRESS|<ts_ms>|<seq>` in its body.  Every receiver records its
   local time-of-arrival for each message.
6. Compute p50 / p95 / p99 / max across all receivers × messages.
7. Sit idle for 30 s while sampling the server process's CPU usage
   once per second with psutil.  Average the samples.
8. Log everyone out, disconnect, print the report.

Acceptance targets (from the interview spec):
    concurrent connections   >= 50
    e2e latency              <= 500 ms
    idle CPU (50 connections) <= 5 %
"""

from __future__ import annotations

import asyncio
import statistics
import time
from typing import Optional

import psutil

from src.codec import read_frame, write_frame
from src.protocol_types import ContentType, MsgType, TargetType

HOST = "127.0.0.1"
PORT = 9999
N_CLIENTS = 50
N_MESSAGES = 100
MSG_INTERVAL_SEC = 0.05
IDLE_SAMPLE_SEC = 30
GROUP_NAME = "stress_group"
MSG_PREFIX = "STRESS"


# ---------------------------------------------------------- client harness


class StressClient:
    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.username = f"user{idx:04d}"
        self.password = "pwd"
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.latencies_ms: list[float] = []
        self._stop = False

    async def connect(self) -> bool:
        try:
            self.reader, self.writer = await asyncio.open_connection(HOST, PORT)
            return True
        except OSError as exc:
            print(f"  client #{self.idx} connect failed: {exc}")
            return False

    async def register(self) -> None:
        await write_frame(self.writer, {
            "type": MsgType.REGISTER,
            "username": self.username, "password": self.password,
        })
        await read_frame(self.reader)  # ignore result — may be "user_exists"

    async def login(self) -> bool:
        await write_frame(self.writer, {
            "type": MsgType.LOGIN,
            "username": self.username, "password": self.password,
        })
        resp = await read_frame(self.reader)
        return bool(resp and resp.get("ok"))

    async def join_group(self) -> None:
        await write_frame(self.writer, {
            "type": MsgType.JOIN_GROUP, "group_name": GROUP_NAME,
        })
        await read_frame(self.reader)  # ignore result

    async def create_group(self) -> None:
        await write_frame(self.writer, {
            "type": MsgType.CREATE_GROUP, "group_name": GROUP_NAME,
        })
        await read_frame(self.reader)  # may already exist; that's fine

    async def send_burst(self, n: int, interval: float) -> None:
        for seq in range(n):
            now_ms = int(time.time() * 1000)
            body = f"{MSG_PREFIX}|{now_ms}|{seq}"
            await write_frame(self.writer, {
                "type": MsgType.SEND_MSG,
                "target_type": TargetType.GROUP,
                "target": GROUP_NAME,
                "content_type": ContentType.TEXT,
                "content": body,
            })
            await asyncio.sleep(interval)

    async def receive_loop(self) -> None:
        """Record latency for every STRESS message we see."""
        while not self._stop:
            try:
                frame = await read_frame(self.reader)
            except Exception:  # noqa: BLE001
                return
            if frame is None:
                return
            if frame.get("type") != MsgType.RECV_MSG:
                continue
            content = frame.get("content", "")
            if not content.startswith(f"{MSG_PREFIX}|"):
                continue
            try:
                _, ts_str, _seq = content.split("|", 2)
                sent_ms = int(ts_str)
            except ValueError:
                continue
            recv_ms = int(time.time() * 1000)
            self.latencies_ms.append(recv_ms - sent_ms)

    async def logout_and_close(self) -> None:
        self._stop = True
        try:
            await write_frame(self.writer, {"type": MsgType.LOGOUT})
        except Exception:  # noqa: BLE001
            pass
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------- cpu sampler


def find_server_process() -> Optional[psutil.Process]:
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = proc.info.get("cmdline") or []
            if any("src.server" in str(c) or "server.py" in str(c) for c in cmd):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


async def sample_cpu_avg(proc: psutil.Process, seconds: int) -> float:
    # The first call primes the baseline and always returns 0.0.
    proc.cpu_percent(interval=None)
    samples: list[float] = []
    for _ in range(seconds):
        await asyncio.sleep(1.0)
        try:
            samples.append(proc.cpu_percent(interval=None))
        except psutil.NoSuchProcess:
            break
    return statistics.fmean(samples) if samples else 0.0


# ------------------------------------------------------------------- driver


def _percentiles(data: list[float]) -> tuple[float, float, float, float]:
    if not data:
        return 0.0, 0.0, 0.0, 0.0
    s = sorted(data)
    n = len(s)
    return (
        s[int(n * 0.50)],
        s[int(n * 0.95)],
        s[min(int(n * 0.99), n - 1)],
        s[-1],
    )


async def main() -> None:
    print(f"stress test: {N_CLIENTS} clients, {N_MESSAGES} messages")
    print("-" * 60)

    clients = [StressClient(i + 1) for i in range(N_CLIENTS)]
    connected = await asyncio.gather(*(c.connect() for c in clients))
    ok_count = sum(1 for x in connected if x)
    print(f"[connect]  {ok_count}/{N_CLIENTS}")

    # Registration and login sequentially per client (but interleaved
    # across clients via gather).
    await asyncio.gather(*(c.register() for c in clients))
    logins = await asyncio.gather(*(c.login() for c in clients))
    print(f"[login]    {sum(logins)}/{N_CLIENTS}")

    sender = clients[0]
    await sender.create_group()
    await asyncio.gather(*(c.join_group() for c in clients[1:]))

    # Background receive loops for everyone.
    recv_tasks = [asyncio.create_task(c.receive_loop()) for c in clients]

    # Give the join events a moment to settle.
    await asyncio.sleep(1.0)

    print(f"[send]     {N_MESSAGES} messages from {sender.username}")
    await sender.send_burst(N_MESSAGES, MSG_INTERVAL_SEC)

    # Let the last messages drain.
    await asyncio.sleep(1.0)

    all_lat: list[float] = []
    for c in clients:
        all_lat.extend(c.latencies_ms)
    p50, p95, p99, mx = _percentiles(all_lat)
    print(
        f"[latency]  samples={len(all_lat)}  "
        f"p50={p50:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms  max={mx:.0f}ms"
    )

    # Idle CPU measurement.
    proc = find_server_process()
    if proc is None:
        print("[cpu]      server process not found (skipped)")
        avg_cpu = -1.0
    else:
        print(f"[cpu]      sampling for {IDLE_SAMPLE_SEC}s...")
        avg_cpu = await sample_cpu_avg(proc, IDLE_SAMPLE_SEC)
        print(f"[cpu]      average={avg_cpu:.2f}%")

    # Teardown.
    await asyncio.gather(*(c.logout_and_close() for c in clients),
                         return_exceptions=True)
    for t in recv_tasks:
        t.cancel()

    # Report.
    print("\n" + "=" * 60)
    print(" STRESS TEST REPORT")
    print("=" * 60)
    report = [
        ("concurrent connections", f"{ok_count}/{N_CLIENTS}",   ok_count >= N_CLIENTS),
        ("latency p50",            f"{p50:.0f} ms  (<=500)",    p50 <= 500),
        ("latency p95",            f"{p95:.0f} ms  (<=500)",    p95 <= 500),
        ("latency p99",            f"{p99:.0f} ms  (<=500)",    p99 <= 500),
        ("latency max",            f"{mx:.0f} ms  (<=500)",     mx  <= 500),
        ("idle cpu",               f"{avg_cpu:.2f}%  (<=5%)",  0 <= avg_cpu <= 5.0),
    ]
    for label, value, ok in report:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}]  {label:26s}  {value}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
