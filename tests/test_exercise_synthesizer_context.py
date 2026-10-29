"""Large retrieved sources must not prevent interactive exercise generation."""
from types import SimpleNamespace

from agents.exercise import exercise_synthesizer_agent as module


def test_large_references_fit_request_budget_without_losing_goal(monkeypatch):
    captured = []

    class BudgetedModel:
        def invoke(self, messages):
            prompt = "\n".join(message.content for message in messages)
            assert len(prompt) < 20000, "Retrieved sources exceeded the request budget"
            captured.append(prompt)
            return SimpleNamespace(content="Create a list and return its first item.")

    monkeypatch.setattr(module, "get_chat_model", lambda _tier: BudgetedModel())
    result = module.ExerciseSynthesizerAgent().synthesize(
        "Learn Python lists", "coding_challenge", exercise_language="python",
        github_material="Repository reference: " + "x" * 50000,
        blog_material="Tutorial reference: " + "y" * 50000,
        dataset_material="Dataset reference: " + "z" * 50000,
    )

    assert result == "Create a list and return its first item."
    assert "Learn Python lists" in captured[0]
    assert "Coding language: python" in captured[0]
    assert all(label in captured[0] for label in (
        "Repository reference:", "Tutorial reference:", "Dataset reference:",
    ))


def test_short_references_are_preserved(monkeypatch):
    captured = []

    class Model:
        def invoke(self, messages):
            captured.append(messages[-1].content)
            return SimpleNamespace(content="Practice exercise")

    monkeypatch.setattr(module, "get_chat_model", lambda _tier: Model())
    module.ExerciseSynthesizerAgent().synthesize(
        "Python lists", "coding_challenge", github_material="A complete short reference",
    )

    assert "A complete short reference" in captured[0]
    assert "(none found)" in captured[0]
    assert "omitted" not in captured[0]
