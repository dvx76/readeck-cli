"""Shared fixtures: a stub Readeck API server and a CLI runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
TOKEN = "test-token"

BM1 = {
    "id": "bm1",
    "title": "The Linux Kernel Documentation · The Linux Kernel documentation",
    "url": "https://www.kernel.org/doc/html/latest/",
    "site_name": "kernel.org",
    "site": "kernel.org",
    "authors": ["Linus Torvalds"],
    "description": "Official Linux kernel docs.",
    "created": "2026-05-01T10:00:00+00:00",
    "published": "2026-04-12T00:00:00+00:00",
    "labels": ["linux", "docs"],
    "is_marked": True,
    "is_archived": False,
    "read_progress": 40,
    "reading_time": 12,
    "word_count": 5400,
    "has_article": True,
    "loaded": True,
    "type": "article",
    "lang": "en",
}
BM2 = {
    "id": "bm2",
    "title": "Latest kernel security news",
    "url": "https://lwn.net/Articles/999",
    "site_name": "LWN.net",
    "created": "2026-09-20T09:30:00+00:00",
    "published": None,
    "labels": ["linux", "security"],
    "is_marked": False,
    "is_archived": True,
    "read_progress": 100,
    "reading_time": 4,
    "word_count": 1200,
    "has_article": True,
    "loaded": True,
    "type": "article",
    "lang": "en",
}
ARTICLE_MD = (
    "---\ntitle: The Linux Kernel Documentation\nsource: https://www.kernel.org/doc/html/latest/\n---\n\n"
    "# The Linux Kernel Documentation\n\nText with ==a highlighted passage== and more.\n"
)


@dataclass
class Stub:
    url: str = ""
    queries: list[str] = field(default_factory=list)

    @property
    def last_query(self) -> str:
        return self.queries[-1] if self.queries else ""

    def reset(self) -> None:
        self.queries.clear()


def make_handler(stub: Stub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ARG002 - silence request logging
            pass

        def _send(self, code, payload=None, headers=None, raw=None):
            self.send_response(code)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if raw is not None:
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.end_headers()
                self.wfile.write(raw.encode())
                return
            body = json.dumps(payload).encode() if payload is not None else b""
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send(401, {"error": "unauthorized"})
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            stub.queries.append(self.path)
            if path == "/api/bookmarks":
                total = 0 if query.get("search", ["x"])[0] == "nothing" else 2
                return self._send(200, [BM1, BM2] if total else [], {"Total-Count": str(total)})
            if path == "/api/bookmarks/bm1":
                return self._send(200, BM1 | {"note": "Read later", "resources": {}})
            if path == "/api/bookmarks/bm1/article.md":
                return self._send(200, raw=ARTICLE_MD)
            if path == "/api/bookmarks/bm2/article.md":
                return self._send(404, {"error": "no article"})
            if path == "/api/bookmarks/labels":
                return self._send(200, [{"name": "linux", "count": 4}, {"name": "security", "count": 1}])
            if path == "/api/bookmarks/annotations":
                return self._send(
                    200,
                    [
                        {
                            "id": "h1",
                            "text": "highlighted passage",
                            "created": "2026-05-02T00:00:00Z",
                            "bookmark_id": "bm1",
                            "bookmark_title": BM1["title"],
                            "bookmark_url": BM1["url"],
                        }
                    ],
                )
            if path == "/api/bookmarks/bm1/annotations":
                return self._send(
                    200,
                    [
                        {
                            "id": "h1",
                            "text": "highlighted passage",
                            "created": "2026-05-02T00:00:00Z",
                        }
                    ],
                )
            if path == "/api/bookmarks/collections":
                return self._send(
                    200,
                    [
                        {
                            "id": "c1",
                            "name": "Kernel stuff",
                            "search": "kernel",
                            "range_start": "-6m",
                            "is_pinned": True,
                        }
                    ],
                )
            if path == "/api/info":
                return self._send(200, {"version": "0.23.4"})
            return self._send(404, {"error": "not found"})

    return Handler


@pytest.fixture(scope="session")
def stub():
    stub = Stub()
    server = HTTPServer(("127.0.0.1", 0), make_handler(stub))
    stub.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield stub
    server.shutdown()


def base_env(stub: Stub, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        **os.environ,
        "READECK_URL": stub.url,
        "READECK_TOKEN": TOKEN,
        "READECK_CLI_CONFIG": "/nonexistent/config.json",
        "PYTHONPATH": str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    env.update(extra or {})
    return env


def run_cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "readeck_cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture()
def run(stub):
    stub.reset()

    def _run(*args, env_extra=None, expect_ok=True):
        proc = run_cli(*args, env=base_env(stub, env_extra))
        if expect_ok and proc.returncode != 0:
            raise AssertionError(f"command failed: {args}\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
        return proc

    return _run
