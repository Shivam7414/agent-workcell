"""Run the answer server (and other ASGI apps) on a free local port for end-to-end tests."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from agent_workcell.config import Settings
from agent_workcell.runtime import FakeRuntime
from agent_workcell.server import build_service, create_app
from reference_erp import seed
from tests.security.conftest import NAMES, POLICY_PATH

KNOWLEDGE = Path(__file__).parents[2] / "reference_erp" / "knowledge"
HOST_KEY = "host-key-for-tests-only-0123456789"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def serving(app, port: int) -> Iterator[str]:
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    while not running.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        thread.join(timeout=10)


@contextmanager
def answer_server(guard_db, data_dir: Path) -> Iterator[SimpleNamespace]:
    """The answer server with the fake runtime, on the guarded test database."""
    port = free_port()
    settings = Settings(policy=POLICY_PATH, knowledge=KNOWLEDGE, agent_url=guard_db.agent_url,
                        binder_url=guard_db.binder_url, host_key=HOST_KEY, data_dir=data_dir, port=port,
                        runtime="fake", answer_timeout_s=30, today=seed.ANCHOR_DATE)
    service = build_service(settings, FakeRuntime([]), names=NAMES)
    try:
        with serving(create_app(service), port) as url:
            yield SimpleNamespace(service=service, url=url, settings=settings)
    finally:
        service.audit.close()
        service.memory.close()
        service.files.close()
