"""Collect recent commits + diffs from the demo service's repo.

The repo is a read-only bind mount owned by the host user, so every git
invocation passes -c safe.directory=* to bypass the dubious-ownership check.
Diffs are truncated in Python before they reach any prompt.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("oncall-agent.context.git")

FIELD_SEP = "\x1f"
MAX_COMMITS = 10
MAX_DIFF_LINES = 300


@dataclass
class Commit:
    sha: str  # short sha
    author: str
    date: str  # ISO 8601
    message: str
    diff: str  # truncated patch


def _git(repo_dir: str, *args: str) -> str | None:
    cmd = ["git", "-C", repo_dir, "-c", "safe.directory=*", *args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        log.error("git %s failed: %s", args[0], e.stderr.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error("git %s failed: %s", args[0], e)
    return None


def _truncate(diff: str, max_lines: int) -> str:
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff
    kept = lines[:max_lines]
    kept.append(f"... [{len(lines) - max_lines} diff lines truncated]")
    return "\n".join(kept)


def collect_commits(
    repo_dir: str, max_commits: int = MAX_COMMITS, max_diff_lines: int = MAX_DIFF_LINES
) -> list[Commit] | None:
    """Last N commits, newest first, or None when no repo is available."""
    if not Path(repo_dir, ".git").exists():
        log.warning("no git repo at %s", repo_dir)
        return None

    log_out = _git(
        repo_dir,
        "log",
        f"-n{max_commits}",
        f"--pretty=format:%h{FIELD_SEP}%an{FIELD_SEP}%aI{FIELD_SEP}%s",
    )
    if not log_out:
        return None

    commits: list[Commit] = []
    for line in log_out.splitlines():
        parts = line.split(FIELD_SEP)
        if len(parts) != 4:
            continue
        sha, author, date, message = parts
        diff = _git(repo_dir, "show", "--format=", "--no-color", "--patch", sha) or ""
        commits.append(
            Commit(
                sha=sha,
                author=author,
                date=date,
                message=message,
                diff=_truncate(diff.strip(), max_diff_lines),
            )
        )
    return commits or None
