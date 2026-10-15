"""End-to-end tests for the CLI against a stub Readeck API."""

from __future__ import annotations

import json

from conftest import ARTICLE_MD, BM1, base_env, run_cli


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


def test_search_table_output(run):
    proc = run("search", "-q", "linux", "--format", "table")
    assert "bm1" in proc.stdout
    assert "Linux Kernel" in proc.stdout


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
    table = run("highlights", "bm1", "--format", "table").stdout
    assert "highlighted passage" in table


def test_labels_sorted_with_counts(run):
    labels = json.loads(run("labels").stdout)
    assert [label["name"] for label in labels] == ["linux", "security"]
    assert "linux" in run("labels", "--format", "table").stdout


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
