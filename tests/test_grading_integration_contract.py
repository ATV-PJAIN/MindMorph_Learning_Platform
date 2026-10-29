"""Real grading through the HTTP/service boundary, without AI or a database."""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api import routes
from persistence.repository import InMemoryLearningSessionRepository
from services import learning_service as svc
from services.mastery import default_node_state


PYTHON_TESTS = [
    "from solution import add",
    "def test_add():\n    assert add(2, 3) == 5\n",
]
JAVASCRIPT_TESTS = [
    "const { it } = require('node:test');",
    "const assert = require('node:assert/strict');",
    "const { add } = require('./solution');",
    "it('adds asynchronously', async () => { assert.equal(await add(2, 3), 5); });",
]
SQL_ASSERTIONS = [
    json.dumps({"label": "count", "query": "SELECT COUNT(*) FROM numbers", "expected": [[2]]}),
    json.dumps({"label": "sum", "query": "SELECT SUM(n) FROM numbers", "expected": [[5]]}),
]


def _session(exercise):
    return {
        "skill_graph": {"nodes": [{"id": "a", "label": "Addition"}], "edges": []},
        "node_state": {"a": default_node_state()},
        "lessons": {"a": {"content": "Addition", "exercise": exercise}},
        "events": [],
    }


@pytest.fixture
def grade_client(monkeypatch):
    repo = InMemoryLearningSessionRepository()
    monkeypatch.setattr(routes, "get_default_repository", lambda: repo)
    adaptation = Mock(return_value=[])
    monkeypatch.setattr(svc, "adapt_after_grade", adaptation)
    return TestClient(app), repo, adaptation


@pytest.mark.parametrize("language,artifact,solution,total", [
    ("py", {"format": "coding_challenge", "language": "python", "unit_tests": PYTHON_TESTS},
     "def add(a, b):\n    return a + b\n", 1),
    ("node", {"format": "coding_challenge", "language": "javascript", "unit_tests": JAVASCRIPT_TESTS},
     "module.exports.add = async (a,b) => a+b;", 1),
    ("sqlite", {"format": "coding_challenge", "language": "sql", "sql_assertions": SQL_ASSERTIONS},
     "CREATE TABLE numbers(n INT); INSERT INTO numbers VALUES(2),(3);", 2),
])
def test_supported_languages_grade_and_persist_through_http(grade_client, language, artifact, solution, total):
    client, repo, adaptation = grade_client
    repo.save("audit", "session", _session({
        "format": "coding_challenge", "language": language, "grading_artifact": artifact,
    }))
    response = client.post("/sessions/audit/session/grade", params={"node_id": "a"},
                           json={"solution": solution})
    assert response.status_code == 200
    result = response.json()["grade_result"]
    assert result["score"] == 100
    assert result["passed"] == result["total"] == total
    saved = repo.get("audit", "session")
    assert saved["node_state"]["a"]["status"] == "mastered"
    assert saved["node_state"]["a"]["attempts"] == 1
    adaptation.assert_called_once()


@pytest.mark.parametrize("language,artifact", [
    ("javascript", {"format": "coding_challenge", "language": "python", "unit_tests": PYTHON_TESTS}),
    ("rust", {"format": "coding_challenge", "language": "python", "unit_tests": PYTHON_TESTS}),
    ("sql", {"format": "coding_challenge", "language": "sql", "sql_assertions": ["broken JSON"]}),
    ("python", {"format": "coding_challenge", "language": "python", "unit_tests": ["x = 1"]}),
    ("javascript", {"format": "coding_challenge", "language": "javascript", "unit_tests": ["module.exports={};"]}),
    ("python", {"format": "case_study", "language": "python", "unit_tests": PYTHON_TESTS}),
])
def test_ungradable_exercises_do_not_penalize_learner(grade_client, language, artifact):
    client, repo, adaptation = grade_client
    initial = _session({"format": "coding_challenge", "language": language,
                        "grading_artifact": artifact})
    before = deepcopy(initial["node_state"])
    repo.save("audit", "session", initial)
    response = client.post("/sessions/audit/session/grade", params={"node_id": "a"},
                           json={"solution": "not a valid solution in any language"})
    assert response.status_code == 200
    result = response.json()["grade_result"]
    assert result["harness_error"] is True
    assert "score" not in result
    saved = repo.get("audit", "session")
    assert saved["node_state"] == before
    assert response.json()["new_node_ids"] == []
    adaptation.assert_not_called()
    assert "grade_failure" in [event["stage"] for event in saved["events"]]
    assert "exercise_graded" not in [event["stage"] for event in saved["events"]]


def test_legacy_python_artifact_keeps_default_language(grade_client):
    client, repo, _ = grade_client
    repo.save("audit", "session", _session({
        "format": "coding_challenge", "grading_artifact": {"unit_tests": PYTHON_TESTS},
    }))
    response = client.post("/sessions/audit/session/grade", params={"node_id": "a"},
                           json={"solution": "def add(a,b): return a+b"})
    assert response.status_code == 200
    assert response.json()["grade_result"]["score"] == 100


def test_real_wrong_answer_still_triggers_remediation(grade_client):
    client, repo, adaptation = grade_client
    repo.save("audit", "session", _session({
        "format": "coding_challenge", "language": "python",
        "grading_artifact": {"language": "python", "unit_tests": PYTHON_TESTS},
    }))
    response = client.post("/sessions/audit/session/grade", params={"node_id": "a"},
                           json={"solution": "def add(a,b): return a-b"})
    assert response.status_code == 200
    assert response.json()["grade_result"]["score"] == 0
    state = repo.get("audit", "session")["node_state"]["a"]
    assert state["status"] == "needs_review"
    assert state["remediation_pending"] is True
    adaptation.assert_called_once()


@pytest.mark.parametrize("preferred,expected_format,expected_language", [
    ("JS", "coding_challenge", "javascript"),
    ("py", "coding_challenge", "python"),
    ("sqlite", "coding_challenge", "sql"),
    ("rust", "case_study", None),
])
async def test_exercise_graph_uses_supported_canonical_languages(preferred, expected_format, expected_language):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from graph.exercise_graph import build_exercise_graph

    selector = Mock()
    selector.select_format.return_value = SimpleNamespace(
        format="coding_challenge", language="python", reasoning="practice",
    )
    synthesizer = Mock()
    synthesizer.synthesize.return_value = "Exercise statement"
    grader = Mock()
    grader.build_grading_artifact.return_value = None
    factual = Mock()
    factual.gather_facts.return_value = None
    graph = build_exercise_graph(format_selector=selector, synthesizer=synthesizer,
                                 grader=grader, github=AsyncMock(return_value=None),
                                 blog=factual, dataset=factual)
    result = await graph.ainvoke({"user_query": "practice", "primary_language": preferred})
    assert result["exercise_format"] == expected_format
    assert result["exercise_language"] == expected_language
    assert synthesizer.synthesize.call_args.kwargs["exercise_language"] == expected_language
    assert grader.build_grading_artifact.call_args.kwargs["exercise_language"] == (expected_language or "python")
