"""Recover cached failed grading setup without changing the exercise or penalizing progress."""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from agents.exercise import grader_agent
from agents.exercise.grading_schema import GradingArtifact
from api import routes
from api.main import app
from persistence.repository import InMemoryLearningSessionRepository
from services import learning_service as svc
from services.mastery import default_node_state
from tools.code_executor import execute_tests


def _artifact():
    return GradingArtifact(
        format="coding_challenge", language="python", instructions="Submit add(a, b).",
        unit_tests=["from solution import add", "def test_add():\n    assert add(2, 3) == 5"],
    )


def _session():
    return {
        "summary": "Learn Python", "events": [],
        "skill_graph": {"nodes": [{"id": "a", "label": "Addition"}], "edges": []},
        "node_state": {"a": default_node_state()},
        "lessons": {"a": {"content": "Keep this lesson", "exercise": {
            "revision": "original", "format": "coding_challenge", "language": "python",
            "statement": "Implement add(a, b).", "grading_artifact": None,
        }}},
    }


def _mock_grader(monkeypatch, artifact=None, error=None):
    builder = Mock(return_value=artifact, side_effect=error)
    constructor = Mock(return_value=Mock(build_grading_artifact=builder))
    monkeypatch.setattr(grader_agent, "GraderAgent", constructor)
    return builder


def test_reopening_failed_cached_setup_repairs_only_tests(monkeypatch):
    builder = _mock_grader(monkeypatch, _artifact())
    ls = _session()
    original_state = deepcopy(ls["node_state"])
    svc.open_lesson(ls, "a")
    exercise = ls["lessons"]["a"]["exercise"]
    assert exercise["grading_artifact"]["unit_tests"] == _artifact().unit_tests
    assert exercise["revision"] == "original"
    assert exercise["statement"] == "Implement add(a, b)."
    assert ls["lessons"]["a"]["content"] == "Keep this lesson"
    assert ls["node_state"] == original_state
    svc.open_lesson(ls, "a")
    builder.assert_called_once()


def test_missing_tests_repaired_and_real_grade_persisted_through_http(monkeypatch):
    builder = _mock_grader(monkeypatch, _artifact())
    repo = InMemoryLearningSessionRepository()
    repo.save("audit", "repair", _session())
    monkeypatch.setattr(routes, "get_default_repository", lambda: repo)
    monkeypatch.setattr(svc, "adapt_after_grade", Mock(return_value=[]))
    response = TestClient(app).post(
        "/sessions/audit/repair/grade?node_id=a",
        json={"solution": "def add(a,b): return a+b", "exercise_revision": "original"},
    )
    assert response.status_code == 200
    assert response.json()["grade_result"]["score"] == 100
    saved = repo.get("audit", "repair")
    assert saved["node_state"]["a"]["status"] == "mastered"
    assert saved["node_state"]["a"]["attempts"] == 1
    assert saved["lessons"]["a"]["exercise"]["revision"] == "original"
    builder.assert_called_once()


@pytest.mark.parametrize("error", [None, RuntimeError("generation unavailable")])
def test_failed_retry_still_does_not_score_or_adapt(monkeypatch, error):
    _mock_grader(monkeypatch, error=error)
    adaptation = Mock(return_value=[])
    monkeypatch.setattr(svc, "adapt_after_grade", adaptation)
    ls = _session()
    before = deepcopy(ls["node_state"])
    _, new_nodes, result = svc.grade(
        ls, "a", "def add(a,b): return a+b", exercise_revision="original"
    )
    assert result["harness_error"] is True
    assert "score" not in result
    assert ls["node_state"] == before
    assert ls["lessons"]["a"]["exercise"]["grading_artifact"] is None
    assert new_nodes == []
    adaptation.assert_not_called()


def test_existing_artifact_is_not_rebuilt(monkeypatch):
    builder = _mock_grader(monkeypatch)
    ls = _session()
    ls["lessons"]["a"]["exercise"]["grading_artifact"] = _artifact().model_dump()
    svc.open_lesson(ls, "a")
    builder.assert_not_called()


def test_python_grading_runtime_supports_pandas_and_numpy():
    result = execute_tests(
        "import numpy as np\nimport pandas as pd\n"
        "def revenue(rows):\n"
        "    df = pd.DataFrame(rows)\n"
        "    return np.multiply(df['units'], df['price']).sum()\n",
        "from solution import revenue\n"
        "def test_revenue():\n"
        "    assert revenue([{'units': 2, 'price': 3.5}]) == 7.0\n",
    )
    assert not result.get("harness_error"), result
    assert result["passed"] == result["total"] == 1
    assert result["score"] == 100
