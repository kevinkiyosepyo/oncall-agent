"""Git collector against a real throwaway repo."""

import subprocess

from app.context.git_history import collect_commits


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@t.t", *args],
        check=True, capture_output=True,
    )


def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "svc.py").write_text("POOL_SIZE = 20\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial import")
    (repo / "svc.py").write_text("POOL_SIZE = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "db: trim connection pool")
    return repo


def test_collects_commits_newest_first_with_diffs(tmp_path):
    commits = collect_commits(str(_make_repo(tmp_path)))

    assert [c.message for c in commits] == ["db: trim connection pool", "initial import"]
    assert all(len(c.sha) >= 7 for c in commits)
    assert all(c.author == "Test" for c in commits)
    assert "-POOL_SIZE = 20" in commits[0].diff
    assert "+POOL_SIZE = 2" in commits[0].diff


def test_max_commits_limits_window(tmp_path):
    commits = collect_commits(str(_make_repo(tmp_path)), max_commits=1)

    assert len(commits) == 1
    assert commits[0].message == "db: trim connection pool"


def test_diff_truncation_annotates(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "big.py").write_text("\n".join(f"line_{i} = {i}" for i in range(200)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "big file")

    commits = collect_commits(str(repo), max_diff_lines=10)
    assert "diff lines truncated]" in commits[0].diff
    assert len(commits[0].diff.splitlines()) == 11  # 10 kept + annotation


def test_missing_repo_returns_none(tmp_path):
    assert collect_commits(str(tmp_path / "no-repo")) is None
