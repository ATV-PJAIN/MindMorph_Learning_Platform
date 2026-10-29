"use client";

import {
  Background,
  Controls,
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";
import {
  FOCUS_PADDING_X,
  FOCUS_PADDING_Y,
  focusFrameIds,
  layoutSkillGraph,
  NODE_HEIGHT,
  NODE_WIDTH,
  type NodeBox,
} from "@/lib/graphLayout";
import { STATUS_STYLE, displayStatus, incompletePrereqLabels, lockedNodeIds } from "@/lib/status";
import type { LearningSession, NodeStatus } from "@/lib/types";

// Stagger window between two newly-inserted nodes' entrances, and the hand-off point from the
// entrance animation to the pulse-glow ring that follows it (must match `skill-node-enter`'s
// duration in globals.css).
const ENTER_STAGGER_MS = 140;
const ENTER_DURATION_MS = 580;

type SkillNodeData = {
  label: string;
  status: NodeStatus;
  locked: boolean;
  // Non-empty only when `locked` — the unmet-prerequisite labels, so a screen reader user gets the
  // reason instead of just "locked" (mirrors the sighted 🔒 + dimmed-card signal, which is otherwise
  // colour/opacity-only).
  lockedReason: string;
  selected: boolean;
  isNew: boolean;
  enterDelayMs: number;
  isStartHere?: boolean;
  // The latest `onOpen` from the owning SkillGraph instance, read (never written) inside the node's
  // own keydown handler — a ref so the click-through path stays identical to mouse activation
  // (`onNodeClick` below) without this node's `data` needing a fresh closure every render.
  onOpenRef: { current: (nodeId: string, locked: boolean) => void };
};

// A custom node: surface card with a status dot + glow. Locked nodes are dimmed and non-interactive.
// `isNew` gets a one-shot entrance + pulse-glow animation on THIS inner card — never on the
// `.react-flow__node` wrapper xyflow renders around it, which already carries its own position
// transition (globals.css). `enterDelayMs` staggers multiple same-render insertions so they don't all
// pop in on the same frame; the glow is chained to start right as the entrance settles.
function SkillFlowNode({ id, data }: NodeProps<Node<SkillNodeData>>) {
  const style = STATUS_STYLE[data.status];
  // Accessible name folds in status (and, if locked, the reason) so a screen reader user gets the
  // same information the glyph + dimming + 🔒 convey visually — never a colour/opacity-only signal.
  const accessibleName = `${data.isStartHere ? "Start Here. " : ""}${data.label}. ${style.label}${data.locked && data.lockedReason ? `. Requires: ${data.lockedReason}` : ""}`;

  return (
    <div
      className={`surface accent-ring relative w-64 px-4 py-3 ${data.isNew ? "skill-node-enter" : ""}`}
      style={{
        // 0.7 (not the original 0.45): at 0.45 the locked label's already-muted colour composited
        // against the ink surface fell below WCAG AA (~3:1) for its 11px size — see globals.css
        // `--color-blocked`, raised alongside this so the pair clears 4.5:1 while staying visibly
        // dimmed relative to an unlocked card.
        opacity: data.locked ? 0.7 : 1,
        borderColor: data.selected
          ? "var(--color-gold)"
          : data.isStartHere
          ? "var(--color-gold)"
          : undefined,
        boxShadow: data.selected
          ? "0 0 0 1px var(--color-gold), 0 0 28px -6px var(--color-gold)"
          : data.isStartHere
          ? "0 0 0 1px var(--color-gold), 0 0 22px -4px var(--color-gold)"
          : data.status === "mastered"
          ? `0 0 24px -8px ${style.color}`
          : undefined,
        cursor: data.locked ? "not-allowed" : "pointer",
        ...(data.isNew
          ? { animationDelay: `${data.enterDelayMs}ms, ${data.enterDelayMs + ENTER_DURATION_MS}ms` }
          : null),
      }}
      // React Flow's own wrapper focus/keydown is disabled (`nodesFocusable={false}` below) because
      // its Enter/Space handler only updates internal selection state, never this app's onNodeClick —
      // a keyboard user could tab to a card and press Enter and nothing would happen. This element
      // owns focus and activation itself instead; the mouse path (`onNodeClick` on <ReactFlow>) is
      // untouched, so it stays exactly what the e2e suite exercises.
      tabIndex={0}
      role="button"
      aria-label={accessibleName}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          data.onOpenRef.current(id, data.locked);
        }
      }}
    >
      {data.isStartHere && (
        <span
          className="absolute -top-3 left-4 z-10 flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[10px] font-bold tracking-wider shadow-lg"
          style={{
            background: "var(--color-gold)",
            color: "#0f172a",
          }}
        >
          <span>👉</span> START HERE
        </span>
      )}
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px]"
          style={{ background: `${style.color}22`, color: style.color }}
        >
          {style.glyph}
        </span>
        {/* No `truncate`: overflow is measured in pre-transform CSS pixels, so an ellipsis added at
            zoom 1 stays ellipsised however far the camera zooms in. Real skill names ("Advanced List
            Comprehensions") need two lines at this width; clamp at two so the card height stays the
            fixed box graphLayout reserves in dagre. */}
        <span className="line-clamp-2 text-sm font-medium leading-snug text-text-strong">
          {data.label}
        </span>
      </div>
      <p className="mt-1 text-[11px]" style={{ color: style.color }}>
        {style.label}
      </p>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

const nodeTypes = { skill: SkillFlowNode };

export function SkillGraph({
  session,
  onOpen,
  focusNodeIds,
}: {
  session: LearningSession;
  onOpen: (nodeId: string, locked: boolean) => void;
  // Ids the caller wants the camera drawn to right now (the authoritative signal is the grade
  // response's `new_node_ids` — a rewire that happened off-screen otherwise goes unnoticed since the
  // graph sits above the lesson panel the learner is scrolled down to).
  focusNodeIds?: string[];
}) {
  // Ids/edge-keys seen on a prior render, so a mid-session remedial node or edge (added by an LLM
  // adaptation after a sub-40 grade) gets its entrance animation exactly once instead of on every
  // recompute.
  const seenNodeIds = useRef<Set<string>>(new Set());
  const seenEdgeKeys = useRef<Set<string>>(new Set());
  const containerRef = useRef<HTMLDivElement>(null);
  const rfInstanceRef = useRef<ReactFlowInstance<Node<SkillNodeData>, Edge> | null>(null);
  // Read inside each card's keydown handler, written here after every render — keeps `onOpen`'s
  // ever-changing identity (an inline arrow in the caller) out of the nodes/edges useMemo below.
  const onOpenRef = useRef(onOpen);
  useEffect(() => {
    onOpenRef.current = onOpen;
  }, [onOpen]);

  // Same invariant lib/status.ts:prereqsByNode and the backend's prune_dangling_edges enforce: an
  // edge naming a node that doesn't exist (a hallucinated `source` from an LLM adaptation) is not
  // part of the graph. Neither consumer below breaks on one today — layoutSkillGraph filters
  // internally and React Flow renders a dangling edge as null — but they should all see the same
  // edge list, and this keeps the phantom id out of the focus frame and the seen-edge bookkeeping.
  // Keyed on skill_graph alone so a node_state-only update can't re-fire the camera effect.
  const graphEdges = useMemo(() => {
    const ids = new Set(session.skill_graph.nodes.map((n) => n.id));
    return (session.skill_graph.edges ?? []).filter(
      (e) => ids.has(e.source) && ids.has(e.target),
    );
  }, [session.skill_graph]);

  const { nodes, edges } = useMemo(() => {
    const graph = session.skill_graph;
    const status = displayStatus(graph, session.node_state);
    const locked = lockedNodeIds(graph, session.node_state);

    // Read-only diff against the refs — StrictMode may invoke this factory twice per commit, and a
    // useMemo factory must stay pure, so the refs are updated in the effect below instead of here.
    const seenNodes = seenNodeIds.current;
    const seenEdges = seenEdgeKeys.current;
    // Preserve graph order so multiple insertions in one adaptation stagger in a stable sequence.
    const newIdOrder = graph.nodes.filter((n) => !seenNodes.has(n.id)).map((n) => n.id);
    const enterDelayByNode = new Map(newIdOrder.map((id, i) => [id, i * ENTER_STAGGER_MS]));

    // Edge-aware layered layout (dagre): position by the prereq DAG, so remedial prerequisite nodes
    // added mid-session render left of the node they unlock and the graph reflows automatically.
    const { positions } = layoutSkillGraph(graph.nodes, graphEdges);

    // Identify the "Start Here" node: the primary root unlocked node that is not yet mastered.
    const incomingEdgeTargets = new Set(graphEdges.map((e) => e.target));
    const startCandidates = graph.nodes.filter(
      (n) => !locked.has(n.id) && status[n.id] !== "mastered"
    );
    const startNode =
      startCandidates.find((n) => !incomingEdgeTargets.has(n.id)) || startCandidates[0];
    const startNodeId = startNode ? startNode.id : null;

    const rfNodes: Node<SkillNodeData>[] = graph.nodes.map((n) => {
      return {
        id: n.id,
        type: "skill",
        position: positions[n.id] ?? { x: 0, y: 0 },
        data: {
          label: n.label,
          status: status[n.id] ?? "available",
          locked: locked.has(n.id),
          lockedReason: locked.has(n.id)
            ? incompletePrereqLabels(graph, session.node_state, n.id).join(", ")
            : "",
          selected: session.selected_node === n.id,
          isNew: enterDelayByNode.has(n.id),
          enterDelayMs: enterDelayByNode.get(n.id) ?? 0,
          isStartHere: startNodeId === n.id,
          onOpenRef,
        },
      };
    });

    const rfEdges: Edge[] = graphEdges.map((e, i) => {
      const key = `${e.source}->${e.target}`;
      return {
        id: `e${i}`,
        source: e.source,
        target: e.target,
        animated: false,
        className: seenEdges.has(key) ? undefined : "skill-edge-enter",
        style: { stroke: "oklch(60% 0.02 270 / 0.5)" },
      };
    });

    return { nodes: rfNodes, edges: rfEdges };
  }, [session, graphEdges]);

  // Mark this render's ids/edge-keys as seen AFTER commit, so the next diff excludes them. Runs
  // post-commit (not during render), so StrictMode's dev double-invoke is harmless — Set.add is
  // idempotent.
  useEffect(() => {
    const seenNodes = seenNodeIds.current;
    for (const n of nodes) seenNodes.add(n.id);
    const seenEdges = seenEdgeKeys.current;
    for (const e of edges) seenEdges.add(`${e.source}->${e.target}`);
  }, [nodes, edges]);

  // Camera cue: a grade that grew the graph shouldn't rewire off-screen while the learner is still
  // scrolled down at the editor. Scroll the graph into view, then frame the new node(s) plus whatever
  // they attach to, so the rewire reads as a connected change rather than an isolated pop-in.
  useEffect(() => {
    if (!focusNodeIds || focusNodeIds.length === 0) return;
    const container = containerRef.current;
    if (!container) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Reduced-motion users still land on the right scroll position — just without the smooth glide.
    container.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });

    const instance = rfInstanceRef.current;
    if (!instance) return;
    // How many hops of context to frame is decided from the real layout, not a fixed number — see
    // focusFrameIds. Positions come from the instance rather than the nodes useMemo so this effect
    // doesn't re-fire (and yank the camera) on every status change; they're also React Flow's own
    // measured boxes, so the bbox matches what fitView will actually compute.
    const boxes: Record<string, NodeBox> = {};
    for (const n of instance.getNodes()) {
      boxes[n.id] = {
        x: n.position.x,
        y: n.position.y,
        // Fall back to the box dagre reserved, for the frame before React Flow has measured.
        width: n.measured?.width || NODE_WIDTH,
        height: n.measured?.height || NODE_HEIGHT,
      };
    }
    const focusIds = focusFrameIds(
      focusNodeIds,
      graphEdges,
      boxes,
      { width: container.clientWidth, height: container.clientHeight },
    );
    const run = () =>
      instance.fitView({
        nodes: focusIds.map((id) => ({ id })),
        // maxZoom matters as much as padding: without it a two-or-three-node focus set zooms past 1.8
        // and clips the very node the new ones attach to, so the rewire reads as an unrelated card
        // instead of a change to the map. Cap it near the mount-time framing. Padding is in pixels
        // and matches what focusFrameIds budgeted for when it sized the set.
        padding: { x: `${FOCUS_PADDING_X}px`, y: `${FOCUS_PADDING_Y}px` },
        maxZoom: 1.1,
        duration: reduceMotion ? 0 : 900,
      });

    if (reduceMotion) {
      run();
      return;
    }
    // The same commit that triggers this cue unmounts the lesson panel and its Monaco editor. Starting
    // the camera tween inside that blocked frame eats most of its duration and lands as a snap, so let
    // the re-render settle first.
    const timer = window.setTimeout(run, 150);
    return () => window.clearTimeout(timer);
  }, [focusNodeIds, graphEdges]);

  return (
    <div ref={containerRef} className="surface h-[420px] overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1.5 }}
        onInit={(instance) => {
          rfInstanceRef.current = instance;
        }}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onOpen(node.id, (node.data as SkillNodeData).locked)}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        // React Flow's own node focus/keydown is Tab-reachable but Enter/Space only updates its
        // internal selection state, never `onNodeClick` — a silent keyboard dead end. The card itself
        // (SkillFlowNode) now owns tabIndex/role/aria-label/keydown, so this wrapper-level focus
        // handling is turned off rather than stacking a second, non-functional tab stop per node.
        nodesFocusable={false}
      >
        <Background color="oklch(40% 0.02 270 / 0.4)" gap={22} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
