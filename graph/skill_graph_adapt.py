"""Phase 3 — deterministic application of a GraphAdaptation to a SkillGraph (no LLM).

The Adaptation agent proposes additive changes; this module applies them under a hard invariant:
adaptation may only ADD new nodes/edges — never rename or delete an existing node id (mastery state
and cached lessons key off node_id). The merge is immutable: it builds a brand-new graph dict and
never mutates the input.
"""
from typing import Any, Dict, List, Optional, Tuple


def _as_dict(obj: Any) -> dict:
    """Normalize a pydantic model or dict to a plain dict."""
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return obj
    return {}


def _edge_key(edge: dict) -> tuple:
    return (edge.get("source"), edge.get("target"), edge.get("relation") or "prerequisite")


def _reaches(edges_by_source: Dict[str, list], start: str, target: str) -> bool:
    """DFS reachability over the merged edge set (cycle-safe via a visited set)."""
    seen: set = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges_by_source.get(cur, ()))
    return False


def _synthesize_inbound_edges(
    merged_edges: List[dict], seen_edges: set, new_node_ids: List[str], graded_node_id: str,
) -> List[dict]:
    """Guarantee every newly added node has a path INTO the graded node.

    The remediation contract is: new nodes are remedial sub-skills, and the graded node stays locked
    until they exist AND are complete (services/completion.py:_remediation_locked). A model that omits
    the inbound edge (or only chains new nodes to each other, e.g. ``setup -> verify`` with neither
    pointing at the graded node) would otherwise lock the node with no completable path — a permanent
    dead end for the learner.

    For a chain of new nodes, only the END of the chain (the node with no outgoing edge to another new
    node — i.e. the "sink") gets wired to the graded node; its predecessors already reach the graded
    node transitively once the sink is connected, so wiring every node individually would add redundant
    edges. Multiple independent new nodes (no edges between them) are each their own one-node chain, so
    each gets its own inbound edge — the desired "N direct prerequisites" shape.
    """
    edges_by_source: Dict[str, list] = {}
    for e in merged_edges:
        edges_by_source.setdefault(e.get("source"), []).append(e.get("target"))

    dangling = [
        nid for nid in new_node_ids
        if nid != graded_node_id and not _reaches(edges_by_source, nid, graded_node_id)
    ]
    if not dangling:
        return []

    dangling_set = set(dangling)
    sinks = [
        nid for nid in dangling
        if not any(t in dangling_set for t in edges_by_source.get(nid, ()))
    ]
    if not sinks:
        # Degenerate case: a cycle among the new nodes leaves no sink. Fall back to wiring every
        # dangling node directly — still additive-only and still guarantees a completable path.
        sinks = dangling

    synthesized: List[dict] = []
    for nid in sinks:
        edge = {"source": nid, "target": graded_node_id, "relation": "prerequisite"}
        key = _edge_key(edge)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        synthesized.append(edge)
    return synthesized


def apply_adaptation(
    skill_graph: Any, adaptation: Any, graded_node_id: Optional[str] = None,
) -> Tuple[dict, List[str]]:
    """Merge an adaptation into a skill graph, returning (new_graph, new_node_ids).

    - Immutable: the input graph is never mutated; a new dict with new node/edge lists is returned.
    - Additive only: existing node ids are preserved; a proposed new node whose id already exists is
      dropped (the invariant — never overwrite an existing node).
    - Dedup: edges already present (same source/target/relation) are not duplicated.
    - When ``graded_node_id`` is given (the sub-40 remediation path — see services/completion.py's
      deterministic lock), any newly added node left with no path into the graded node gets one
      synthesized deterministically (see ``_synthesize_inbound_edges``). Without ``graded_node_id``
      (unlock-edge / no-node adaptations, and existing callers/tests that don't pass it) behavior is
      unchanged.

    new_node_ids is the list of ids that were actually added, so the caller can seed node_state.
    """
    graph = _as_dict(skill_graph)
    adapt = _as_dict(adaptation)

    existing_nodes = [dict(n) for n in (graph.get("nodes") or [])]
    existing_ids = {n.get("id") for n in existing_nodes}

    merged_nodes = list(existing_nodes)
    new_node_ids: List[str] = []
    for proposed in adapt.get("new_nodes") or []:
        node = _as_dict(proposed)
        nid = node.get("id")
        # Invariant: only ADD — never overwrite an existing id, and never add the same new id twice.
        if not nid or nid in existing_ids:
            continue
        existing_ids.add(nid)
        merged_nodes.append(node)
        new_node_ids.append(nid)

    existing_edges = [dict(e) for e in (graph.get("edges") or [])]
    seen_edges = {_edge_key(e) for e in existing_edges}

    merged_edges = list(existing_edges)
    for proposed in adapt.get("new_edges") or []:
        edge = _as_dict(proposed)
        key = _edge_key(edge)
        # Skip duplicates and edges that reference a node id present in neither the old graph
        # nor the just-added nodes (a hallucinated endpoint would orphan the edge).
        if key in seen_edges:
            continue
        if edge.get("source") not in existing_ids or edge.get("target") not in existing_ids:
            continue
        seen_edges.add(key)
        merged_edges.append(edge)

    if graded_node_id and graded_node_id in existing_ids and new_node_ids:
        merged_edges.extend(
            _synthesize_inbound_edges(merged_edges, seen_edges, new_node_ids, graded_node_id)
        )

    new_graph = {**graph, "nodes": merged_nodes, "edges": merged_edges}
    from graph.validation import validate_skill_graph
    validate_skill_graph(new_graph)
    return new_graph, new_node_ids


def prune_dangling_edges(skill_graph: Any) -> Tuple[dict, List[dict]]:
    """Drop edges whose source or target is not in the node list. Returns (new_graph, dropped_edges).

    Same invariant ``apply_adaptation`` already enforces on adaptations, hoisted so the INITIAL
    Consensus graph gets it too: a hallucinated endpoint (e.g. an edge from ``mlops_tools`` when no
    such node exists) lands in ``services/completion.py:prereqs_by_node`` as a prerequisite that can
    never be mastered, permanently locking its dependants and making ``is_session_complete``
    unreachable. Dropping is deterministic and cannot make the graph worse: a bad TARGET is already
    ignored by ``prereqs_by_node`` (no-op beyond cleaning the render), and a bad SOURCE is only ever
    an unsatisfiable gate. A node left with zero prerequisites is correctly available.

    Immutable: the input graph is never mutated. The caller logs the drops (it has query context).
    """
    graph = _as_dict(skill_graph)
    node_ids = {n.get("id") for n in (graph.get("nodes") or [])}

    kept: List[dict] = []
    dropped: List[dict] = []
    for e in graph.get("edges") or []:
        edge = _as_dict(e)
        (kept if edge.get("source") in node_ids and edge.get("target") in node_ids else dropped).append(edge)

    if not dropped:
        return graph, []
    return {**graph, "edges": kept}, dropped
