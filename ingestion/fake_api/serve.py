"""Run the fake API in a background thread, on a free port on 127.0.0.1.

    with serve(data_dir) as api_url:
        ...   # http://127.0.0.1:<port>

Used by the pipeline tests and by jobs/daily_load.py. The Databricks job has no API to call, so it starts
its own. One shared helper means the tests exercise the same startup the job uses.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from fake_api.app import create_app


@contextmanager
def serve(data_dir: Path, day: int = 1, startup_timeout: float = 30) -> Iterator[str]:
    """Yields the base URL. The server stops when the block ends, also when the block raises."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))  # port 0 = let the OS pick a free port
        port = s.getsockname()[1]
    config = uvicorn.Config(create_app(data_dir, day=day), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + startup_timeout
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError(f"The fake API didn't start on 127.0.0.1:{port}")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
