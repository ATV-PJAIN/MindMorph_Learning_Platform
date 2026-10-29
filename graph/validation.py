"""Structural checks independent of an AI reviewer's wording."""


def validate_skill_graph(graph: dict) -> dict:
    nodes = graph.get("nodes") or []
    if not nodes:
        raise ValueError("The roadmap has no skills. Please generate it again.")
    ids = [n.get("id") for n in nodes]
    if any(not isinstance(n, str) or not n.strip() for n in ids) or len(set(ids)) != len(ids):
        raise ValueError("The roadmap contains missing or duplicate skill identifiers.")
    if any(n.get("level") not in (None, "foundational", "intermediate", "advanced") for n in nodes):
        raise ValueError("The roadmap contains an invalid skill level.")
    children = {nid: set() for nid in ids}
    incoming = dict.fromkeys(ids, 0)
    for edge in graph.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if source not in children or target not in children or source == target:
            raise ValueError("The roadmap contains an invalid prerequisite.")
        if target not in children[source]:
            children[source].add(target)
            incoming[target] += 1
    ready = [nid for nid, degree in incoming.items() if degree == 0]
    count = 0
    while ready:
        count += 1
        for child in children[ready.pop()]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if count != len(ids):
        raise ValueError("The roadmap contains a prerequisite cycle. Please generate it again.")
    return graph
