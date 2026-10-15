"""Tests for the `readeck auth` command and config-file resolution.

Prompt-driven flows are tested in-process with monkeypatched input()/getpass:
driving a real pseudo-terminal proved flaky because getpass flushes pending
input (TCSAFLUSH) and writes its prompt to /dev/tty when one is available.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys

import pytest
from conftest import TOKEN, bare_env, run_cli

from readeck_cli import commands
from readeck_cli import config as config_mod


def write_config(path, url: str, token: str) -> None:
    path.write_text(json.dumps({"url": url, "token": token}))


def auth_args(**overrides) -> argparse.Namespace:
    defaults = dict(url=None, token=None, no_verify=True, insecure=False, timeout=30.0, action="login")
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class FakeTTY:
    def isatty(self) -> bool:
        return True


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Point the CLI at a temp config file and remove credential env vars."""
    config = tmp_path / "cli.json"
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(commands, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.delenv("READECK_URL", raising=False)
    monkeypatch.delenv("READECK_TOKEN", raising=False)
    return config


# ---------------------------------------------------------------------------
# subprocess tests (non-interactive behavior)
# ---------------------------------------------------------------------------


def test_auth_login_saves_config_and_verifies(stub, tmp_path):
    config = tmp_path / "cli.json"
    proc = run_cli("auth", "--url", stub.url, "--token", TOKEN, env=bare_env(config))
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout)
    assert receipt["verified"] is True
    assert receipt["instance"]["version"] == "0.23.4"
    assert receipt["config"] == str(config)
    assert TOKEN not in proc.stdout  # token is masked in the receipt

    assert config.exists()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert json.loads(config.read_text()) == {"url": stub.url, "token": TOKEN}


def test_auth_login_makes_search_work_without_env(stub, tmp_path):
    config = tmp_path / "cli.json"
    assert run_cli("auth", "--url", stub.url, "--token", TOKEN, env=bare_env(config)).returncode == 0
    proc = run_cli("search", "-q", "linux", env=bare_env(config))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["total"] == 2


def test_auth_login_unverified_still_saves(stub, tmp_path):
    config = tmp_path / "cli.json"
    proc = run_cli("auth", "--url", stub.url, "--token", "wrong-token", env=bare_env(config))
    assert proc.returncode == 0
    receipt = json.loads(proc.stdout)
    assert receipt["verified"] is False
    assert "401" in receipt["error"]
    assert config.exists()


def test_auth_login_needs_flags_without_tty(stub, tmp_path):
    config = tmp_path / "cli.json"
    proc = run_cli("auth", env=bare_env(config))
    assert proc.returncode == 2
    assert "non-interactive" in proc.stderr

    proc = run_cli("auth", "--url", stub.url, env=bare_env(config))  # token missing
    assert proc.returncode == 2


def test_auth_login_rejects_invalid_url(tmp_path):
    config = tmp_path / "cli.json"
    proc = run_cli("auth", "--url", "ftp://example.com", "--token", "x", env=bare_env(config))
    assert proc.returncode == 2
    assert "invalid Readeck URL" in proc.stderr


def test_auth_status_configured(stub, tmp_path):
    config = tmp_path / "cli.json"
    write_config(config, stub.url, TOKEN)
    proc = run_cli("auth", "status", env=bare_env(config))
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["configured"] is True
    assert report["reachable"] is True
    assert report["instance"]["version"] == "0.23.4"
    assert report["token"] != TOKEN
    assert report["token"].startswith("test") and report["token"].endswith("oken")


def test_auth_status_unreachable_instance(tmp_path):
    config = tmp_path / "cli.json"
    write_config(config, "http://127.0.0.1:1", TOKEN)  # nothing listens there
    proc = run_cli("auth", "status", env=bare_env(config))
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["configured"] is True
    assert report["reachable"] is False
    assert report["error"]


def test_auth_status_unconfigured_exits_3(tmp_path):
    proc = run_cli("auth", "status", env=bare_env(tmp_path / "missing.json"))
    assert proc.returncode == 3
    report = json.loads(proc.stdout)
    assert report["configured"] is False


def test_auth_logout_removes_config(stub, tmp_path):
    config = tmp_path / "cli.json"
    write_config(config, stub.url, TOKEN)

    proc = run_cli("auth", "logout", env=bare_env(config))
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["removed"] is True
    assert not config.exists()

    proc = run_cli("auth", "logout", env=bare_env(config))
    assert json.loads(proc.stdout)["removed"] is False


# ---------------------------------------------------------------------------
# in-process tests (interactive prompting)
# ---------------------------------------------------------------------------


def test_auth_login_prompts_for_url_and_token(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt="": "http://readeck.example.com")
    monkeypatch.setattr(commands.getpass, "getpass", lambda prompt="": "typed-token")

    commands.cmd_auth(auth_args())

    receipt = json.loads(capsys.readouterr().out)
    assert receipt["url"] == "http://readeck.example.com"
    assert receipt["verified"] is False  # --no-verify
    assert json.loads(isolated_config.read_text()) == {
        "url": "http://readeck.example.com",
        "token": "typed-token",
    }


def test_auth_login_reprompts_on_invalid_url(isolated_config, monkeypatch, capsys):
    answers = iter(["not-a-url", "http://readeck.example.com"])
    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(commands.getpass, "getpass", lambda prompt="": "typed-token")

    commands.cmd_auth(auth_args())

    captured = capsys.readouterr()
    assert "valid http(s) URL" in captured.err
    assert json.loads(captured.out)["url"] == "http://readeck.example.com"


def test_auth_login_enter_keeps_stored_credentials(isolated_config, monkeypatch, capsys):
    write_config(isolated_config, "http://stored.example.com", "stored-token")
    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # accept the default URL
    monkeypatch.setattr(commands.getpass, "getpass", lambda prompt="": "")  # keep the stored token

    commands.cmd_auth(auth_args())

    json.loads(capsys.readouterr().out)
    assert json.loads(isolated_config.read_text()) == {
        "url": "http://stored.example.com",
        "token": "stored-token",
    }


def test_auth_login_cancelled_on_eof(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", FakeTTY())

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(SystemExit) as excinfo:
        commands.cmd_auth(auth_args())

    assert excinfo.value.code == 130
    assert "cancelled" in capsys.readouterr().err
    assert not isolated_config.exists()
