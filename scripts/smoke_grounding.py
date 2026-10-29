"""Three bounded fixture paths through real generation; never touches the running app's stores.

Uses configured Groq/Ollama and actual local retrieval/public research. Market scraping is
explicitly unavailable in this fixture run, exercising graceful degradation without paid job scraping.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update({"MINDMORPH_STORE": "memory", "MINDMORPH_RAG": "0", "MINDMORPH_RERANK": "1",
                   "MINDMORPH_WEB_RESEARCH": "1", "MINDMORPH_NEO4J_MIRROR": "0",
                   "MINDMORPH_RICH_CONTENT": "0",
                   "KAFKA_BOOTSTRAP_SERVERS": "", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                   "MINDMORPH_MODEL_CACHE": str(ROOT / "logs/model-cache")})

import fitz
from fastapi.testclient import TestClient
from api.main import app
from graph.learning_plan_graph import build_graph
from rag.grounding import _reranker
from rag.embeddings import get_embeddings
from services import learning_service as service

OUT = ROOT / "logs/p1-7-implementation"


class NoMarketScrape:
    scraper = None

    def __init__(self):
        self.scraper = self

    async def initialize(self):
        raise RuntimeError("Fixture run: market scraping deliberately unavailable")

    async def extract_job_title(self, query):
        return "Software developer"


def pdf(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 550, 700), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("all", "relevant", "no_upload", "unrelated"), default="all")
    requested = parser.parse_args().scenario
    get_embeddings().embed_query("Python list comprehensions")
    ranker = _reranker()
    if ranker is None:
        raise RuntimeError("Reranker unavailable")
    list(ranker.rerank("Python list comprehensions", ["Python comprehensions filter and transform lists."]))
    service._orchestration_graph = build_graph(market=NoMarketScrape())
    client = TestClient(app)
    report = OUT / "live-smoke.json"
    results = json.loads(report.read_text())["results"] if requested != "all" and report.exists() else []
    query = "Create a focused beginner learning roadmap for Python list comprehensions, including list iteration, transformation and filtering. Keep the scope small."
    fixture = "Python list comprehensions\nA list comprehension transforms each item of an iterable into a new list.\nSyntax: [expression for item in iterable if condition].\nFor positive squares: [n*n for n in numbers if n > 0].\nFor even values: [n for n in numbers if n % 2 == 0].\nTeach ordinary list iteration first, then transformation, then filtering."
    for scenario, material in [("relevant", fixture), ("no_upload", None),
                               ("unrelated", "Acoustic guitar chord practice. Change slowly between C major and G major chords. Keep four-beat strumming rhythm. Practice finger placement for clean chord transitions.")]:
        if requested not in ("all", scenario):
            continue
        user = "p1-7-smoke-" + scenario
        if material:
            response = client.post(f"/users/{user}/knowledge", files={"file": ("Fixture.pdf", pdf(material), "application/pdf")})
            assert response.status_code == 200, response.text
        started = time.perf_counter()
        request = {"user_id": user, "query": query, "format_type": "A", "primary_language": "python", "domain": "programming", "scope": "focused"}
        response = client.post("/sessions", json=request)
        if response.status_code == 503:
            print("Fixture planning rate-limited; waiting once before retry", flush=True)
            time.sleep(45)
            response = client.post("/sessions", json=request)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["learning_session"] and data["route"] == "SCOUT"
        state = data["learning_session"]
        (OUT / ("live-" + scenario + "-path.json")).write_text(json.dumps(data, indent=2), encoding="utf-8")
        personal = [p for p in state["grounding"]["passages"] if p["kind"] == "personal"]
        assert bool(personal) == (scenario == "relevant"), (scenario, state["grounding"])
        row = {"scenario": scenario, "session_id": data["session_id"], "planning_seconds": round(time.perf_counter() - started, 2),
               "node_count": len(state["skill_graph"]["nodes"]), "grounding_status": state["grounding"]["status"],
               "sources": state["grounding"]["sources"], "personal_passages": len(personal),
               "review_passed": state.get("review_passed")}
        if scenario == "relevant":
            from services.completion import locked_node_ids
            locked = locked_node_ids(state["skill_graph"], state["node_state"])
            node = next(n["id"] for n in state["skill_graph"]["nodes"] if n["id"] not in locked)
            path = f"/sessions/{user}/{data['session_id']}"
            response = client.post(path + f"/lessons/{node}")
            assert response.status_code == 200, response.text
            (OUT / "live-relevant-session.json").write_text(response.text, encoding="utf-8")
            lesson = response.json()["learning_session"]["lessons"][node]
            (OUT / "live-lesson-fixture.json").write_text(json.dumps(lesson, indent=2), encoding="utf-8")
            assert lesson["content"] and lesson["exercise"]["statement"]
            assert lesson["grounding"] == lesson["exercise"]["grounding"]
            revision = lesson["exercise"]["revision"]
            if not lesson["exercise"]["grading_artifact"]:
                print("Grading setup unavailable; waiting once before cached-artifact repair", flush=True)
                time.sleep(45)
            reopened = client.post(path + f"/lessons/{node}").json()["learning_session"]["lessons"][node]
            assert reopened["exercise"]["revision"] == revision
            assert reopened["exercise"]["grading_artifact"], "Grading setup remained unavailable after the bounded repair check"
            assert reopened["content"] == lesson["content"] and reopened["exercise"]["statement"] == lesson["exercise"]["statement"]
            reply = client.post(path + "/chat", json={"message": "Explain one simple example of this skill.", "node_id": node})
            assert reply.status_code == 200 and '"done": true' in reply.text, reply.text
            turn = client.get(path).json()["learning_session"]["chat"][-1]
            assert turn["role"] == "assistant" and turn["content"] and turn["grounding"]
            row.update(lesson_generated=True, exercise_generated=True, grading_artifact_generated=True,
                       saved_revision_reused=True, tutor_stream_and_saved_sources=True,
                       lesson_grounding_status=lesson["grounding"]["status"], tutor_grounding_status=turn["grounding"]["status"])
            (OUT / "live-lesson-fixture.json").write_text(json.dumps(lesson, indent=2), encoding="utf-8")
        results = [r for r in results if r["scenario"] != scenario] + [row]
        (OUT / "live-smoke.json").write_text(json.dumps({"results": results,
            "scope": "Real configured generation/local models/public research with synthetic uploaded text. Uses short Boost lessons and disables optional rich-content tail to bound calls. Market scraping deliberately unavailable; no running app state/database was used."}, indent=2), encoding="utf-8")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
