# Local code knowledge graph

Graphify maps this repository for code navigation. It is separate from the learner roadmap and progress graph in Neo4j.

Use the project's `mindmorph` conda environment. The official package is `graphifyy` (the executable is `graphify`); it is included in `requirements.txt`.

```powershell
conda run -n mindmorph graphify update .
conda run -n mindmorph graphify export wiki
```

`update` performs local AST extraction and regenerates `graphify-out/graph.json`, `GRAPH_REPORT.md` and `graph.html`. The wiki export regenerates `graphify-out/wiki/index.md` and its articles. These commands require no LLM API calls. Prompt files containing only constants may have no AST symbols. The graph does not replace reading a prompt when its exact wording matters.

Read `graphify-out/GRAPH_REPORT.md` first, then use the wiki and scoped queries:

```powershell
conda run -n mindmorph graphify explain retrieve_evidence
conda run -n mindmorph graphify query "uploaded material and lesson generation" --budget 2000
conda run -n mindmorph graphify path ingest_pdf retrieve_evidence --undirected
```

Run the refresh after source changes and export the wiki again. Generated output is Git-ignored and can be recreated. `.graphifyignore` excludes secrets, local backups/logs, dependencies and generated builds. Do not use a full semantic extraction or push the code graph into the learner Neo4j database as part of this local refresh.
