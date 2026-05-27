"""Tests for the local Azure OpenAI proxy.

Sample usage:
    python3.10 -m pytest tests/test_aoai_proxy.py

These tests start local HTTP servers only. They verify that the proxy keeps
Azure SDK request shape intact while hiding the client-provided dummy key from
the upstream service.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT_DIR / "tools" / "aoai_proxy.py"
REQUEST_TIMEOUT_SECONDS = 5
UPSTREAM_API_KEY = "real-upstream-key"
DUMMY_API_KEY = "dummy-client-key"


def load_proxy_module():
    spec = importlib.util.spec_from_file_location("aoai_proxy", PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ServerHandle:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread):
        self.server = server
        self.thread = thread

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=REQUEST_TIMEOUT_SECONDS)


def start_server(handler_class: type[BaseHTTPRequestHandler]) -> ServerHandle:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return ServerHandle(server, thread)


def test_healthz_works_without_contacting_upstream():
    proxy = load_proxy_module()
    handler = proxy.make_proxy_handler(
        proxy.ProxyConfig(
            upstream_endpoint="http://127.0.0.1:9",
            upstream_api_key=UPSTREAM_API_KEY,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )
    proxy_server = start_server(handler)

    try:
        with urllib.request.urlopen(
            f"{proxy_server.url}/healthz", timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            assert response.status == 200
            assert response.read() == b"ok\n"
    finally:
        proxy_server.close()


def test_post_request_path_query_body_and_key_are_forwarded_correctly():
    captured_requests = []

    class FakeUpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            captured_requests.append(
                {
                    "path": self.path,
                    "body": body,
                    "api_key": self.headers.get("api-key"),
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"chatcmpl-test","choices":[]}')

        def log_message(self, format, *args):
            return

    upstream_server = start_server(FakeUpstreamHandler)
    proxy = load_proxy_module()
    handler = proxy.make_proxy_handler(
        proxy.ProxyConfig(
            upstream_endpoint=upstream_server.url,
            upstream_api_key=UPSTREAM_API_KEY,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )
    proxy_server = start_server(handler)
    request_body = json.dumps({"model": "gpt-5.2", "messages": [{"role": "user"}]}).encode()
    request = urllib.request.Request(
        f"{proxy_server.url}/openai/deployments/gpt-5.2/chat/completions?api-version=2024-12-01-preview",
        data=request_body,
        method="POST",
        headers={
            "api-key": DUMMY_API_KEY,
            "Authorization": f"Bearer {DUMMY_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert response.status == 200
            assert json.loads(response.read())["id"] == "chatcmpl-test"

        assert len(captured_requests) == 1
        captured = captured_requests[0]
        assert captured["path"] == (
            "/openai/deployments/gpt-5.2/chat/completions"
            "?api-version=2024-12-01-preview"
        )
        assert captured["body"] == request_body
        assert captured["api_key"] == UPSTREAM_API_KEY
        assert captured["authorization"] is None
        assert captured["content_type"] == "application/json"
    finally:
        proxy_server.close()
        upstream_server.close()


def test_openai_v1_chat_completion_path_uses_real_azure_key():
    captured_requests = []

    class FakeUpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            captured_requests.append(
                {
                    "path": self.path,
                    "body": body,
                    "api_key": self.headers.get("api-key"),
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"chatcmpl-v1-test","choices":[]}')

        def log_message(self, format, *args):
            return

    upstream_server = start_server(FakeUpstreamHandler)
    proxy = load_proxy_module()
    handler = proxy.make_proxy_handler(
        proxy.ProxyConfig(
            upstream_endpoint=upstream_server.url,
            upstream_api_key=UPSTREAM_API_KEY,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )
    proxy_server = start_server(handler)
    request_body = json.dumps({"model": "gpt-5", "messages": [{"role": "user"}]}).encode()
    request = urllib.request.Request(
        f"{proxy_server.url}/openai/v1/chat/completions",
        data=request_body,
        method="POST",
        headers={
            "Authorization": f"Bearer {DUMMY_API_KEY}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            assert response.status == 200
            assert json.loads(response.read())["id"] == "chatcmpl-v1-test"

        assert len(captured_requests) == 1
        captured = captured_requests[0]
        assert captured["path"] == "/openai/v1/chat/completions"
        assert captured["body"] == request_body
        assert captured["api_key"] == UPSTREAM_API_KEY
        assert captured["authorization"] is None
        assert captured["content_type"] == "application/json"
    finally:
        proxy_server.close()
        upstream_server.close()


def test_upstream_http_errors_are_relayed_without_body_logging():
    class ErrorUpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')

        def log_message(self, format, *args):
            return

    upstream_server = start_server(ErrorUpstreamHandler)
    proxy = load_proxy_module()
    handler = proxy.make_proxy_handler(
        proxy.ProxyConfig(
            upstream_endpoint=upstream_server.url,
            upstream_api_key=UPSTREAM_API_KEY,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )
    proxy_server = start_server(handler)

    try:
        with urllib.request.urlopen(
            f"{proxy_server.url}/openai/models", timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            raise AssertionError(f"expected HTTPError, got {response.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code == 429
        assert json.loads(exc.read())["error"] == "rate limited"
    finally:
        proxy_server.close()
        upstream_server.close()


def test_upstream_network_errors_include_safe_reason(monkeypatch):
    proxy = load_proxy_module()
    original_urlopen = urllib.request.urlopen

    def fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("diagnostic reason")

    monkeypatch.setattr(proxy.urllib.request, "urlopen", fail_urlopen)
    handler = proxy.make_proxy_handler(
        proxy.ProxyConfig(
            upstream_endpoint="https://example.invalid",
            upstream_api_key=UPSTREAM_API_KEY,
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
    )
    proxy_server = start_server(handler)

    try:
        with original_urlopen(
            f"{proxy_server.url}/openai/models", timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            raise AssertionError(f"expected HTTPError, got {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        assert exc.code == 502
        assert "URLError" in body
        assert "diagnostic reason" in body
        assert UPSTREAM_API_KEY not in body
    finally:
        proxy_server.close()
