from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "docs" / "decisions" / "0009-hands-free-runner.md"

ANSWERS = [
    "Approval: on the central page",
    "Guard: opt-in per project",
    "Daily cap: 20 USD per runner",
    "Poll: 5 minutes",
    "Pause: the runner's person and admins",
    "Planner: opus, may plan without a person",
]


def test_decision_record_exists():
    assert DOC.is_file()


@pytest.mark.parametrize("answer", ANSWERS)
def test_decision_record_contains_answer(answer):
    assert answer in DOC.read_text(encoding="utf-8")


def test_decision_record_has_decisions_section():
    assert "\n## Decisions\n" in DOC.read_text(encoding="utf-8")
