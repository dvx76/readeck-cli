"""HTTP client and error types for the Readeck API (stdlib only)."""

from __future__ import annotations

import http.client
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


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or {"http": 80, "https": 443}.get(parts.scheme)


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the same origin, so the bearer token never leaves it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != _origin(req.full_url):
            raise CliError(
                f"Readeck at {req.full_url} redirected ({code}) to a different origin: {newurl}; "
                "refusing to follow it with the API token (check READECK_URL and your reverse proxy)"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ssl_context), _SameOriginRedirectHandler
        )

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
            with self.opener.open(req, timeout=self.timeout) as resp:
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
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout_error(method, path) from exc
            raise CliError(f"cannot reach Readeck at {self.base}: {exc.reason}") from exc
        except TimeoutError as exc:  # raised by resp.read() once the response has started
            raise self._timeout_error(method, path) from exc
        except (OSError, http.client.HTTPException) as exc:  # connection reset, incomplete read, ...
            raise CliError(f"network error for {method} {path}: {type(exc).__name__}: {exc}") from exc

    def media(self, url: str) -> bytes | None:
        """Fetch a bookmark resource (article image) or None. Readeck serves them publicly under
        ``/bm/``, so no token is sent, and only URLs on the Readeck instance itself are fetched."""
        if _origin(url) != _origin(self.base):
            return None
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.read(32 << 20)
        except CliError, OSError, http.client.HTTPException, ValueError:
            return None

    def _timeout_error(self, method: str, path: str) -> CliError:
        return CliError(f"timed out after {self.timeout:g}s for {method} {path} (raise it with --timeout SECONDS)")

    def json(self, method: str, path: str, params: dict[str, Any] | None = None) -> tuple[Any, Any]:
        headers, body = self.request(method, path, params)
        try:
            return headers, json.loads(body)
        except json.JSONDecodeError as exc:
            raise CliError(f"invalid JSON from Readeck for {path}: {exc}") from exc
