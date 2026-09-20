import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from 'react';
import { MarkdownContent, cn } from '@niuulabs/ui';
import type {
  WorkflowExecutionChild,
  WorkflowExecutionEdge,
  WorkflowExecutionEvidence,
  WorkflowExecutionEvidenceContext,
  WorkflowExecutionEvent,
  WorkflowExecutionNode,
  WorkflowExecutionOutput,
  WorkflowExecutionPosition,
  WorkflowExecutionStatus,
} from '../domain/workflowExecutionGraph';

const NODE_WIDTH = 230;
const NODE_HEIGHT = 112;
const COLUMN_GAP = 72;
const ROW_GAP = 56;
const GRAPH_PADDING = 56;
const MAX_COLUMNS = 4;
const MIN_ZOOM = 0.35;
const MAX_ZOOM = 2.4;
const DEFAULT_VIEWPORT_WIDTH = 960;
const DEFAULT_VIEWPORT_HEIGHT = 620;
const FIT_PADDING = 48;

const STATUS_PRESENTATION: Record<
  WorkflowExecutionStatus,
  { label: string; tone: string; fill: string; stroke: string }
> = {
  unobserved: {
    label: 'Unobserved',
    tone: 'niuu:text-text-muted niuu:bg-bg-elevated niuu:border-border',
    fill: 'var(--color-text-muted)',
    stroke: 'var(--color-border)',
  },
  recorded: {
    label: 'Output recorded',
    tone: 'niuu:text-status-cyan niuu:bg-bg-elevated niuu:border-status-cyan',
    fill: 'var(--status-cyan)',
    stroke: 'var(--status-cyan)',
  },
  running: {
    label: 'Running',
    tone: 'niuu:text-brand niuu:bg-bg-elevated niuu:border-brand',
    fill: 'var(--color-brand)',
    stroke: 'var(--color-brand)',
  },
  completed: {
    label: 'Completed',
    tone: 'niuu:text-status-emerald niuu:bg-bg-elevated niuu:border-status-emerald',
    fill: 'var(--status-emerald)',
    stroke: 'var(--status-emerald)',
  },
  failed: {
    label: 'Failed',
    tone: 'niuu:text-critical niuu:bg-bg-elevated niuu:border-critical',
    fill: 'var(--color-critical)',
    stroke: 'var(--color-critical)',
  },
  waiting: {
    label: 'Waiting',
    tone: 'niuu:text-status-amber niuu:bg-bg-elevated niuu:border-status-amber',
    fill: 'var(--status-amber)',
    stroke: 'var(--status-amber)',
  },
};

interface ViewportTransform {
  x: number;
  y: number;
  scale: number;
}

interface ViewportSize {
  width: number;
  height: number;
}

export interface WorkflowExecutionGraphProps {
  nodes: WorkflowExecutionNode[];
  edges: WorkflowExecutionEdge[];
  selectedNodeId?: string | null;
  defaultSelectedNodeId?: string | null;
  onSelectedNodeChange?: (node: WorkflowExecutionNode | null) => void;
  onOpenChildWorkflow?: (child: WorkflowExecutionChild, node: WorkflowExecutionNode) => void;
  onOpenEvidence?: (
    evidence: WorkflowExecutionEvidence,
    context: WorkflowExecutionEvidenceContext,
  ) => void;
  title?: string;
  emptyLabel?: string;
  className?: string;
}

function clampZoom(scale: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
}

/**
 * Derive stable positions without requiring editor-owned layout data.
 *
 * Node order acts as the tie-breaker for cyclic graphs: only forward edges raise
 * a rank. Back edges remain visible, but cannot make the rank relaxation loop.
 * Long chains wrap into alternating horizontal bands so fit-to-view remains legible.
 */
export function layoutExecutionNodes(
  nodes: WorkflowExecutionNode[],
  edges: WorkflowExecutionEdge[],
): Map<string, WorkflowExecutionPosition> {
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const rankById = new Map(nodes.map((node) => [node.id, 0]));

  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const edge of edges) {
      const sourceIndex = indexById.get(edge.source);
      const targetIndex = indexById.get(edge.target);
      if (sourceIndex === undefined || targetIndex === undefined || sourceIndex >= targetIndex) {
        continue;
      }
      const nextRank = (rankById.get(edge.source) ?? 0) + 1;
      if (nextRank <= (rankById.get(edge.target) ?? 0)) continue;
      rankById.set(edge.target, nextRank);
      changed = true;
    }
    if (!changed) break;
  }

  const nodesByRank = new Map<number, WorkflowExecutionNode[]>();
  for (const node of nodes) {
    const rank = rankById.get(node.id) ?? 0;
    const peers = nodesByRank.get(rank) ?? [];
    peers.push(node);
    nodesByRank.set(rank, peers);
  }

  const maxRank = Math.max(0, ...rankById.values());
  const bandOffsets = new Map<number, number>();
  let nextBandOffset = GRAPH_PADDING;
  for (let band = 0; band <= Math.floor(maxRank / MAX_COLUMNS); band += 1) {
    bandOffsets.set(band, nextBandOffset);
    let maxPeers = 1;
    for (let offset = 0; offset < MAX_COLUMNS; offset += 1) {
      maxPeers = Math.max(maxPeers, nodesByRank.get(band * MAX_COLUMNS + offset)?.length ?? 0);
    }
    nextBandOffset += maxPeers * (NODE_HEIGHT + ROW_GAP) + ROW_GAP;
  }

  const positions = new Map<string, WorkflowExecutionPosition>();
  for (const node of nodes) {
    if (node.position) {
      positions.set(node.id, node.position);
      continue;
    }
    const rank = rankById.get(node.id) ?? 0;
    const band = Math.floor(rank / MAX_COLUMNS);
    const offset = rank % MAX_COLUMNS;
    const column = band % 2 === 0 ? offset : MAX_COLUMNS - 1 - offset;
    const peerIndex = nodesByRank.get(rank)?.findIndex((peer) => peer.id === node.id) ?? 0;
    positions.set(node.id, {
      x: GRAPH_PADDING + column * (NODE_WIDTH + COLUMN_GAP),
      y: (bandOffsets.get(band) ?? GRAPH_PADDING) + peerIndex * (NODE_HEIGHT + ROW_GAP),
    });
  }
  return positions;
}

function graphBounds(positions: Map<string, WorkflowExecutionPosition>) {
  if (positions.size === 0) {
    return { minX: 0, minY: 0, width: DEFAULT_VIEWPORT_WIDTH, height: DEFAULT_VIEWPORT_HEIGHT };
  }
  const values = [...positions.values()];
  const minX = Math.min(...values.map((position) => position.x));
  const minY = Math.min(...values.map((position) => position.y));
  const maxX = Math.max(...values.map((position) => position.x + NODE_WIDTH));
  const maxY = Math.max(...values.map((position) => position.y + NODE_HEIGHT));
  return { minX, minY, width: maxX - minX, height: maxY - minY };
}

function wrappedLabel(label: string): [string, string?] {
  const normalized = label.trim();
  if (normalized.length <= 28) return [normalized];
  const breakpoint = normalized.lastIndexOf(' ', 28);
  const splitAt = breakpoint >= 14 ? breakpoint : 28;
  const first = normalized.slice(0, splitAt).trim();
  const rest = normalized.slice(splitAt).trim();
  return [first, rest.length > 30 ? `${rest.slice(0, 29)}…` : rest];
}

function edgePath(
  edge: WorkflowExecutionEdge,
  positions: Map<string, WorkflowExecutionPosition>,
): string | null {
  const source = positions.get(edge.source);
  const target = positions.get(edge.target);
  if (!source || !target) return null;

  const sourceCenter = { x: source.x + NODE_WIDTH / 2, y: source.y + NODE_HEIGHT / 2 };
  const targetCenter = { x: target.x + NODE_WIDTH / 2, y: target.y + NODE_HEIGHT / 2 };
  const horizontal =
    Math.abs(targetCenter.x - sourceCenter.x) >= Math.abs(targetCenter.y - sourceCenter.y);

  if (horizontal) {
    const goesRight = targetCenter.x >= sourceCenter.x;
    const startX = sourceCenter.x + (goesRight ? NODE_WIDTH / 2 : -NODE_WIDTH / 2);
    const endX = targetCenter.x + (goesRight ? -NODE_WIDTH / 2 : NODE_WIDTH / 2);
    const bend = Math.max(56, Math.abs(endX - startX) / 2);
    const direction = goesRight ? 1 : -1;
    return `M ${startX} ${sourceCenter.y} C ${startX + bend * direction} ${sourceCenter.y}, ${endX - bend * direction} ${targetCenter.y}, ${endX} ${targetCenter.y}`;
  }

  const goesDown = targetCenter.y >= sourceCenter.y;
  const startY = sourceCenter.y + (goesDown ? NODE_HEIGHT / 2 : -NODE_HEIGHT / 2);
  const endY = targetCenter.y + (goesDown ? -NODE_HEIGHT / 2 : NODE_HEIGHT / 2);
  const bend = Math.max(56, Math.abs(endY - startY) / 2);
  const direction = goesDown ? 1 : -1;
  return `M ${sourceCenter.x} ${startY} C ${sourceCenter.x} ${startY + bend * direction}, ${targetCenter.x} ${endY - bend * direction}, ${targetCenter.x} ${endY}`;
}

function StatusBadge({ status }: { status: WorkflowExecutionStatus }) {
  const presentation = STATUS_PRESENTATION[status];
  return (
    <span
      className={cn(
        'niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:rounded-full niuu:border niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:font-medium',
        presentation.tone,
      )}
    >
      <span className="niuu:size-1.5 niuu:rounded-full niuu:bg-current" aria-hidden="true" />
      {presentation.label}
    </span>
  );
}

function EvidenceActions({
  evidence,
  context,
  onOpen,
}: {
  evidence?: WorkflowExecutionEvidence[];
  context: WorkflowExecutionEvidenceContext;
  onOpen?: WorkflowExecutionGraphProps['onOpenEvidence'];
}) {
  if (!evidence?.length) return null;
  return (
    <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
      {evidence.map((item) => (
        <button
          key={item.id}
          type="button"
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-secondary niuu:transition-colors hover:niuu:border-brand hover:niuu:text-text-primary focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
          onClick={() => onOpen?.(item, context)}
          disabled={!onOpen}
          title={item.kind}
        >
          Evidence · {item.label}
        </button>
      ))}
    </div>
  );
}

function OutputValue({ output }: { output: WorkflowExecutionOutput }) {
  if (output.format === 'markdown') {
    if (!output.value.trim()) {
      return <p className="niuu:text-sm niuu:text-text-muted">No content was recorded.</p>;
    }
    return (
      <div className="niuu:text-sm niuu:text-text-secondary">
        <MarkdownContent content={output.value} />
      </div>
    );
  }
  return (
    <pre className="niuu:max-h-72 niuu:overflow-auto niuu:whitespace-pre-wrap niuu:break-words niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-xs niuu:leading-relaxed niuu:text-text-secondary">
      {JSON.stringify(output.value, null, 2) ?? 'null'}
    </pre>
  );
}

function formatTimestamp(timestamp: string): string {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return timestamp;
  return parsed.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

function History({
  events,
  nodeId,
  onOpenEvidence,
}: {
  events: WorkflowExecutionEvent[];
  nodeId: string;
  onOpenEvidence?: WorkflowExecutionGraphProps['onOpenEvidence'];
}) {
  if (!events.length) {
    return (
      <div className="niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-muted">
        No public history has been recorded for this node.
      </div>
    );
  }

  return (
    <ol className="niuu:space-y-3">
      {events.map((event) => (
        <li key={event.id} className="niuu:relative niuu:border-l niuu:border-border niuu:pl-4">
          <span
            className="niuu:absolute niuu:-left-1 niuu:top-1.5 niuu:size-2 niuu:rounded-full niuu:bg-brand"
            aria-hidden="true"
          />
          <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
            <span className="niuu:font-mono niuu:text-xs niuu:text-brand">{event.eventType}</span>
            <time className="niuu:text-xs niuu:text-text-muted" dateTime={event.timestamp}>
              {formatTimestamp(event.timestamp)}
            </time>
          </div>
          <p className="niuu:mt-1 niuu:text-sm niuu:text-text-secondary">{event.summary}</p>
          {(event.persona || event.verdict) && (
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
              {[event.persona, event.verdict].filter(Boolean).join(' · ')}
            </p>
          )}
          {event.fields && Object.keys(event.fields).length > 0 && (
            <details className="niuu:mt-2">
              <summary className="niuu:cursor-pointer niuu:text-xs niuu:text-text-secondary">
                Structured fields
              </summary>
              <pre className="niuu:mt-2 niuu:max-h-56 niuu:overflow-auto niuu:whitespace-pre-wrap niuu:break-words niuu:rounded-md niuu:bg-bg-primary niuu:p-2 niuu:font-mono niuu:text-xs niuu:text-text-muted">
                {JSON.stringify(event.fields, null, 2)}
              </pre>
            </details>
          )}
          <div className="niuu:mt-2">
            <EvidenceActions
              evidence={event.evidence}
              context={{ nodeId, eventId: event.id }}
              onOpen={onOpenEvidence}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}

function NodeInspector({
  node,
  onOpenChildWorkflow,
  onOpenEvidence,
}: {
  node: WorkflowExecutionNode | null;
  onOpenChildWorkflow?: WorkflowExecutionGraphProps['onOpenChildWorkflow'];
  onOpenEvidence?: WorkflowExecutionGraphProps['onOpenEvidence'];
}) {
  const [outputSelection, setOutputSelection] = useState<{
    nodeId: string;
    outputId: string;
  } | null>(null);
  const outputs = node?.outputs ?? [];
  const selectedOutput =
    outputs.find(
      (output) => outputSelection?.nodeId === node?.id && output.id === outputSelection?.outputId,
    ) ?? outputs[0];

  if (!node) {
    return (
      <aside className="niuu:flex niuu:min-h-0 niuu:min-w-0 niuu:items-center niuu:justify-center niuu:border-l niuu:border-border niuu:bg-bg-secondary niuu:p-8">
        <div className="niuu:max-w-xs niuu:text-center">
          <p className="niuu:text-sm niuu:font-medium niuu:text-text-secondary">Select a node</p>
          <p className="niuu:mt-1 niuu:text-xs niuu:leading-relaxed niuu:text-text-muted">
            Inspect its public output, chronological history, evidence, and child workflows.
          </p>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className="niuu:min-h-0 niuu:min-w-0 niuu:overflow-auto niuu:border-l niuu:border-border niuu:bg-bg-secondary"
      aria-label={`${node.label} inspector`}
    >
      <div className="niuu:sticky niuu:top-0 niuu:z-10 niuu:border-b niuu:border-border niuu:bg-bg-secondary niuu:p-5">
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
          <div className="niuu:min-w-0">
            <p className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
              {node.kind}
            </p>
            <h3 className="niuu:mt-1 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
              {node.label}
            </h3>
          </div>
          <StatusBadge status={node.status} />
        </div>
        {node.detail && (
          <p className="niuu:mt-3 niuu:text-sm niuu:leading-relaxed niuu:text-text-secondary">
            {node.detail}
          </p>
        )}
      </div>

      <div className="niuu:space-y-6 niuu:p-5">
        <section aria-labelledby={`output-title-${node.id}`}>
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
            <h4
              id={`output-title-${node.id}`}
              className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wider niuu:text-text-muted"
            >
              Public output
            </h4>
            <span className="niuu:text-xs niuu:text-text-muted">{outputs.length}</span>
          </div>
          {outputs.length === 0 ? (
            <div className="niuu:mt-3 niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:p-4 niuu:text-sm niuu:text-text-muted">
              No public output has been recorded.
            </div>
          ) : (
            <>
              <div
                className="niuu:mt-3 niuu:flex niuu:flex-wrap niuu:gap-1"
                role="tablist"
                aria-label="Node outputs"
              >
                {outputs.map((output) => {
                  const active = output.id === selectedOutput?.id;
                  return (
                    <button
                      key={output.id}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      className={cn(
                        'niuu:rounded-md niuu:px-2.5 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:transition-colors focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand',
                        active
                          ? 'niuu:bg-brand niuu:text-bg-primary'
                          : 'niuu:bg-bg-elevated niuu:text-text-secondary hover:niuu:text-text-primary',
                      )}
                      onClick={() => setOutputSelection({ nodeId: node.id, outputId: output.id })}
                    >
                      {output.label}
                    </button>
                  );
                })}
              </div>
              {selectedOutput && (
                <div
                  className="niuu:mt-3 niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-4"
                  role="tabpanel"
                >
                  <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
                    <span className="niuu:text-xs niuu:font-medium niuu:text-text-muted">
                      {selectedOutput.format === 'markdown' ? 'Markdown' : 'Structured data'}
                    </span>
                    {selectedOutput.isPublic && (
                      <span className="niuu:rounded-full niuu:bg-bg-elevated niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-status-cyan">
                        Public
                      </span>
                    )}
                  </div>
                  <OutputValue output={selectedOutput} />
                  <div className="niuu:mt-3">
                    <EvidenceActions
                      evidence={selectedOutput.evidence}
                      context={{ nodeId: node.id, outputId: selectedOutput.id }}
                      onOpen={onOpenEvidence}
                    />
                  </div>
                </div>
              )}
            </>
          )}
        </section>

        <section aria-labelledby={`history-title-${node.id}`}>
          <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
            <h4
              id={`history-title-${node.id}`}
              className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wider niuu:text-text-muted"
            >
              History
            </h4>
            <span className="niuu:text-xs niuu:text-text-muted">{node.events?.length ?? 0}</span>
          </div>
          <History events={node.events ?? []} nodeId={node.id} onOpenEvidence={onOpenEvidence} />
        </section>

        {(node.childWorkflows?.length ?? 0) > 0 && (
          <details
            className="niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary"
            open
          >
            <summary className="niuu:flex niuu:cursor-pointer niuu:items-center niuu:justify-between niuu:gap-3 niuu:p-3 niuu:text-sm niuu:font-medium niuu:text-text-primary">
              Child workflows
              <span className="niuu:text-xs niuu:text-text-muted">
                {node.childWorkflows?.length}
              </span>
            </summary>
            <div className="niuu:space-y-2 niuu:border-t niuu:border-border niuu:p-3">
              {node.childWorkflows?.map((child) => (
                <button
                  key={child.id}
                  type="button"
                  className="niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-3 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-3 niuu:text-left niuu:transition-colors hover:niuu:border-brand focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
                  onClick={() => onOpenChildWorkflow?.(child, node)}
                  disabled={!onOpenChildWorkflow}
                >
                  <span className="niuu:min-w-0">
                    <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                      {child.label}
                    </span>
                    {child.detail && (
                      <span className="niuu:mt-0.5 niuu:block niuu:truncate niuu:text-xs niuu:text-text-muted">
                        {child.detail}
                      </span>
                    )}
                  </span>
                  {child.status && <StatusBadge status={child.status} />}
                </button>
              ))}
            </div>
          </details>
        )}

        <EvidenceActions
          evidence={node.evidence}
          context={{ nodeId: node.id }}
          onOpen={onOpenEvidence}
        />
      </div>
    </aside>
  );
}

export function WorkflowExecutionGraph({
  nodes,
  edges,
  selectedNodeId: controlledSelectedNodeId,
  defaultSelectedNodeId,
  onSelectedNodeChange,
  onOpenChildWorkflow,
  onOpenEvidence,
  title = 'Execution graph',
  emptyLabel = 'No execution topology is available yet.',
  className,
}: WorkflowExecutionGraphProps) {
  const [internalSelectedNodeId, setInternalSelectedNodeId] = useState<string | null>(
    defaultSelectedNodeId ?? nodes[0]?.id ?? null,
  );
  const validInternalSelectedNodeId = nodes.some((node) => node.id === internalSelectedNodeId)
    ? internalSelectedNodeId
    : (nodes[0]?.id ?? null);
  const selectedNodeId =
    controlledSelectedNodeId === undefined ? validInternalSelectedNodeId : controlledSelectedNodeId;
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const positions = useMemo(() => layoutExecutionNodes(nodes, edges), [nodes, edges]);
  const bounds = useMemo(() => graphBounds(positions), [positions]);
  const geometrySignature = JSON.stringify({
    nodes: nodes.map((node) => [node.id, node.position?.x ?? null, node.position?.y ?? null]),
    edges: edges.map((edge) => [edge.id, edge.source, edge.target]),
  });
  const definitionId = useId().replace(/:/g, '');
  const gridId = `workflow-execution-grid-${definitionId}`;
  const arrowId = `workflow-execution-arrow-${definitionId}`;
  const canvasHostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<SVGSVGElement>(null);
  const panRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    originX: number;
    originY: number;
  } | null>(null);
  const fittedLayoutRef = useRef<string | null>(null);
  const [viewportSize, setViewportSize] = useState<ViewportSize>({
    width: DEFAULT_VIEWPORT_WIDTH,
    height: DEFAULT_VIEWPORT_HEIGHT,
  });
  const [viewport, setViewport] = useState<ViewportTransform>({ x: 0, y: 0, scale: 1 });

  useEffect(() => {
    const host = canvasHostRef.current;
    if (!host) return;

    function updateSize(width: number, height: number) {
      if (width <= 0 || height <= 0) return;
      setViewportSize((current) =>
        current.width === width && current.height === height ? current : { width, height },
      );
    }

    updateSize(host.clientWidth, host.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) updateSize(rect.width, rect.height);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const fitGraph = useCallback(() => {
    const { width, height } = viewportSize;
    const scale = clampZoom(
      Math.min(
        (width - FIT_PADDING * 2) / Math.max(bounds.width, 1),
        (height - FIT_PADDING * 2) / Math.max(bounds.height, 1),
        1.15,
      ),
    );
    setViewport({
      scale,
      x: (width - bounds.width * scale) / 2 - bounds.minX * scale,
      y: (height - bounds.height * scale) / 2 - bounds.minY * scale,
    });
  }, [bounds, viewportSize]);

  useEffect(() => {
    const layoutSignature = `${geometrySignature}:${viewportSize.width}x${viewportSize.height}`;
    if (fittedLayoutRef.current === layoutSignature) return;
    fittedLayoutRef.current = layoutSignature;
    fitGraph();
  }, [fitGraph, geometrySignature, viewportSize]);

  function selectNode(node: WorkflowExecutionNode | null) {
    if (controlledSelectedNodeId === undefined) setInternalSelectedNodeId(node?.id ?? null);
    onSelectedNodeChange?.(node);
  }

  function zoomBy(factor: number) {
    setViewport((current) => {
      const scale = clampZoom(current.scale * factor);
      const { width, height } = viewportSize;
      const ratio = scale / current.scale;
      return {
        scale,
        x: width / 2 - (width / 2 - current.x) * ratio,
        y: height / 2 - (height / 2 - current.y) * ratio,
      };
    });
  }

  function handleGraphKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      zoomBy(1.2);
      return;
    }
    if (event.key === '-') {
      event.preventDefault();
      zoomBy(1 / 1.2);
      return;
    }
    if (event.key === '0' || event.key.toLowerCase() === 'f') {
      event.preventDefault();
      fitGraph();
      return;
    }
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
    event.preventDefault();
    if (!nodes.length) return;
    const selectedIndex = nodes.findIndex((node) => node.id === selectedNodeId);
    const direction = event.key === 'ArrowRight' ? 1 : -1;
    const nextIndex = (Math.max(selectedIndex, 0) + direction + nodes.length) % nodes.length;
    selectNode(nodes[nextIndex] ?? null);
  }

  function handleWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12);
  }

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return;
    panRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      originX: viewport.x,
      originY: viewport.y,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!panRef.current || panRef.current.pointerId !== event.pointerId) return;
    setViewport((current) => ({
      ...current,
      x: panRef.current!.originX + event.clientX - panRef.current!.x,
      y: panRef.current!.originY + event.clientY - panRef.current!.y,
    }));
  }

  function handlePointerUp(event: PointerEvent<SVGSVGElement>) {
    if (panRef.current?.pointerId === event.pointerId) panRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  }

  return (
    <section
      className={cn(
        'niuu:overflow-hidden niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-primary',
        className,
      )}
      aria-label={title}
    >
      <header className="niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-3 niuu:border-b niuu:border-border niuu:bg-bg-secondary niuu:px-4 niuu:py-3">
        <div>
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">{title}</h2>
          <p className="niuu:mt-0.5 niuu:text-xs niuu:text-text-muted">
            {nodes.length} nodes · {edges.length} connections
          </p>
        </div>
        <div className="niuu:flex niuu:items-center niuu:gap-1" aria-label="Graph controls">
          <span
            className="niuu:min-w-12 niuu:text-center niuu:font-mono niuu:text-xs niuu:text-text-muted"
            aria-live="polite"
          >
            {Math.round(viewport.scale * 100)}%
          </span>
          <button
            type="button"
            aria-label="Zoom out"
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-secondary hover:niuu:text-text-primary focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
            onClick={() => zoomBy(1 / 1.2)}
          >
            −
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-secondary hover:niuu:text-text-primary focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
            onClick={() => zoomBy(1.2)}
          >
            +
          </button>
          <button
            type="button"
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-elevated niuu:px-2.5 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-secondary hover:niuu:text-text-primary focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
            onClick={fitGraph}
          >
            Fit
          </button>
        </div>
      </header>

      <div className="niuu:grid niuu:min-w-0 niuu:grid-cols-1 niuu:xl:h-[calc(100vh-12rem)] niuu:xl:min-h-96 niuu:xl:!grid-cols-3">
        <div
          ref={canvasHostRef}
          data-testid="execution-canvas-host"
          className="niuu:relative niuu:h-[32rem] niuu:min-w-0 niuu:overflow-hidden niuu:bg-bg-primary niuu:xl:col-span-2 niuu:xl:h-auto niuu:xl:min-h-0"
        >
          {nodes.length === 0 ? (
            <div className="niuu:absolute niuu:inset-0 niuu:flex niuu:items-center niuu:justify-center niuu:p-8">
              <p className="niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-secondary niuu:px-5 niuu:py-4 niuu:text-sm niuu:text-text-muted">
                {emptyLabel}
              </p>
            </div>
          ) : (
            <svg
              ref={canvasRef}
              className="niuu:absolute niuu:inset-0 niuu:size-full niuu:touch-none niuu:cursor-grab focus-visible:niuu:outline focus-visible:niuu:outline-2 focus-visible:niuu:outline-brand"
              role="application"
              aria-label={`${title} canvas. Use arrow keys to select nodes, plus and minus to zoom, and F to fit.`}
              tabIndex={0}
              onKeyDown={handleGraphKeyDown}
              onWheel={handleWheel}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerCancel={handlePointerUp}
            >
              <defs>
                <pattern id={gridId} width="28" height="28" patternUnits="userSpaceOnUse">
                  <path
                    d="M 28 0 L 0 0 0 28"
                    fill="none"
                    stroke="var(--color-border)"
                    strokeOpacity="0.28"
                    strokeWidth="1"
                  />
                </pattern>
                <marker
                  id={arrowId}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-text-muted)" />
                </marker>
              </defs>
              <rect width="100%" height="100%" fill={`url(#${gridId})`} />
              <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.scale})`}>
                <g aria-label="Connections">
                  {edges.map((edge) => {
                    const path = edgePath(edge, positions);
                    if (!path) return null;
                    const presentation = edge.status ? STATUS_PRESENTATION[edge.status] : null;
                    const source = positions.get(edge.source);
                    const target = positions.get(edge.target);
                    const labelX = source && target ? (source.x + target.x + NODE_WIDTH) / 2 : 0;
                    const labelY =
                      source && target ? (source.y + target.y + NODE_HEIGHT) / 2 - 7 : 0;
                    return (
                      <g key={edge.id} data-testid={`execution-edge-${edge.id}`}>
                        <path
                          d={path}
                          fill="none"
                          stroke={presentation?.stroke ?? 'var(--color-text-muted)'}
                          strokeWidth={edge.status === 'running' ? 2.5 : 1.5}
                          strokeOpacity={edge.status === 'unobserved' ? 0.45 : 0.8}
                          markerEnd={`url(#${arrowId})`}
                        />
                        {edge.label && (
                          <text
                            x={labelX}
                            y={labelY}
                            textAnchor="middle"
                            fill="var(--color-text-muted)"
                            stroke="var(--color-bg-primary)"
                            strokeWidth="5"
                            paintOrder="stroke"
                            fontFamily="var(--font-mono)"
                            fontSize="10"
                          >
                            {edge.label.length > 30 ? `${edge.label.slice(0, 29)}…` : edge.label}
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
                <g aria-label="Execution nodes">
                  {nodes.map((node) => {
                    const position = positions.get(node.id);
                    if (!position) return null;
                    const presentation = STATUS_PRESENTATION[node.status];
                    const selected = node.id === selectedNodeId;
                    const [labelLineOne, labelLineTwo] = wrappedLabel(node.label);
                    return (
                      <g
                        key={node.id}
                        data-testid={`execution-node-${node.id}`}
                        data-status={node.status}
                        role="button"
                        aria-label={`${node.label}, ${presentation.label}`}
                        aria-pressed={selected}
                        tabIndex={0}
                        className="niuu:cursor-pointer focus-visible:niuu:outline-none"
                        transform={`translate(${position.x} ${position.y})`}
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={() => selectNode(node)}
                        onKeyDown={(event) => {
                          if (event.key !== 'Enter' && event.key !== ' ') return;
                          event.preventDefault();
                          event.stopPropagation();
                          selectNode(node);
                        }}
                      >
                        {selected && (
                          <rect
                            x="-5"
                            y="-5"
                            width={NODE_WIDTH + 10}
                            height={NODE_HEIGHT + 10}
                            rx="15"
                            fill="none"
                            stroke="var(--color-brand)"
                            strokeWidth="2"
                            strokeOpacity="0.55"
                          />
                        )}
                        <rect
                          width={NODE_WIDTH}
                          height={NODE_HEIGHT}
                          rx="11"
                          fill="var(--color-bg-secondary)"
                          stroke={selected ? 'var(--color-brand)' : presentation.stroke}
                          strokeWidth={selected || node.status === 'running' ? 2 : 1.25}
                        />
                        <rect width="5" height={NODE_HEIGHT} rx="2.5" fill={presentation.fill} />
                        <circle cx="22" cy="23" r="5" fill={presentation.fill} />
                        {node.status === 'running' && (
                          <circle
                            cx="22"
                            cy="23"
                            r="10"
                            fill="none"
                            stroke={presentation.stroke}
                            strokeWidth="1.5"
                            opacity="0.5"
                          />
                        )}
                        <text
                          x="36"
                          y="27"
                          fill="var(--color-text-muted)"
                          fontFamily="var(--font-mono)"
                          fontSize="10"
                          letterSpacing="0.7"
                        >
                          {node.kind.toUpperCase().slice(0, 24)}
                        </text>
                        <text
                          x="18"
                          y="52"
                          fill="var(--color-text-primary)"
                          fontFamily="var(--font-sans)"
                          fontSize="13"
                          fontWeight="600"
                        >
                          <tspan x="18" dy="0">
                            {labelLineOne}
                          </tspan>
                          {labelLineTwo && (
                            <tspan x="18" dy="17">
                              {labelLineTwo}
                            </tspan>
                          )}
                        </text>
                        <text
                          x="18"
                          y="98"
                          fill={presentation.fill}
                          fontFamily="var(--font-sans)"
                          fontSize="10"
                          fontWeight="600"
                        >
                          {presentation.label.toUpperCase()}
                        </text>
                        <text
                          x={NODE_WIDTH - 16}
                          y="98"
                          textAnchor="end"
                          fill="var(--color-text-muted)"
                          fontFamily="var(--font-mono)"
                          fontSize="10"
                        >
                          {(node.outputs?.length ?? 0) > 0 ? `${node.outputs?.length} OUT` : ''}
                        </text>
                      </g>
                    );
                  })}
                </g>
              </g>
            </svg>
          )}
          <div className="niuu:pointer-events-none niuu:absolute niuu:bottom-3 niuu:left-3 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1.5 niuu:text-xs niuu:text-text-muted">
            Drag to pan · scroll to zoom · arrows to inspect
          </div>
        </div>
        <NodeInspector
          node={selectedNode}
          onOpenChildWorkflow={onOpenChildWorkflow}
          onOpenEvidence={onOpenEvidence}
        />
      </div>
    </section>
  );
}
