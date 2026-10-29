"""Small labelled real local-model evaluation; does not contact a hosted generation model."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update({"MINDMORPH_STORE": "memory", "MINDMORPH_RAG": "0", "MINDMORPH_WEB_RESEARCH": "0",
                   "MINDMORPH_RERANK": "1", "MINDMORPH_NEO4J_MIRROR": "0", "KAFKA_BOOTSTRAP_SERVERS": "",
                   "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                   "MINDMORPH_MODEL_CACHE": str(ROOT / "logs/model-cache")})

from rag import registry
from rag.embeddings import get_embeddings
from rag.grounding import retrieve_evidence, _reranker

FIXTURES = [
    ("sql", "SQL LEFT JOIN returns every row from the left table, including unmatched customers. Missing right-table values are NULL. INNER JOIN returns only rows matching the join condition in both tables."),
    ("python", "Python list comprehensions create a new list from an iterable. Use [x * x for x in numbers if x > 0] to square positive numbers and filter out zero or negative inputs."),
    ("guitar", "For acoustic guitar practice, change between open C major and G major chords slowly. Keep a steady four-beat strumming rhythm, move fingers together, and increase tempo after clean chord transitions."),
    ("gardening", "Compost kitchen vegetable scraps with dry leaves. Turn the garden compost to add air. Balance moist green plant waste and dry brown matter; avoid waterlogged compost piles."),
]
QUERIES = [
    ("SQL left join keeping unmatched customers", "sql"),
    ("Python list comprehension filtering positive numbers", "python"),
    ("acoustic guitar changing open C and G chords with rhythm", "guitar"),
    ("gardening compost vegetable scraps and dry leaves", "gardening"),
    ("sourdough fermentation temperature and bread starter hydration", None),
    ("orbital mechanics Kepler elliptical planetary orbits", None),
    ("Create a focused beginner learning roadmap for Python list comprehensions, including list iteration, transformation and filtering. Keep the scope small.", "python"),
]


def main():
    started = time.monotonic()
    embeddings = get_embeddings()
    ranker = _reranker()
    if ranker is None:
        raise RuntimeError("Real local reranker unavailable; quality acceptance is outstanding")
    list(ranker.rerank("SQL joins", [FIXTURES[0][1]], batch_size=1))
    warmup = time.monotonic() - started
    store = registry.get_user_store("quality-fixtures", create=True)
    store.add_texts([text for _, text in FIXTURES], [{"source": label + ".pdf", "source_id": label, "page": 1} for label, _ in FIXTURES])
    results = []
    for query, expected in QUERIES:
        result = retrieve_evidence(query, "quality-fixtures")
        first = result["passages"][0]["source_id"] if result["passages"] else None
        passed = first == expected
        results.append({"query": query, "expected_top_source": expected, "actual_top_source": first,
                        "passed": passed, "status": result["status"], "sources": result["sources"],
                        "scores": [{"source": p["source_id"], "score": p["score"]} for p in result["passages"]],
                        "elapsed_ms": result["elapsed_ms"]})
    record = {"embedding": "BAAI/bge-small-en-v1.5", "reranker": "Xenova/ms-marco-MiniLM-L-6-v2",
              "warmup_seconds": round(warmup, 2), "fixture_count": len(FIXTURES), "results": results,
              "scope": "Seven labelled local queries, including coding, music, gardening, a verbose planning goal and two no-match controls. This is bounded sample evidence, not broad retrieval accuracy."}
    output = ROOT / "logs/p1-7-implementation/retrieval-quality.json"
    output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)
    assert all(row["passed"] for row in results), "A labelled retrieval/no-match control failed"


if __name__ == "__main__":
    main()
