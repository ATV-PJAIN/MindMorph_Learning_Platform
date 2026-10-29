"""AI Teaching Assistant (P3 #10): grounded message build + streaming chat endpoint + persistence.

No real LLM — a fake agent supplies the token stream; embeddings/RAG are monkeypatched."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

import services.learning_service as svc
from agents.tutor.tutor_agent import TutorAgent


# --- build_messages (pure) ----------------------------------------------------------------------

def test_build_messages_grounds_and_orders():
    msgs = TutorAgent(model=object()).build_messages(
        skill_label="Recursion",
        lesson_content="LESSON_BODY",
        rag_context="USER_MATERIAL",
        history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        question="what is a base case?",
    )
    assert isinstance(msgs[0], SystemMessage)
    sys_text = msgs[0].content
    assert "Recursion" in sys_text and "LESSON_BODY" in sys_text and "USER_MATERIAL" in sys_text
    assert isinstance(msgs[-1], HumanMessage) and msgs[-1].content == "what is a base case?"
    assert len(msgs) == 1 + 2 + 1  # system + 2 history + question


# --- build_tutor_messages (service) -------------------------------------------------------------

class _FakeStore:
    is_empty = False

    def retrieve(self, q, k=4):
        return "RETRIEVED_CTX"

    def search(self, q, k=4, source_ids=None):
        return [{"id": "test", "text": "RETRIEVED_CTX", "source": "notes.pdf", "score": 0.9}]


def test_build_tutor_messages_pulls_lesson_and_rag(monkeypatch):
    monkeypatch.setattr("rag.registry.get_user_store", lambda uid: _FakeStore())
    ls = {
        "skill_graph": {"nodes": [{"id": "a", "label": "Skill A"}]},
        "lessons": {"a": {"content": "LESSON_A"}},
        "chat": [],
    }
    msgs = svc.build_tutor_messages(ls, "a", "explain it", "u1")
    sys_text = msgs[0].content
    assert "Skill A" in sys_text and "LESSON_A" in sys_text and "RETRIEVED_CTX" in sys_text


# --- streaming endpoint -------------------------------------------------------------------------

class _FakeAgent:
    def build_messages(self, **kw):
        return []

    async def astream(self, messages):
        for t in ["Hel", "lo ", "wor", "ld"]:
            yield t


class _BoomAgent:
    def build_messages(self, **kw):
        return []

    async def astream(self, messages):
        raise RuntimeError("llm provider down")
        yield  # pragma: no cover (makes this an async generator)


def _client(monkeypatch, agent):
    monkeypatch.setenv("MINDMORPH_STORE", "memory")
    monkeypatch.setattr(svc, "_get_tutor_agent", lambda: agent)
    monkeypatch.setattr("api.routes.build_tutor_messages", lambda *a, **k: [])
    from fastapi.testclient import TestClient
    from api.main import app
    from persistence.repository import get_default_repository

    return TestClient(app), get_default_repository()


def test_chat_streams_and_persists(monkeypatch):
    client, repo = _client(monkeypatch, _FakeAgent())
    repo.save("u1", "s1", {"skill_graph": {"nodes": []}, "lessons": {}, "chat": []}, title="t")

    r = client.post("/sessions/u1/s1/chat", json={"message": "hi", "node_id": None})
    assert r.status_code == 200
    body = r.text
    assert '"token"' in body and "Hello world" in body.replace('"', "") or "Hel" in body
    assert '"done": true' in body or '"done":true' in body

    chat = repo.get("u1", "s1")["chat"]
    assert chat[0] == {"role": "user", "content": "hi"}
    assert chat[1]["role"] == "assistant" and chat[1]["content"] == "Hello world"


def test_chat_works_on_session_without_chat_key(monkeypatch):
    client, repo = _client(monkeypatch, _FakeAgent())
    repo.save("u1", "s2", {"skill_graph": {"nodes": []}, "lessons": {}}, title="t")  # NO chat key

    r = client.post("/sessions/u1/s2/chat", json={"message": "yo"})
    assert r.status_code == 200
    assert repo.get("u1", "s2")["chat"][0]["content"] == "yo"


def test_chat_error_persists_user_message(monkeypatch):
    client, repo = _client(monkeypatch, _BoomAgent())
    repo.save("u1", "s3", {"skill_graph": {"nodes": []}, "lessons": {}, "chat": []}, title="t")

    r = client.post("/sessions/u1/s3/chat", json={"message": "q"})
    assert r.status_code == 200
    assert '"error"' in r.text
    chat = repo.get("u1", "s3")["chat"]
    assert chat == [{"role": "user", "content": "q"}]  # user kept, no assistant turn
