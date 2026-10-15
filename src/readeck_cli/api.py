"""HTTP client and error types for the Readeck API (stdlib only)."""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from readeck_cli import __version__

USER_AGENT = f"readeck-cli/{__version__}"


class CliError(Exception):
    """Fatal error reported to the user. ``code`` is the process exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def fail(message: str, code: int = 1) -> None:
    """Print an error and exit immediately (used by argument-level checks)."""
    print(f"readeck: {message}", file=sys.stderr)
    raise SystemExit(code)


class Api:
    """Minimal Readeck API client using the ``/api`` endpoints."""

    def __init__(self, base_url: str, token: str, insecure: bool = False, timeout: float = 30.0):
        self.base = base_url + "/api"
        self.token = token
        self.timeout = timeout
        self.ssl_context = ssl.create_default_context()
        if insecure:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> tuple[Any, bytes]:
        query = ""
        if params:
            flat = {k: v for k, v in params.items() if v is not None}
            if flat:
                query = "?" + urllib.parse.urlencode(flat, doseq=True)
        req = urllib.request.Request(self.base + path + query, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", accept)
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                return resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300].strip()
            hint = ""
            if exc.code == 401:
                hint = " (check READECK_TOKEN: the token is invalid or revoked)"
            elif exc.code == 403:
                hint = " (the token lacks the required scope; bookmarks:read should be enough)"
            elif exc.code == 404:
                hint = " (not found: bad ID, or the bookmark has no article yet)"
            raise CliError(f"Readeck API error {exc.code} for {method} {path}{hint}: {body}") from exc
        except urllib.error.URLError as exc:
            raise CliError(f"cannot reach Readeck at {self.base}: {exc.reason}") from exc

    def json(self, method: str, path: str, params: dict[str, Any] | None = None) -> tuple[Any, Any]:
        headers, body = self.request(method, path, params)
        try:
            return headers, json.loads(body)
        except json.JSONDecodeError as exc:
            raise CliError(f"invalid JSON from Readeck for {path}: {exc}") from exc
