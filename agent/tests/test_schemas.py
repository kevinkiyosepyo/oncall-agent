"""Runtime-built enum schemas: hallucinated runbook paths and invented commit
SHAs must fail validation instead of reaching a brief."""

import pytest
from pydantic import ValidationError

from app.llm.steps.commit_analysis import build_output_model as build_commit_model
from app.llm.steps.runbook_match import NO_MATCH, build_output_model as build_match_model


def test_runbook_enum_accepts_known_paths_and_no_match():
    model = build_match_model(["a.md", "b.md"])

    for path in ("a.md", "b.md", NO_MATCH):
        parsed = model(
            runbook_path=path, confidence="high", reasoning="r", first_actions=["x"]
        )
        assert parsed.runbook_path == path


def test_runbook_enum_rejects_invented_path():
    model = build_match_model(["a.md"])

    with pytest.raises(ValidationError):
        model(
            runbook_path="hallucinated.md",
            confidence="high",
            reasoning="r",
            first_actions=[],
        )


def test_commit_enum_rejects_invented_sha():
    model = build_commit_model(["abc1234", "def5678"])

    with pytest.raises(ValidationError):
        model(
            suspect_commits=[
                {
                    "sha": "999beef",
                    "confidence": "high",
                    "mechanism": "m",
                    "evidence": [],
                }
            ],
            no_culprit_found=False,
            alternative_hypotheses=[],
        )


def test_commit_model_accepts_no_culprit_shape():
    model = build_commit_model(["abc1234"])
    parsed = model(
        suspect_commits=[],
        no_culprit_found=True,
        alternative_hypotheses=["dependency outage"],
    )

    assert parsed.no_culprit_found is True
    assert parsed.suspect_commits == []


def test_commit_model_accepts_valid_suspect_with_evidence():
    model = build_commit_model(["abc1234"])
    parsed = model(
        suspect_commits=[
            {
                "sha": "abc1234",
                "confidence": "medium",
                "mechanism": "unsafe dict access",
                "evidence": [{"diff_hunk": "+DISCOUNTS[code]", "error_line": "KeyError"}],
            }
        ],
        no_culprit_found=False,
        alternative_hypotheses=[],
    )

    assert parsed.suspect_commits[0].evidence[0].error_line == "KeyError"
