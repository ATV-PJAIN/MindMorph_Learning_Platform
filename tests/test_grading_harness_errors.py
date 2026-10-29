"""A broken GENERATED test must never be scored as a learner failure.

The regression these lock down was found live: every generated test for one exercise had a
submission-independent defect (imported scikit-learn, which is not installed; used `pd` without
importing pandas; passed a csv path into a sandbox that has no files). Grading returned 0/3 for a
deliberately wrong submission — and would have returned 0/3 for a perfect one. The false 0 flipped
the node to needs_review, locked it, injected remedial nodes, and told the learner their gap was
"install and import scikit-learn".

These tests assert the two halves of the fix: the executor tells a broken harness apart from a wrong
submission, and a broken harness produces no score, no mastery change and no adaptation — while a
genuinely wrong submission against a VALID artifact still scores low and still triggers remediation.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest

from services import learning_service as svc
from services.mastery import apply_score
from tools.code_executor import check_test_artifact, execute_tests

# The exact artifact from the live run: sklearn import, `pd` never imported, and a csv that the
# sandbox does not contain. Reproduced verbatim in shape so the fix is pinned to the real defect.
_BROKEN_TESTS = [
    "from solution import prepare_and_fit",
    (
        "def test_regression_case():\n"
        "    from sklearn.datasets import make_regression\n"
        "    X, y = make_regression(n_samples=10, n_features=2)\n"
        "    assert prepare_and_fit(csv_path='regression.csv') is not None\n"
    ),
    (
        "def test_additional_categorical_column():\n"
        "    df = pd.DataFrame({'a': [1, 2], 'b': ['x', 'y']})\n"
        "    assert prepare_and_fit(csv_path='regression.csv') is not None\n"
    ),
]

# A valid artifact: self-contained, stdlib only, inputs passed as arguments.
_VALID_TESTS = [
    "from solution import add",
    "def test_add():\n    assert add(2, 3) == 5\n",
    "def test_add_negative():\n    assert add(-1, 1) == 0\n",
]

_WRONG_SOLUTION = "def add(a, b):\n    return a - b\n"


# --- executor: broken harness vs wrong submission -----------------------------------------------

def test_broken_artifact_from_live_run_is_a_harness_error_not_a_zero():
    result = execute_tests("def prepare_and_fit(**kw):\n    return 1\n", "\n".join(_BROKEN_TESTS))
    assert result["harness_error"] is True
    assert "score" not in result  # nothing was measured, so no score may be reported
    assert any("sklearn" in f for f in result["failures"])


@pytest.mark.parametrize("tests,needle", [
    # Missing third-party library in the grading interpreter.
    (["def test_x():\n    import sklearn\n    assert True\n"], "sklearn"),
    # Test module uses a name it never imported.
    (["def test_x():\n    df = pd.DataFrame({'a': [1]})\n    assert df is not None\n"], "never imported"),
    # Test expects a data file the sandbox has no way to contain.
    (["def test_x():\n    open('regression.csv')\n"], "data file"),
    # Generated test module is not even valid Python.
    (["def test_x(:\n    pass\n"], "valid Python"),
])
def test_each_generated_defect_is_classified_as_harness(tests, needle):
    result = execute_tests("x = 1\n", "\n".join(tests))
    assert result["harness_error"] is True
    assert needle in " ".join(result["failures"])


def test_missing_solution_name_stays_a_learner_failure():
    # ImportError naming `solution` means the learner did not define what was asked — a real 0.
    result = execute_tests("def other():\n    pass\n", "\n".join(_VALID_TESTS))
    assert result.get("harness_error") is not True
    assert result["score"] == 0.0


def test_wrong_submission_against_valid_artifact_still_scores_low():
    result = execute_tests(_WRONG_SOLUTION, "\n".join(_VALID_TESTS))
    assert result.get("harness_error") is not True
    assert result["score"] < 40
    assert result["failures"]


def test_no_generated_tests_is_a_harness_error():
    assert execute_tests("x = 1\n", "")["harness_error"] is True


# --- generation-time validation -----------------------------------------------------------------

def test_check_test_artifact_rejects_the_broken_artifact():
    assert check_test_artifact(_BROKEN_TESTS) is not None


def test_check_test_artifact_accepts_a_self_contained_artifact():
    assert check_test_artifact(_VALID_TESTS) is None


def test_check_test_artifact_rejects_file_reading_tests():
    reason = check_test_artifact(["from solution import f",
                                  "def test_x():\n    f(pd.read_csv('data.csv'))\n"])
    assert reason is not None and "external file" in reason


class _FakeStructuredLLM:
    """Returns canned artifacts, one per invoke(), recording how many times it was asked."""

    def __init__(self, artifacts):
        self.artifacts = list(artifacts)
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return self.artifacts.pop(0)


def _artifact(unit_tests):
    from agents.exercise.grading_schema import GradingArtifact

    return GradingArtifact(format="coding_challenge", unit_tests=unit_tests, instructions="submit it")


def _grader():
    from agents.exercise.grader_agent import GraderAgent

    return GraderAgent.__new__(GraderAgent)  # no LLM/config construction


def test_grader_retries_once_and_returns_the_valid_artifact():
    grader = _grader()
    grader.structured_llm = _FakeStructuredLLM([_artifact(_BROKEN_TESTS), _artifact(_VALID_TESTS)])
    grader.chat_prompt = type("P", (), {"format_messages": staticmethod(lambda **k: [])})()

    artifact = grader.build_grading_artifact("q", "coding_challenge", "statement")
    assert artifact is not None and artifact.unit_tests == _VALID_TESTS
    assert grader.structured_llm.calls == 2


def test_grader_gives_up_rather_than_shipping_unrunnable_tests():
    grader = _grader()
    grader.structured_llm = _FakeStructuredLLM([_artifact(_BROKEN_TESTS), _artifact(_BROKEN_TESTS)])
    grader.chat_prompt = type("P", (), {"format_messages": staticmethod(lambda **k: [])})()

    assert grader.build_grading_artifact("q", "coding_challenge", "statement") is None


# --- mastery: a harness error is not a learner signal --------------------------------------------

def test_apply_score_ignores_harness_errors():
    ls = {"node_state": {"a": {"status": "available", "best_score": 0, "attempts": 0,
                               "weaknesses": [], "last_feedback": None,
                               "remediation_pending": False}}}
    apply_score(ls, "a", "coding_challenge", {"harness_error": True, "failures": ["boom"]})
    assert ls["node_state"]["a"] == {"status": "available", "best_score": 0, "attempts": 0,
                                     "weaknesses": [], "last_feedback": None,
                                     "remediation_pending": False}


# --- end to end through the service --------------------------------------------------------------

def _opened_with_artifact(monkeypatch, unit_tests):
    monkeypatch.setattr(svc, "_run_lesson", lambda *a, **k: ({
        "content": "c", "exercise_format": "coding_challenge", "exercise_statement": "s",
        "grading_artifact": {"format": "coding_challenge", "unit_tests": unit_tests},
    }, {"tokens_in": 1, "tokens_out": 1, "est_cost_usd": 0.0, "by_model": {}, "unknown": False}))
    ls = svc.new_learning_session({
        "route": "SCOUT",
        "skill_graph": {"summary": "P", "nodes": [{"id": "a", "label": "A", "description": "d",
                                                   "level": "foundational"}], "edges": []},
        "format_type": "B",
    }, "B")
    svc.open_lesson(ls, "a")
    return ls


def test_broken_harness_end_to_end_leaves_the_learner_untouched(monkeypatch):
    """The live failure, replayed: real executor, real mastery, real adaptation wiring."""
    adapt_calls = {"n": 0}
    monkeypatch.setattr(svc, "adapt_after_grade",
                        lambda ls, nid: adapt_calls.__setitem__("n", adapt_calls["n"] + 1) or [])
    ls = _opened_with_artifact(monkeypatch, _BROKEN_TESTS)

    ls, new_ids, result = svc.grade(ls, "a", "def prepare_and_fit(**kw):\n    return 1\n", exercise_revision=ls["lessons"]["a"]["exercise"]["revision"])

    assert result["harness_error"] is True
    state = ls["node_state"]["a"]
    assert state["status"] == "available"          # NOT needs_review
    assert state["remediation_pending"] is False   # NOT locked
    assert state["weaknesses"] == []               # no fabricated knowledge gap
    assert state["attempts"] == 0
    assert adapt_calls["n"] == 0                   # no remedial nodes injected
    assert new_ids == []
    stages = [e["stage"] for e in ls["events"]]
    assert "grade_failure" in stages
    assert "exercise_graded" not in stages         # keeps the false 0 out of the funnel


def test_wrong_submission_against_valid_artifact_still_remediates(monkeypatch):
    """The other half: real grading still punishes a genuinely wrong answer, exactly as designed."""
    adapt_calls = {"n": 0}
    monkeypatch.setattr(svc, "adapt_after_grade",
                        lambda ls, nid: adapt_calls.__setitem__("n", adapt_calls["n"] + 1) or [])
    ls = _opened_with_artifact(monkeypatch, _VALID_TESTS)

    ls, _new_ids, result = svc.grade(ls, "a", _WRONG_SOLUTION, exercise_revision=ls["lessons"]["a"]["exercise"]["revision"])

    assert result.get("harness_error") is not True
    assert result["score"] < 40
    state = ls["node_state"]["a"]
    assert state["status"] == "needs_review"
    assert state["remediation_pending"] is True
    assert adapt_calls["n"] == 1
    assert "exercise_graded" in [e["stage"] for e in ls["events"]]
