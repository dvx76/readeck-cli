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


@dataclass
class Settings:
    url: str
    token: str
    insecure: bool = False
    timeout: float = 30.0


def load_settings(args: Any) -> Settings:
    """Resolve connection settings from flags, environment, and config file."""
    url = (args.url or os.environ.get("READECK_URL") or "").strip().rstrip("/")
    token = (args.token or os.environ.get("READECK_TOKEN") or "").strip()
    if (not url or not token) and DEFAULT_CONFIG_PATH.exists():
        try:
            cfg = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CliError(f"cannot read config file {DEFAULT_CONFIG_PATH}: {exc}", 3) from exc
        url = url or str(cfg.get("url", "")).strip().rstrip("/")
        token = token or str(cfg.get("token", "")).strip()
    if not url or not token:
        raise CliError(
            "missing configuration. Provide READECK_URL and READECK_TOKEN environment "
            f"variables, --url/--token flags, or a config file at {DEFAULT_CONFIG_PATH} containing "
            '{"url": "https://readeck.example.com", "token": "..."}. Create a token in '
            "Readeck under Settings -> API Tokens (scope bookmarks:read is sufficient).",
            3,
        )
    if not url.startswith(("http://", "https://")):
        raise CliError(f"invalid Readeck URL {url!r}: must start with http:// or https://", 3)
    return Settings(url=url, token=token, insecure=bool(args.insecure), timeout=float(args.timeout))
