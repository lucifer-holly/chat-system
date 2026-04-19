.PHONY: help install server client test stress malformed clean

help:
	@echo "chat-system-ai-coding  -  Makefile targets"
	@echo ""
	@echo "  make install      Install Python dependencies"
	@echo "  make server       Start the chat server (foreground)"
	@echo "  make client       Start a TUI client"
	@echo "  make test         Run protocol functional test"
	@echo "  make stress       Run 50-client stress test"
	@echo "  make malformed    Run protocol fuzz/malformed test"
	@echo "  make clean        Remove runtime artifacts (db, logs, pycache)"

install:
	pip install -r requirements.txt

server:
	python -m src.server

client:
	python -m src.client

test:
	python -m tests.test_protocol

stress:
	python -m tests.test_stress

malformed:
	python -m tests.test_malformed

clean:
	rm -f chat.db chat.db-journal server.log client.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
