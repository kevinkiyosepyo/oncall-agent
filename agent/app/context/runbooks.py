"""Index the runbooks/ directory for the runbook-matching step.

Only titles and summaries go to the LLM (the index), never full bodies —
matching against 5 summaries costs a few hundred tokens regardless of how
long the runbooks get. Runbook frontmatter is a minimal `key: value` block
between `---` markers; values must be single-line.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Runbook:
    path: str  # filename relative to the runbooks dir, e.g. "high-error-rate.md"
    title: str
    summary: str
    applies_to: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return {}  # never saw the closing marker


def load_runbook_body(runbooks_dir: str, filename: str, max_chars: int = 4000) -> str | None:
    """Full text of one runbook (for the postmortem), path-confined."""
    path = (Path(runbooks_dir) / filename).resolve()
    if not path.is_relative_to(Path(runbooks_dir).resolve()) or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text[:max_chars]


def load_runbook_index(runbooks_dir: str) -> list[Runbook]:
    root = Path(runbooks_dir)
    if not root.is_dir():
        return []
    index = []
    for f in sorted(root.glob("*.md")):
        meta = _parse_frontmatter(f.read_text(encoding="utf-8"))
        index.append(
            Runbook(
                path=f.name,
                title=meta.get("title", f.stem.replace("-", " ").title()),
                summary=meta.get("summary", ""),
                applies_to=meta.get("applies_to", ""),
            )
        )
    return index
