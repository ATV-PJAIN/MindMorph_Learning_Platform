import logging
from functools import lru_cache

from graph.skill_graph_adapt import prune_dangling_edges

logger = logging.getLogger(__name__)

_LEVEL_RANK = {"foundational": 0, "intermediate": 1, "advanced": 2}


def node_label_map(skill_graph: dict) -> dict:
    """Map node_id -> label for the skill picker (ids are unique; labels may repeat)."""
    return {n["id"]: n.get("label", n["id"]) for n in skill_graph.get("nodes", [])}


def ordered_node_ids(skill_graph: dict) -> list:
    nodes = skill_graph.get("nodes", [])
    return [
        n["id"]
        for n in sorted(nodes, key=lambda n: _LEVEL_RANK.get((n.get("level") or "").lower(), 1))
    ]


@lru_cache(maxsize=256)
def _warn_dangling(dropped: tuple) -> None:
    logger.warning(
        "Skill graph loaded with %d edge(s) referencing unknown node ids: %s — ignored at read time "
        "(stored session left as-is; see graph/skill_graph_adapt.prune_dangling_edges)",
        len(dropped), list(dropped),
    )


def prereqs_by_node(skill_graph: dict) -> dict:
    graph, dropped = prune_dangling_edges(skill_graph)
    if dropped:
        _warn_dangling(tuple(sorted((str(e.get("source")), str(e.get("target"))) for e in dropped)))
    prereqs: dict = {n["id"]: set() for n in graph.get("nodes", [])}
    for e in graph.get("edges", []) or []:
        src, tgt = e.get("source"), e.get("target")
        if tgt in prereqs and src is not None:
            prereqs[tgt].add(src)
    return prereqs


# PHASE 3: Neo4j Cache for Completion and Locks
def _get_neo4j_state(skill_graph: dict, node_state: dict) -> tuple[set, set]:
    """Compatibility name: saved session state is always authoritative, including outages."""
    return _fallback_state(skill_graph, node_state)


def _fallback_state(skill_graph: dict, node_state: dict) -> tuple[set, set]:
    """Derive authoritative completion/locks from the saved session graph and progress."""
    prereqs = prereqs_by_node(skill_graph)

    def _is_mastered(nid: str) -> bool:
        return node_state.get(nid, {}).get("status") == "mastered"

    memo: dict = {}
    visiting: set = set()

    def _complete(nid: str) -> bool:
        if nid in memo:
            return memo[nid]
        if nid in visiting:
            return False
        if not _is_mastered(nid):
            memo[nid] = False
            return False
        visiting.add(nid)
        result = all(_complete(p) for p in prereqs.get(nid, set()))
        visiting.discard(nid)
        memo[nid] = result
        return result

    completed = {nid for nid in prereqs if _complete(nid)}
    
    locked = set()
    derived = {nid for nid, ps in prereqs.items() if any(p not in completed for p in ps)}
    locked.update(derived)
    
    for nid, st in node_state.items():
        if not st.get("remediation_pending"):
            continue
        ps = prereqs.get(nid, set())
        satisfied = bool(ps) and all(p in completed for p in ps)
        if not satisfied:
            locked.add(nid)

    return completed, locked


def complete_node_ids(skill_graph: dict, node_state: dict, session_id: str = None) -> set:
    completed, _ = _get_neo4j_state(skill_graph, node_state)
    return completed


def is_session_complete(skill_graph: dict, node_state: dict, session_id: str = None) -> bool:
    nodes = skill_graph.get("nodes", [])
    if not nodes:
        return False
    all_ids = {n["id"] for n in nodes}
    return complete_node_ids(skill_graph, node_state, session_id) >= all_ids


def locked_node_ids(skill_graph: dict, node_state: dict, session_id: str = None) -> set:
    _, locked = _get_neo4j_state(skill_graph, node_state)
    return locked


def incomplete_prereq_labels(skill_graph: dict, node_state: dict, node_id: str) -> list:
    completed, _ = _get_neo4j_state(skill_graph, node_state)
    label_by_id = node_label_map(skill_graph)
    prereqs = prereqs_by_node(skill_graph).get(node_id, set())
    return [label_by_id.get(p, p) for p in prereqs if p not in completed]


def display_status(skill_graph: dict, node_state: dict) -> dict:
    completed, locked = _get_neo4j_state(skill_graph, node_state)
    out: dict = {}
    for nid, s in node_state.items():
        if nid in completed:
            out[nid] = "mastered"
        elif nid in locked:
            out[nid] = "blocked"
        else:
            out[nid] = s.get("status")
    return out

