"""Runbook indexing: frontmatter parsing and path-confined body loading."""

from app.context.runbooks import load_runbook_body, load_runbook_index

RUNBOOK = """---
title: High Error Rate
summary: Elevated 5xx responses.
applies_to: HighErrorRate
---

# High Error Rate

Diagnose things.
"""


def test_index_reads_frontmatter(tmp_path):
    (tmp_path / "high-error-rate.md").write_text(RUNBOOK)
    index = load_runbook_index(str(tmp_path))

    assert len(index) == 1
    rb = index[0]
    assert rb.path == "high-error-rate.md"
    assert rb.title == "High Error Rate"
    assert rb.summary == "Elevated 5xx responses."
    assert rb.applies_to == "HighErrorRate"


def test_missing_frontmatter_falls_back_to_filename(tmp_path):
    (tmp_path / "db-pool-exhausted.md").write_text("# No frontmatter here\n")
    index = load_runbook_index(str(tmp_path))

    assert index[0].title == "Db Pool Exhausted"
    assert index[0].summary == ""


def test_unclosed_frontmatter_yields_no_metadata(tmp_path):
    (tmp_path / "broken.md").write_text("---\ntitle: Broken\n\n# never closed\n")
    index = load_runbook_index(str(tmp_path))

    assert index[0].title == "Broken"  # falls back to filename-derived title


def test_missing_dir_returns_empty_index(tmp_path):
    assert load_runbook_index(str(tmp_path / "nope")) == []


def test_body_loads_and_truncates(tmp_path):
    (tmp_path / "rb.md").write_text(RUNBOOK)

    assert "Diagnose things." in load_runbook_body(str(tmp_path), "rb.md")
    assert len(load_runbook_body(str(tmp_path), "rb.md", max_chars=10)) == 10


def test_body_rejects_path_traversal(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read")
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()

    assert load_runbook_body(str(runbooks), "../secret.txt") is None
    assert load_runbook_body(str(runbooks), "absent.md") is None
