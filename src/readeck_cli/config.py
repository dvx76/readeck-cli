"""Configuration resolution for the Readeck CLI.

Order of precedence:

1. ``--url`` / ``--token`` flags
2. ``READECK_URL`` / ``READECK_TOKEN`` environment variables
3. ``~/.config/readeck/cli.json`` (``{"url": "...", "token": "..."}``)

The config path can be overridden with ``READECK_CLI_CONFIG``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readeck_cli.api import CliError

DEFAULT_CONFIG_PATH = Path(os.environ.get("READECK_CLI_CONFIG", "~/.config/readeck/cli.json")).expanduser()

MISSING_CONFIG_MESSAGE = (
    "missing configuration. Run `readeck auth` to save your instance URL and API token, or set "
    f"READECK_URL and READECK_TOKEN, or create {DEFAULT_CONFIG_PATH} containing "
    '{"url": "https://readeck.example.com", "token": "..."}. Create a token in Readeck under '
    "Settings -> API Tokens (scope bookmarks:read is sufficient)."
)


@dataclass
class Settings:
    url: str
    token: str
    insecure: bool = False
    timeout: float = 30.0


@dataclass
class Resolved:
    """Raw credential resolution result, including where each value came from."""

    url: str = ""
    token: str = ""
    url_source: str = ""
    token_source: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)


def read_config_file() -> dict[str, Any]:
    """Return the config file contents as a dict ({} when the file does not exist)."""
    if not DEFAULT_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read config file {DEFAULT_CONFIG_PATH}: {exc}", 3) from exc
    if not isinstance(data, dict):
        raise CliError(f"config file {DEFAULT_CONFIG_PATH} must contain a JSON object", 3)
    return data


def save_settings(url: str, token: str) -> Path:
    """Write credentials to the config file (merging existing keys), mode 0600."""
    cfg = read_config_file()
    cfg.update({"url": url, "token": token})
    payload = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(DEFAULT_CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.chmod(DEFAULT_CONFIG_PATH, 0o600)  # tighten permissions of a pre-existing file
    return DEFAULT_CONFIG_PATH


def clear_settings() -> bool:
    """Delete the config file. Returns True when it existed."""
    if DEFAULT_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.unlink()
        return True
    return False


def resolve_settings(args: Any) -> Resolved:
    """Resolve credentials from flags, environment, and config file (in that order)."""
    resolved = Resolved()
    if args.url:
        resolved.url = args.url.strip().rstrip("/")
        resolved.url_source = "flag"
    elif os.environ.get("READECK_URL", "").strip():
        resolved.url = os.environ["READECK_URL"].strip().rstrip("/")
        resolved.url_source = "environment"
    if args.token:
        resolved.token = args.token.strip()
        resolved.token_source = "flag"
    elif os.environ.get("READECK_TOKEN", "").strip():
        resolved.token = os.environ["READECK_TOKEN"].strip()
        resolved.token_source = "environment"
    if not resolved.configured:
        cfg = read_config_file()
        if not resolved.url and cfg.get("url"):
            resolved.url = str(cfg["url"]).strip().rstrip("/")
            resolved.url_source = f"file ({DEFAULT_CONFIG_PATH})"
        if not resolved.token and cfg.get("token"):
            resolved.token = str(cfg["token"]).strip()
            resolved.token_source = f"file ({DEFAULT_CONFIG_PATH})"
    return resolved


def load_settings(args: Any) -> Settings:
    """Resolve settings and fail when the CLI is not configured."""
    resolved = resolve_settings(args)
    if not resolved.configured:
        raise CliError(MISSING_CONFIG_MESSAGE, 3)
    if not resolved.url.startswith(("http://", "https://")):
        raise CliError(f"invalid Readeck URL {resolved.url!r}: must start with http:// or https://", 3)
    return Settings(
        url=resolved.url,
        token=resolved.token,
        insecure=bool(args.insecure),
        timeout=float(args.timeout),
    )
