#!/usr/bin/env bash
# Start the chat server in the background, redirecting logs to server.log.
# Kills any prior server instance first to avoid port conflicts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$REPO_ROOT"

echo "[run.sh] Stopping any running server..."
pkill -f "src.server" 2>/dev/null || true
sleep 1

echo "[run.sh] Starting server..."
nohup python -m src.server > server.log 2>&1 &
SERVER_PID=$!
sleep 2

if ps -p "$SERVER_PID" > /dev/null; then
    echo "[run.sh] Server started. PID=$SERVER_PID"
    echo ""
    echo "=========================================="
    echo " Chat server is running on 127.0.0.1:9999"
    echo "=========================================="
    echo ""
    echo " Open another terminal and try:"
    echo "   python -m src.client              # start a TUI client"
    echo "   python -m tests.test_protocol     # functional test"
    echo "   python -m tests.test_stress       # stress test"
    echo "   python -m tests.test_malformed    # protocol fuzz test"
    echo "   tail -f server.log                # watch server log"
    echo ""
else
    echo "[run.sh] Server failed to start. Last 20 log lines:"
    tail -20 server.log
    exit 1
fi
