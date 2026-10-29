"""Fourth research branch: retrieve evidence without an additional rewriting model call."""
from rag.grounding import retrieve_evidence
from services.generation_privacy import private_generation


class KnowledgeGroundingAgent:
    @private_generation
    def gather(self, query: str, user_id: str | None = None, *, public_query: str | None = None) -> dict:
        # Research instructions are useful for candidate discovery but are a poor
        # relevance question for a passage reranker. Anchor planning rank to the goal.
        return retrieve_evidence(query, user_id, public_query=public_query,
                                  ranking_query=public_query or query)
