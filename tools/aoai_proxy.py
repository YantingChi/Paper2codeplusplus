#!/usr/bin/env python3
"""Run a local Azure OpenAI proxy that hides the upstream API key.

Sample usage:
    sudo install -o root -g root -m 600 /dev/null /etc/aoai-proxy.env
    sudoedit /etc/aoai-proxy.env
    python3.10 tools/aoai_proxy.py

The proxy listens on localhost, accepts the dummy key used by Paper2Code, and
injects the real Azure OpenAI key only when forwarding requests upstream.
"""

from __future__ import annotations

import os
import shlex
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_ENV_FILE = Path("/etc/aoai-proxy.env")
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_TIMEOUT_SECONDS = 600
HEALTHZ_PATH = "/healthz"
MAX_ERROR_BODY_BYTES = 1024 * 1024
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
STRIPPED_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | {
    "api-key",
    "authorization",
    "host",
    "content-length",
}


@dataclass(frozen=True)
class ProxyConfig:
    upstream_endpoint: str
    upstream_api_key: str
    listen_host: str = DEFAULT_LISTEN_HOST
    port: int = DEFAULT_PORT
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    """Load KEY=VALUE lines into os.environ without overwriting existing vars."""
    if not path.exists():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise RuntimeError(f"invalid env line {path}:{line_number}: {exc}") from exc
        if len(parts) != 1 or "=" not in parts[0]:
            raise RuntimeError(f"invalid env line {path}:{line_number}: expected KEY=VALUE")
        key, value = parts[0].split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value


def load_config_from_env() -> ProxyConfig:
    """Build proxy config from environment variables."""
    upstream_endpoint = os.getenv("AOAI_PROXY_UPSTREAM_ENDPOINT", "").strip()
    upstream_api_key = os.getenv("AOAI_PROXY_UPSTREAM_API_KEY", "").strip()
    listen_host = os.getenv("AOAI_PROXY_LISTEN_HOST", DEFAULT_LISTEN_HOST).strip()
    port = int(os.getenv("AOAI_PROXY_PORT", str(DEFAULT_PORT)))
    timeout_seconds = int(
        os.getenv("AOAI_PROXY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    )

    if not upstream_endpoint:
        raise RuntimeError("AOAI_PROXY_UPSTREAM_ENDPOINT is required")
    if not upstream_api_key:
        raise RuntimeError("AOAI_PROXY_UPSTREAM_API_KEY is required")
    if not listen_host:
        raise RuntimeError("AOAI_PROXY_LISTEN_HOST must not be empty")
    if port <= 0 or port > 65535:
        raise RuntimeError("AOAI_PROXY_PORT must be between 1 and 65535")
    if timeout_seconds <= 0:
        raise RuntimeError("AOAI_PROXY_TIMEOUT_SECONDS must be positive")

    return ProxyConfig(
        upstream_endpoint=upstream_endpoint,
        upstream_api_key=upstream_api_key,
        listen_host=listen_host,
        port=port,
        timeout_seconds=timeout_seconds,
    )


def has_required_proxy_env() -> bool:
    """Return whether systemd or the shell already supplied required settings."""
    return bool(
        os.getenv("AOAI_PROXY_UPSTREAM_ENDPOINT", "").strip()
        and os.getenv("AOAI_PROXY_UPSTREAM_API_KEY", "").strip()
    )


def make_upstream_url(config: ProxyConfig, request_target: str) -> str:
    """Append the incoming path and query to the configured upstream endpoint."""
    if not request_target.startswith("/"):
        request_target = f"/{request_target}"
    return f"{config.upstream_endpoint.rstrip('/')}{request_target}"


def filtered_request_headers(handler: BaseHTTPRequestHandler, config: ProxyConfig) -> dict[str, str]:
    """Copy safe client headers and inject the real Azure OpenAI API key."""
    headers = {}
    for key, value in handler.headers.items():
        if key.lower() in STRIPPED_REQUEST_HEADERS:
            continue
        headers[key] = value
    headers["api-key"] = config.upstream_api_key
    return headers


def copy_response_headers(
    handler: BaseHTTPRequestHandler,
    upstream_headers,
    body: bytes,
) -> None:
    """Relay upstream response headers while avoiding hop-by-hop metadata."""
    sent_content_length = False
    for key, value in upstream_headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered == "content-length":
            sent_content_length = True
        handler.send_header(key, value)
    if not sent_content_length:
        handler.send_header("Content-Length", str(len(body)))


def describe_upstream_failure(exc: Exception) -> str:
    """Return a safe upstream failure description without request bodies or keys."""
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", "")
        if reason:
            return f"{type(exc).__name__}: {reason}"
    detail = str(exc)
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return type(exc).__name__


def make_proxy_handler(config: ProxyConfig) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to a proxy config."""

    class AoaiProxyHandler(BaseHTTPRequestHandler):
        server_version = "aoai-proxy/1.0"

        def do_GET(self):
            if self.path == HEALTHZ_PATH:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"ok\n")
                return
            self._proxy_request()

        def do_HEAD(self):
            self._proxy_request()

        def do_POST(self):
            self._proxy_request()

        def do_PUT(self):
            self._proxy_request()

        def do_PATCH(self):
            self._proxy_request()

        def do_DELETE(self):
            self._proxy_request()

        def do_OPTIONS(self):
            self._proxy_request()

        def log_message(self, format, *args):
            sys.stderr.write(
                "%s - - [%s] %s\n"
                % (self.client_address[0], self.log_date_time_string(), format % args)
            )

        def _proxy_request(self):
            body = self._read_request_body()
            request = urllib.request.Request(
                make_upstream_url(config, self.path),
                data=body,
                headers=filtered_request_headers(self, config),
                method=self.command,
            )

            try:
                with urllib.request.urlopen(
                    request, timeout=config.timeout_seconds
                ) as upstream_response:
                    response_body = upstream_response.read()
                    self.send_response(upstream_response.status)
                    copy_response_headers(self, upstream_response.headers, response_body)
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(response_body)
            except urllib.error.HTTPError as exc:
                response_body = exc.read(MAX_ERROR_BODY_BYTES)
                self.send_response(exc.code)
                copy_response_headers(self, exc.headers, response_body)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response_body)
            except Exception as exc:
                failure = describe_upstream_failure(exc)
                sys.stderr.write(f"upstream request failed: {failure}\n")
                message = f"upstream request failed: {failure}\n".encode()
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(message)

        def _read_request_body(self) -> bytes | None:
            content_length = self.headers.get("Content-Length")
            if not content_length:
                return None
            return self.rfile.read(int(content_length))

    return AoaiProxyHandler


def run_server(config: ProxyConfig) -> None:
    """Run the proxy until the process receives an interrupt or stop signal."""
    handler = make_proxy_handler(config)
    with ThreadingHTTPServer((config.listen_host, config.port), handler) as server:
        print(
            f"aoai-proxy listening on http://{config.listen_host}:{config.port}",
            flush=True,
        )
        server.serve_forever()


def main() -> int:
    """Load configuration and start the proxy service."""
    try:
        if not has_required_proxy_env():
            load_env_file()
        config = load_config_from_env()
        run_server(config)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"aoai-proxy startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
