"""Call the fake API in-process, without any network connection.

    with in_process(data_dir, regnskap.SESSION) as api_url:
        ...   # every request to api_url through that session goes straight to the FastAPI app

Why: serverless compute in Databricks blocks connections to 127.0.0.1. Even a plain socket gets
"Connection refused" (STEG5 section 6.4), so the job can't run the API as a local HTTP server the way the
tests do (fake_api/serve.py). Instead, a requests transport adapter is mounted on the session: requests to
API_URL are handed to the app through Starlette's TestClient, in the same process. dlt, the clock and the
comparison use the same session and URL as always, and don't know the difference.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from starlette.testclient import TestClient

from fake_api.app import create_app

# Never resolved: the adapter answers before any DNS lookup or socket. ".invalid" can't exist (RFC 2606).
API_URL = "http://fake-erp.invalid"


class ASGIAdapter(BaseAdapter):
    """A requests transport that sends each request to an ASGI app instead of over the network."""

    def __init__(self, client: TestClient) -> None:
        super().__init__()
        self.client = client

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None) -> requests.Response:
        r = self.client.request(request.method, request.url, content=request.body, headers=dict(request.headers))
        response = requests.Response()
        response.status_code = r.status_code
        response.reason = r.reason_phrase
        response.headers = CaseInsensitiveDict(r.headers)
        response._content = r.content
        response.encoding = r.encoding
        response.url = request.url
        response.request = request
        return response

    def close(self) -> None:
        pass  # the TestClient is closed by in_process()

    def __deepcopy__(self, memo) -> ASGIAdapter:
        # dlt deep-copies its REST client config, session included (rest_api _validate_config). Copying the
        # TestClient (threads, an event loop) recurses forever, and a copy must reach the same app anyway.
        return self


@contextmanager
def in_process(data_dir: Path, session: requests.Session, day: int = 1) -> Iterator[str]:
    """Mount the app on `session` for API_URL and yield API_URL. Unmounted again when the block ends."""
    with TestClient(create_app(data_dir, day=day)) as client:
        session.mount(API_URL, ASGIAdapter(client))
        try:
            yield API_URL
        finally:
            session.adapters.pop(API_URL, None)
