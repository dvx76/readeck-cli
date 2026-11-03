"""End-to-end tests for the CLI against a stub Readeck API."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from conftest import ARTICLE_MD, BM1, TOKEN, base_env, run_cli


def test_search_json_and_params(run, stub):
    proc = run("search", "-q", "linux kernel", "--since", "6m", "--no-archived", "--limit", "10")
    data = json.loads(proc.stdout)
    assert data["total"] == 2
    assert data["bookmarks"][0]["id"] == "bm1"
    assert "resources" not in data["bookmarks"][0]
    assert data["bookmarks"][0]["readeck_url"] == f"{stub.url}/bookmarks/bm1"
    query = stub.last_query
    assert "range_start=-6m" in query  # --since 6m normalized to a relative token
    assert "is_archived=false" in query
    assert "is_marked" not in query


def test_search_boolean_and_list_filters(run, stub):
    run("search", "--read-status", "unread", "--marked", "--type", "article")
    query = stub.last_query
    assert "is_marked=true" in query
    assert "read_status=unread" in query
    assert "type=article" in query


def test_search_all_fetches_every_page(run):
    data = json.loads(run("search", "-q", "linux", "--all", "--max", "5").stdout)
    assert data["count"] == 2
    assert data["total"] == 2


def test_search_pretty_output(run):
    out = run("search", "-q", "linux", "--archived", "-o", "pretty").stdout
    assert "\x1b[" not in out  # piped: no color escapes
    assert '1–2 of 2 bookmarks  ·  "linux" · archived' in out
    assert "bm1" in out
    assert "#security" in out
    groups = ("This week", "This month", "Last 3 months", "This year", "Older")
    assert any(g in out for g in groups)  # grouped by save date under the default sort
    assert not any(g in run("search", "--sort", "title", "-o", "pretty").stdout for g in groups)


def test_output_defaults_to_pretty_on_a_terminal(stub):
    primary, replica = pty.openpty()
    env = base_env(stub, {"NO_COLOR": "1", "COLUMNS": "100"})
    proc = subprocess.Popen([sys.executable, "-m", "readeck_cli", "show", "bm1"], stdout=replica, env=env)
    os.close(replica)
    chunks = []
    while True:
        try:
            chunk = os.read(primary, 4096)
        except OSError:  # EIO once the child closes the terminal
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(primary)
    assert proc.wait() == 0
    out = b"".join(chunks).decode()
    assert "\x1b[" not in out  # NO_COLOR is honored
    assert "ARTICLE" in out
    assert "Read later" in out
    assert f"{stub.url}/bookmarks/bm1" in out


def browse_in_terminal(stub, keys: bytes) -> subprocess.CompletedProcess:
    """Run `browse` with a pseudo-terminal as stdin (the UI draws there) and stdout piped."""
    primary, replica = pty.openpty()
    fcntl.ioctl(replica, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 140, 0, 0))
    cmd = [sys.executable, "-m", "readeck_cli", "browse"]
    proc = subprocess.Popen(cmd, stdin=replica, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=base_env(stub))
    os.close(replica)
    screen = b""
    deadline = time.monotonic() + 10
    while b"esc quit" not in screen and time.monotonic() < deadline:  # wait for the first frame
        if select.select([primary], [], [], 0.1)[0]:
            screen += os.read(primary, 65536)
    os.write(primary, keys)

    def drain():  # keep reading frames so the UI never blocks on a full terminal buffer
        while proc.poll() is None:
            if select.select([primary], [], [], 0.05)[0]:
                try:
                    os.read(primary, 65536)
                except OSError:
                    return

    drain = threading.Thread(target=drain)
    drain.start()
    out, err = proc.communicate(timeout=10)
    drain.join()
    os.close(primary)
    return subprocess.CompletedProcess(cmd, proc.returncode, out.decode(), err.decode())


def test_browse_filter_and_arrows_select_a_bookmark(stub):
    filtered = browse_in_terminal(stub, b"security\r")
    assert filtered.returncode == 0, filtered.stderr
    assert json.loads(filtered.stdout)["id"] == "bm2"  # piped stdout gets JSON
    moved = browse_in_terminal(stub, b"\x1b[B\r")
    assert json.loads(moved.stdout)["id"] == "bm2"
    cancelled = browse_in_terminal(stub, b"\x1b")
    assert (cancelled.returncode, cancelled.stdout) == (130, "")


def test_browse_without_terminal_exits_2(run, stub):
    proc = run("browse", expect_ok=False)
    assert proc.returncode == 2
    assert "interactive terminal" in proc.stderr
    assert stub.queries == []  # fails before fetching


def test_show_keeps_metadata(run):
    bookmark = json.loads(run("show", "bm1").stdout)
    assert bookmark["id"] == "bm1"
    assert bookmark["note"] == "Read later"


def test_read_prints_markdown(run):
    proc = run("read", "bm1")
    assert proc.stdout.startswith("---")
    assert "==a highlighted passage==" in proc.stdout


def test_read_out_writes_file(run, stub, tmp_path):
    out = tmp_path / "nested" / "article.md"
    receipt = json.loads(run("read", "bm1", "--out", str(out)).stdout)
    assert out.read_text() == ARTICLE_MD
    assert receipt["path"] == str(out)
    assert receipt["readeck_url"] == f"{stub.url}/bookmarks/bm1"


def test_highlights_library_and_bookmark(run):
    highlights = json.loads(run("highlights").stdout)
    assert highlights[0]["bookmark_title"] == BM1["title"]
    assert highlights[0]["text"] == "highlighted passage"
    pretty = run("highlights", "-o", "pretty").stdout
    assert "highlighted passage" in pretty
    assert BM1["title"] in pretty


def test_labels_sorted_with_counts(run):
    labels = json.loads(run("labels").stdout)
    assert [label["name"] for label in labels] == ["linux", "security"]
    assert "linux" in run("labels", "-o", "pretty").stdout


def test_collections(run):
    collections = json.loads(run("collections").stdout)
    assert collections[0]["name"] == "Kernel stuff"


def test_info(run):
    assert json.loads(run("info").stdout)["version"] == "0.23.4"


def test_mirror_is_incremental(run, tmp_path):
    first = json.loads(run("mirror", "--since", "1y", "--dir", str(tmp_path)).stdout)
    assert first["added"] == 1  # bm1 exported
    assert first["failed"][0]["id"] == "bm2"  # bm2 has no article
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert files[0].name.startswith("2026-05-01-")
    assert files[0].name.endswith("-bm1.md")
    assert (tmp_path / "_index.json").exists()

    second = json.loads(run("mirror", "--since", "1y", "--dir", str(tmp_path)).stdout)
    assert second["added"] == 0
    assert second["skipped"] == 1

    refreshed = json.loads(run("mirror", "--since", "1y", "--dir", str(tmp_path), "--refresh").stdout)
    assert refreshed["added"] == 1


def test_bad_token_exits_1(run):
    proc = run("search", env_extra={"READECK_TOKEN": "wrong"}, expect_ok=False)
    assert proc.returncode == 1
    assert "401" in proc.stderr


def test_read_timeout_is_clean_error_and_mirror_continues(run, tmp_path):
    proc = run("read", "slow", "--timeout", "0.2", expect_ok=False)
    assert proc.returncode == 1
    assert proc.stderr.startswith("readeck: timed out after 0.2s")
    assert "--timeout" in proc.stderr
    assert "Traceback" not in proc.stderr

    result = json.loads(run("mirror", "-q", "slow", "--dir", str(tmp_path), "--timeout", "0.2").stdout)
    assert result["added"] == 1
    assert [f["id"] for f in result["failed"]] == ["slow"]
    assert "timed out" in result["failed"][0]["error"]
    assert "bm1" in json.loads((tmp_path / "_index.json").read_text())["bookmarks"]


def test_missing_config_exits_3(stub):
    env = base_env(stub)
    env.pop("READECK_URL")
    env.pop("READECK_TOKEN")
    proc = run_cli("search", env=env)
    assert proc.returncode == 3
    assert "Settings" in proc.stderr


def test_bad_limit_exits_2(run):
    proc = run("search", "--limit", "0", expect_ok=False)
    assert proc.returncode == 2


def test_version(run):
    proc = run("--version")
    assert proc.stdout.strip().startswith("readeck ")


def test_cross_origin_redirect_does_not_leak_token(run, stub):
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ARG002 - silence request logging
            pass

        def do_GET(self):  # noqa: N802 - stdlib naming
            self.send_response(302)
            self.send_header("Location", stub.url + self.path)  # same host, different port
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        proc = run("info", env_extra={"READECK_URL": url}, expect_ok=False)
    finally:
        server.shutdown()
    assert stub.queries == []  # the stub only records requests carrying the bearer token
    assert proc.returncode == 1
    assert f"different origin: {stub.url}/api/info" in proc.stderr
    assert TOKEN not in proc.stderr
