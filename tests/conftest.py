"""Test-wide setup. Set before any test imports `config`."""
import os

# Default the §6.3 content tail OFF for the suite so building a content graph never fires the real
# example/visual LLM agents. test_content_tail.py opts back in (monkeypatch) to exercise the tail.
os.environ.setdefault("MINDMORPH_RICH_CONTENT", "0")
# Network/model quality checks opt in explicitly in their own isolated runner.
os.environ.setdefault("MINDMORPH_WEB_RESEARCH", "0")
os.environ.setdefault("MINDMORPH_RERANK", "0")
os.environ.setdefault("MINDMORPH_NEO4J_MIRROR", "0")
