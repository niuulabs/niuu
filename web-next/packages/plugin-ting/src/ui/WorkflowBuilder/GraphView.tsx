/**
 * GraphView — interactive pan/zoom SVG canvas.
 *
 * Renders WorkflowNodes as SVG shapes and WorkflowEdges as bezier curves.
 * Supports:
 *  • Mouse-drag pan on the canvas background
 *  • Scroll-wheel zoom (clamped 0.3×–3×)
 *  • Node drag-to-reposition
 *  • Click to select
 *  • Right-click empty canvas to add a node; right-click nodes to inspect
 *  • "Connect" button on selected node to draw edges
 *  • Add-node toolbar
 *  • Delete-node button on selected node
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useRef, useState, useEffect, useMemo, useCallback } from 'react';
import { cn } from '@niuulabs/ui';
import './workflowCanvas.css';
import type {
  WorkflowNode,
  WorkflowEdge,
  WorkflowStageNode,
  WorkflowWaitNode,
} from '../../domain/workflow';
import { providedNodeIds } from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import type { RunGraphNodeState, RunNodeState } from '../../domain/runGraph';
import { isReentryEdge, parseWorkflowEdgeLabel } from '../../domain/workflowSemantics';
import {
  nodeMatchIds,
  nodePortCatalog,
  resolveIncludeEndpoint,
  resolveWorkflowEdgePorts,
  type WorkflowNodePort,
  type WorkflowNodePortCatalog,
} from '../../domain/workflowPorts';
import {
  CARD_WIDTH,
  feedbackLaneAssignments,
  nodeBounds,
  portAnchor,
  workflowCardSize,
  type WorkflowSize,
} from '../../domain/workflowGeometry';
import { layoutWorkflow } from '../../domain/workflowLayout';
import {
  includeCardSubtitle,
  resolveNodeMouseDownAction,
  resourceModeVisuals,
  triggerCardSubtitle,
  triggerOutputEvent,
  truncateLabel,
} from '../../domain/graphViewLogic';
import type { WorkflowBuilderActions } from './useWorkflowBuilder';
import { LibraryPanel, type PersonaEntry } from './LibraryPanel';
import {
  MIMIR_MOUNT_MIME,
  parseWorkflowRegistryMount,
  type WorkflowRegistryMount,
} from './mimirRegistry';
import { normalizedStageMembers } from './graphUtils';

// ---------------------------------------------------------------------------
// Zoom / pan constants
// ---------------------------------------------------------------------------

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 3.0;
/** A viewer framing a whole workflow in a short or narrow panel may go below the editor's floor. */
const VIEWER_MIN_ZOOM = 0.1;


// ---------------------------------------------------------------------------
// Node cards — one recipe for every kind: an icon tile, the step's name and who runs it.
// The canvas lays every card out itself (left to right), so a card is selected, never
// dragged; its ports are drawn on its edges by NodeSocketLayer.
// ---------------------------------------------------------------------------

const CARD_ICON_PATHS = {
  code: 'M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14',
  review:
    'M7 4h10a2 2 0 012 2v13a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2zM9 4h6v3H9zM9 14l2 2 4-4',
  shield: 'M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6zM9 12l2 2 4-4',
  flask: 'M9 3h6M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3',
  list: 'M5 6h14M5 12h14M5 18h9',
  pen: 'M4 20l4-1 11-11-3-3L5 16z',
  person: 'M12 4.5a3.5 3.5 0 110 7 3.5 3.5 0 010-7zM5 20c1-4 4-6 7-6s6 2 7 6',
  bot: 'M8 8h8a3 3 0 013 3v5a3 3 0 01-3 3H8a3 3 0 01-3-3v-5a3 3 0 013-3zM12 4v4M9 13.5h.01M15 13.5h.01',
  bolt: 'M13 3L5 14h6l-1 7 8-11h-6z',
  clock: 'M12 4a8 8 0 110 16 8 8 0 010-16zM12 8v4l3 2',
  stack:
    'M10 8h7a2 2 0 012 2v7a2 2 0 01-2 2h-7a2 2 0 01-2-2v-7a2 2 0 012-2zM5 15V6a1 1 0 011-1h9',
  branch: 'M4 12h6l4-6h6M10 12l4 6h6',
  database:
    'M12 4c4 0 7 1.3 7 3s-3 3-7 3-7-1.3-7-3 3-3 7-3zM5 7v10c0 1.7 3 3 7 3s7-1.3 7-3V7M5 12c0 1.7 3 3 7 3s7-1.3 7-3',
  layers: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
  flag: 'M6 21V4M6 5h11l-2 4 2 4H6',
} as const;
type CardIconName = keyof typeof CARD_ICON_PATHS;

function CardIcon({ name, size = 17 }: { name: CardIconName; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={CARD_ICON_PATHS[name]} />
    </svg>
  );
}

/** A persona's icon, read from what it is called: its role is in its name. */
function personaIcon(personaId: string | undefined): CardIconName {
  const id = (personaId ?? '').toLowerCase();
  if (id.includes('secur') || id.includes('audit')) return 'shield';
  if (id.includes('review')) return 'review';
  if (id.includes('test') || id.includes('qa')) return 'flask';
  if (id.includes('plan')) return 'list';
  if (id.includes('doc') || id.includes('writ')) return 'pen';
  if (id.includes('code') || id.includes('merge') || id.includes('build')) return 'code';
  return 'bot';
}

/** How a card is drawn: its tone (the tile's colour), icon and the line under its name. */
export type WorkflowCardTone =
  | 'agent'
  | 'gate'
  | 'trigger'
  | 'wait'
  | 'cond'
  | 'end'
  | 'resource'
  | 'include';

export interface WorkflowCardContent {
  tone: WorkflowCardTone;
  icon: CardIconName;
  subtitle: string;
  /** How a screen reader names the kind, e.g. "Stage step". */
  kindLabel: string;
}

const GATE_DECIDER: Record<string, string> = {
  human_approval: 'You decide',
  human_review: 'You review',
  automated_approval: 'An agent decides',
  evidence: 'Evidence decides',
};

/** What a card shows for a node, beside its name. */
export function workflowCardContent(
  node: WorkflowNode,
  personas: readonly PersonaEntry[],
): WorkflowCardContent {
  switch (node.kind) {
    case 'stage': {
      const members = normalizedStageMembers(node);
      const name = (personaId: string) =>
        personas.find((persona) => persona.id === personaId)?.label ?? personaId;
      let subtitle = 'No persona yet';
      if (members.length === 1) {
        const [member] = members;
        subtitle = `${name(member!.personaId)} · ${member!.model || 'default model'}`;
      } else if (members.length > 1) {
        const together = (node.executionMode ?? 'parallel') === 'parallel';
        subtitle = `${members.map((member) => name(member.personaId)).join(' + ')} · ${
          together ? 'together' : 'in turn'
        }`;
      }
      return {
        tone: 'agent',
        icon: personaIcon(members[0]?.personaId),
        subtitle,
        kindLabel: 'Stage step',
      };
    }
    case 'gate': {
      const mode = node.mode ?? 'human_approval';
      return {
        tone: 'gate',
        icon: mode === 'automated_approval' ? 'bot' : mode === 'human_approval' ? 'person' : 'review',
        subtitle: GATE_DECIDER[mode] ?? mode,
        kindLabel: 'Gate step',
      };
    }
    case 'trigger':
      return {
        tone: 'trigger',
        icon: 'bolt',
        subtitle: `When ${node.source || 'started'}`,
        kindLabel: 'Trigger step',
      };
    case 'subworkflow':
      return {
        tone: 'include',
        icon: 'stack',
        subtitle: `For each · ${triggerCardSubtitle(node, triggerOutputEvent(node))}`,
        kindLabel: 'Subworkflow step',
      };
    case 'wait':
      return {
        tone: 'wait',
        icon: 'clock',
        subtitle: node.conditions?.length
          ? `Waits for ${node.conditions.join(', ')}`
          : 'Waits for an outside event',
        kindLabel: 'Wait step',
      };
    case 'cond':
      return {
        tone: 'cond',
        icon: 'branch',
        subtitle: node.predicate || 'No condition yet',
        kindLabel: 'Condition step',
      };
    case 'end':
      return { tone: 'end', icon: 'flag', subtitle: '', kindLabel: 'End step' };
    case 'resource':
      return {
        tone: 'resource',
        icon: 'database',
        subtitle: resourceModeVisuals(node.bindingMode).modeLabel.toLowerCase(),
        kindLabel: 'Resource',
      };
    case 'include':
      return {
        tone: 'include',
        icon: 'layers',
        subtitle: includeCardSubtitle(node.workflow, Object.keys(node.nodes ?? {}).length),
        kindLabel: 'Include step',
      };
  }
}

interface WorkflowNodeCardProps {
  node: WorkflowNode;
  bounds: { x: number; y: number; width: number; height: number };
  content: WorkflowCardContent;
  hasPorts: boolean;
  selected: boolean;
  issueLevel: 'error' | 'warning' | null;
  isConnectingMode: boolean;
  onSelect: () => void;
  onInspect: () => void;
  /** A click on the card's body while connecting: the card picks its own input. */
  onBodyConnect: () => void;
}

function WorkflowNodeCard({
  node,
  bounds,
  content,
  hasPorts,
  selected,
  issueLevel,
  isConnectingMode,
  onSelect,
  onInspect,
  onBodyConnect,
}: WorkflowNodeCardProps) {
  function handleMouseDown(event: React.MouseEvent) {
    event.stopPropagation();
    const action = resolveNodeMouseDownAction(event.button, event.ctrlKey, isConnectingMode);
    if (action === 'ignore') return;
    if (action === 'complete-connect') {
      onBodyConnect();
      return;
    }
    onSelect();
  }

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind={node.kind}
      data-selected={selected ? 'true' : undefined}
      data-issue={issueLevel ?? undefined}
      role="button"
      tabIndex={0}
      aria-label={`${content.kindLabel}: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onContextMenu={(event) => {
        event.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'pointer' }}
    >
      <foreignObject x={bounds.x} y={bounds.y} width={bounds.width} height={bounds.height}>
        <div
          className="workflow-card"
          data-tone={content.tone}
          data-selected={selected ? 'true' : undefined}
          data-issue={issueLevel ?? undefined}
        >
          {node.kind === 'end' ? (
            <div className="workflow-card__end">
              <CardIcon name="flag" size={14} />
              <span className="workflow-card__end-label" title={node.label}>
                {node.label}
              </span>
            </div>
          ) : (
            <div className="workflow-card__head">
              <span className="workflow-card__tile">
                <CardIcon name={content.icon} />
              </span>
              <span className="workflow-card__text">
                <span className="workflow-card__title" title={node.label}>
                  {node.label}
                </span>
                <span className="workflow-card__sub" title={content.subtitle}>
                  {content.subtitle}
                </span>
              </span>
            </div>
          )}
          {hasPorts && <div className="workflow-card__ports" />}
        </div>
      </foreignObject>
      {issueLevel && (
        <g
          className="workflow-card__badge"
          data-issue={issueLevel}
          transform={`translate(${bounds.x + bounds.width - 2},${bounds.y + 2})`}
          pointerEvents="none"
        >
          <circle r={9} />
          <text textAnchor="middle" y={4}>
            !
          </text>
        </g>
      )}
    </g>
  );
}

export function waitPortLists(nodeId: string, edges: WorkflowEdge[]) {
  const inputs = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.targetEventType)
    .filter((eventType): eventType is string => Boolean(eventType));
  const outputs = edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.sourceEventType)
    .filter((eventType): eventType is string => Boolean(eventType));
  return {
    incomingEvents: [...new Set(inputs)],
    outgoingEvents: [...new Set(outputs)],
  };
}

function requestWaitEventType(direction: 'incoming' | 'continuation', suggested = '') {
  return window
    .prompt(
      direction === 'incoming'
        ? 'Incoming event type for this wait'
        : 'Continuation event type emitted after this wait',
      suggested,
    )
    ?.trim();
}

/**
 * A wait with no events yet has no ports to connect to: its card offers one socket on each
 * side that asks which event the wait takes in or passes on.
 */
function WaitUnconfiguredSockets({
  node,
  bounds,
  incomingEvents,
  outgoingEvents,
  isConnectingMode,
  readOnly,
  connectingFromLabel,
  onStartConnect,
  onCompleteConnect,
}: {
  node: WorkflowWaitNode;
  bounds: { x: number; y: number; width: number; height: number };
  incomingEvents: string[];
  outgoingEvents: string[];
  isConnectingMode: boolean;
  readOnly: boolean;
  connectingFromLabel?: string | null;
  onStartConnect: (eventType: string) => void;
  onCompleteConnect: (eventType: string) => void;
}) {
  const completeIncoming = () => {
    const selectedEvent = requestWaitEventType('incoming', connectingFromLabel ?? undefined);
    if (selectedEvent) onCompleteConnect(selectedEvent);
  };
  const startOutgoing = () => {
    const selectedEvent = requestWaitEventType('continuation');
    if (selectedEvent) onStartConnect(selectedEvent);
  };
  const socket = (
    testId: string,
    cx: number,
    label: string,
    activate: () => void,
  ) => (
    <circle
      data-testid={testId}
      className="workflow-socket"
      data-direction={testId.includes('input') ? 'input' : 'output'}
      cx={cx}
      cy={bounds.y + bounds.height / 2}
      r={6}
      role="button"
      tabIndex={0}
      aria-label={label}
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        activate();
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        activate();
      }}
    />
  );
  return (
    <g>
      {isConnectingMode &&
        incomingEvents.length === 0 &&
        socket(`wait-input-${node.id}`, bounds.x, 'Connect incoming event', completeIncoming)}
      {outgoingEvents.length === 0 &&
        !readOnly &&
        socket(
          `wait-output-${node.id}`,
          bounds.x + bounds.width,
          'Connect outgoing event',
          startOutgoing,
        )}
    </g>
  );
}

/**
 * An `include` card has no single id an edge can use — real edges name one
 * of its provided local ids instead (see `providedNodeIds`). Resolve which
 * one a connect gesture on the card means: reuse the provided id that
 * already carries this exact event on this side, fall back to the sole
 * provided id when there's only one, or ask when several are equally
 * plausible. Returns the node's own id unchanged for every other kind.
 */
function resolveIncludeConnectionId(
  node: WorkflowNode,
  eventType: string,
  direction: 'source' | 'target',
  edges: readonly WorkflowEdge[],
): string | null {
  return resolveIncludeEndpoint(node, eventType, direction, edges, (event, providedIds) =>
    window.prompt(
      `Which included node does "${event}" belong to? (${providedIds.join(', ')})`,
      providedIds[0],
    ),
  );
}

export function stagePortLists(node: WorkflowStageNode, personas: PersonaEntry[]) {
  const stageMembers = normalizedStageMembers(node);
  const personaMap = new Map(personas.map((persona) => [persona.id, persona]));
  return {
    knownInputs: [
      ...new Set(
        stageMembers.flatMap((member) => personaMap.get(member.personaId)?.consumes ?? []),
      ),
    ],
    knownOutputs: [
      ...new Set(
        stageMembers.flatMap((member) => personaMap.get(member.personaId)?.produces ?? []),
      ),
    ],
  };
}

export function renderedStageHeight(node: WorkflowStageNode, personas: PersonaEntry[]) {
  const catalog = nodePortCatalog(node, { personas });
  return workflowCardSize('stage', Math.max(catalog.inputs.length, catalog.outputs.length))
    .height;
}

export function splitEdgePorts(label?: string) {
  if (!label) return { sourcePort: null, targetPort: null };
  const parsed = parseWorkflowEdgeLabel(label);
  if (parsed) {
    return {
      sourcePort: parsed.sourceEventType,
      targetPort: parsed.targetEventType,
    };
  }
  const [rawSourcePort, rawTargetPort] = label.split(' -> ', 2);
  if (rawTargetPort !== undefined) {
    return {
      sourcePort: rawSourcePort ?? null,
      targetPort: rawTargetPort,
    };
  }
  return { sourcePort: label, targetPort: label };
}

/**
 * The ports a card shows. Every outcome is a dot on its right edge, wired or not, so what a
 * step can say is always in view; its incoming events show on the left only where a wire
 * arrives, or all of them while the card is selected (`expanded`) or a wire is being drawn.
 */
export function visibleNodePortCatalog(
  node: WorkflowNode,
  personas: PersonaEntry[],
  edges: WorkflowEdge[],
  expanded: boolean,
): WorkflowNodePortCatalog {
  const catalog = nodePortCatalog(node, { personas, edges });
  if (expanded) return catalog;
  const connectedInputs = new Set<string>();
  const matchIds = nodeMatchIds(node);
  for (const edge of edges) {
    if (!matchIds.has(edge.target)) continue;
    const parsed = parseWorkflowEdgeLabel(edge.label);
    connectedInputs.add(parsed?.targetEventType ?? `unresolved-edge:${edge.id}`);
  }
  const inputs = catalog.inputs.filter((port) => connectedInputs.has(port.eventType));
  return {
    inputs: inputs.map((port, index) => ({ ...port, index, count: inputs.length })),
    outputs: catalog.outputs,
  };
}

export function buildIssueLevelMap(issues?: WorkflowIssue[] | null) {
  const levels = new Map<string, 'error' | 'warning'>();
  for (const issue of Array.isArray(issues) ? issues : []) {
    if (!issue.nodeId) continue;
    const existing = levels.get(issue.nodeId);
    if (issue.severity === 'error' || !existing) {
      levels.set(issue.nodeId, issue.severity);
    }
  }
  return levels;
}

export function isGraphNodeKind(nodeKind: string): nodeKind is WorkflowNode['kind'] {
  return (
    nodeKind === 'trigger' ||
    nodeKind === 'stage' ||
    nodeKind === 'resource' ||
    nodeKind === 'gate' ||
    nodeKind === 'cond' ||
    nodeKind === 'wait' ||
    nodeKind === 'subworkflow' ||
    nodeKind === 'include' ||
    nodeKind === 'end'
  );
}

/** Where a wire meets a node's port, on a card with every one of its ports shown. */
export function edgeAnchor(
  node: WorkflowNode,
  direction: 'source' | 'target',
  portLabel: string | null,
  personas: PersonaEntry[],
  edges: WorkflowEdge[] = [],
) {
  if (!portLabel) return null;
  const catalog = nodePortCatalog(node, { personas, edges });
  const ports = direction === 'source' ? catalog.outputs : catalog.inputs;
  const port = ports.find((candidate) => candidate.eventType === portLabel);
  if (!port) return null;
  const measurements = new Map<string, WorkflowSize>([
    [node.id, workflowCardSize(node.kind, Math.max(catalog.inputs.length, catalog.outputs.length))],
  ]);
  return portAnchor(node, port, { measurements });
}

/** The point halfway along a cubic bezier. */
function bezierMidpoint(
  p0: { x: number; y: number },
  p1: { x: number; y: number },
  p2: { x: number; y: number },
  p3: { x: number; y: number },
) {
  return {
    x: (p0.x + 3 * p1.x + 3 * p2.x + p3.x) / 8,
    y: (p0.y + 3 * p1.y + 3 * p2.y + p3.y) / 8,
  };
}

/** A label on a wire: the event it carries, or a rework wire's limit. */
function WirePill({
  x,
  y,
  text,
  tone,
  testId,
}: {
  x: number;
  y: number;
  text: string;
  tone: 'event' | 'rework';
  testId: string;
}) {
  const width = text.length * 6.2 + 16;
  return (
    <g
      className="workflow-wire-pill"
      data-tone={tone}
      data-testid={testId}
      transform={`translate(${x - width / 2},${y - 9})`}
      pointerEvents="none"
    >
      <rect width={width} height={18} rx={9} />
      <text x={width / 2} y={12.5} textAnchor="middle">
        {text}
      </text>
    </g>
  );
}

/** What a rework wire's chip says: how often a run may go back this way. */
export function reworkLimitLabel(edge: Pick<WorkflowEdge, 'maxIterations'>): string {
  return edge.maxIterations ? `↩ up to ${edge.maxIterations}×` : '↩ no limit';
}

function WorkflowEdgePath({
  edge,
  edges,
  nodes,
  personas,
  portCatalogs,
  measurements,
  feedbackLane,
  selected,
  onSelect,
  showEvents,
  runPassed = false,
}: {
  edge: WorkflowEdge;
  edges: WorkflowEdge[];
  nodes: Map<string, WorkflowNode>;
  personas: PersonaEntry[];
  portCatalogs: ReadonlyMap<string, WorkflowNodePortCatalog>;
  measurements: ReadonlyMap<string, WorkflowSize>;
  feedbackLane: number;
  selected: boolean;
  onSelect: () => void;
  /** Write the event each wire carries on it. */
  showEvents: boolean;
  /** A run went this way: its source closed and its target was reached. */
  runPassed?: boolean;
}) {
  const src = nodes.get(edge.source);
  const tgt = nodes.get(edge.target);
  if (!src || !tgt) return null;
  const resolution = resolveWorkflowEdgePorts(edge, nodes, { personas, edges });
  if (!resolution.sourcePort || !resolution.targetPort) return null;
  const sourcePort = portCatalogs
    .get(src.id)
    ?.outputs.find((port) => port.eventType === resolution.sourcePort?.eventType);
  const targetPort = portCatalogs
    .get(tgt.id)
    ?.inputs.find((port) => port.eventType === resolution.targetPort?.eventType);
  if (!sourcePort || !targetPort) return null;
  const srcC = portAnchor(src, sourcePort, { measurements });
  const tgtC = portAnchor(tgt, targetPort, { measurements });
  const feedback = isReentryEdge(edge);
  const maxBottom = Math.max(
    ...[...nodes.values()].map((node) => {
      const bounds = nodeBounds(node, { measurements });
      return bounds.y + bounds.height;
    }),
  );
  // Work sent back runs under every card, one lane per rework wire, so it never crosses one.
  const laneY = maxBottom + 48 + feedbackLane * 30;
  const forwardDirection = tgtC.x >= srcC.x ? 1 : -1;
  const forwardBend = Math.max(32, Math.min(110, Math.abs(tgtC.x - srcC.x) * 0.5));
  const c1 = { x: srcC.x + forwardDirection * forwardBend, y: srcC.y };
  const c2 = { x: tgtC.x - forwardDirection * forwardBend, y: tgtC.y };
  const r = 12;
  const d = feedback
    ? [
        `M ${srcC.x} ${srcC.y}`,
        `L ${srcC.x + 22 - r} ${srcC.y}`,
        `Q ${srcC.x + 22} ${srcC.y} ${srcC.x + 22} ${srcC.y + r}`,
        `L ${srcC.x + 22} ${laneY - r}`,
        `Q ${srcC.x + 22} ${laneY} ${srcC.x + 22 - r} ${laneY}`,
        `L ${tgtC.x - 22 + r} ${laneY}`,
        `Q ${tgtC.x - 22} ${laneY} ${tgtC.x - 22} ${laneY - r}`,
        `L ${tgtC.x - 22} ${tgtC.y + r}`,
        `Q ${tgtC.x - 22} ${tgtC.y} ${tgtC.x - 22 + r} ${tgtC.y}`,
        `L ${tgtC.x} ${tgtC.y}`,
      ].join(' ')
    : `M ${srcC.x} ${srcC.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${tgtC.x} ${tgtC.y}`;
  const eventText =
    sourcePort.eventType === targetPort.eventType
      ? sourcePort.eventType
      : `${sourcePort.eventType} → ${targetPort.eventType}`;
  const middle = feedback
    ? { x: (srcC.x + tgtC.x) / 2, y: laneY }
    : bezierMidpoint(srcC, c1, c2, tgtC);
  return (
    <g
      className="workflow-wire"
      data-testid={`workflow-edge-${edge.id}`}
      data-source-node={edge.source}
      data-target-node={edge.target}
      data-source-event={sourcePort.eventType}
      data-target-event={targetPort.eventType}
      data-resolved={resolution.resolved ? 'true' : 'false'}
      data-feedback={feedback ? 'true' : 'false'}
      data-selected={selected ? 'true' : undefined}
      data-run-passed={runPassed ? 'true' : undefined}
    >
      <path
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={12}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        className="niuu:cursor-pointer"
      />
      <path
        d={d}
        fill="none"
        markerEnd={feedback ? 'url(#arrowhead-rework)' : 'url(#arrowhead)'}
        className="workflow-edge-line niuu:cursor-pointer"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      />
      {feedback ? (
        <WirePill
          x={middle.x}
          y={middle.y}
          text={`${reworkLimitLabel(edge)}${showEvents ? ` · ${sourcePort.eventType}` : ''}`}
          tone="rework"
          testId={`workflow-edge-limit-${edge.id}`}
        />
      ) : (
        (showEvents || selected) && (
          <WirePill
            x={middle.x}
            y={middle.y}
            text={eventText}
            tone="event"
            testId={`workflow-edge-event-${edge.id}`}
          />
        )
      )}
    </g>
  );
}

/** How an outcome is wired: on to a later step, back to an earlier one, or not yet. */
type OutcomeWiring = 'forward' | 'rework' | 'open';

function NodeSocketLayer({
  node,
  portCatalog,
  measurements,
  selected,
  wiring,
  connecting,
  readOnly,
  connectingFromLabel,
  onStartConnect,
  onCompleteConnect,
}: {
  node: WorkflowNode;
  portCatalog: WorkflowNodePortCatalog;
  measurements: ReadonlyMap<string, WorkflowSize>;
  selected: boolean;
  /** Each outcome's wiring, by event type. */
  wiring: ReadonlyMap<string, OutcomeWiring>;
  connecting: boolean;
  readOnly: boolean;
  connectingFromLabel?: string | null;
  onStartConnect: (eventType: string) => void;
  onCompleteConnect: (eventType: string) => void;
}) {
  const ports = [...portCatalog.inputs, ...portCatalog.outputs];
  // An end pill has room for no labels; every other card writes its ports inside its edges.
  const labelled = node.kind !== 'end';
  // Members of one stage can name outcomes alike ("pass"); such a name is written as its event.
  const shared = new Set<string>();
  for (const side of [portCatalog.inputs, portCatalog.outputs]) {
    const seen = new Set<string>();
    for (const port of side) {
      const key = `${port.direction}:${port.label}`;
      if (seen.has(key)) shared.add(key);
      seen.add(key);
    }
  }

  function activate(port: WorkflowNodePort) {
    if (readOnly) return;
    if (port.direction === 'output') {
      onStartConnect(port.eventType);
      return;
    }
    if (connecting) onCompleteConnect(port.eventType);
  }

  return (
    <g data-testid={`workflow-sockets-${node.id}`}>
      {ports.map((port) => {
        const anchor = portAnchor(node, port, { measurements });
        const active =
          port.direction === 'output' && connectingFromLabel === port.eventType && connecting;
        const interactive = !readOnly && (port.direction === 'output' || connecting);
        const outcome =
          port.direction === 'output' ? (wiring.get(port.eventType) ?? 'open') : undefined;
        const showLabel = labelled && (port.direction === 'output' || selected || connecting);
        return (
          <g key={port.id}>
            {showLabel && (
              <text
                className="workflow-socket__label"
                data-direction={port.direction}
                x={anchor.x + (port.direction === 'input' ? 13 : -13)}
                y={anchor.y + 4}
                textAnchor={port.direction === 'input' ? 'start' : 'end'}
                pointerEvents="none"
              >
                {truncateLabel(
                  shared.has(`${port.direction}:${port.label}`) ? port.eventType : port.label,
                  26,
                  24,
                )}
              </text>
            )}
            <circle
              data-workflow-socket="true"
              data-testid={`workflow-socket-${node.id}-${port.direction}-${port.index}`}
              data-node-id={node.id}
              data-direction={port.direction}
              data-event-type={port.eventType}
              data-wiring={outcome}
              data-resolved={port.resolved ? 'true' : 'false'}
              data-active={active ? 'true' : 'false'}
              cx={anchor.x}
              cy={anchor.y}
              r={port.direction === 'output' ? 6 : 5}
              role={interactive ? 'button' : undefined}
              tabIndex={interactive ? 0 : -1}
              aria-label={`${node.label}: ${port.label} ${port.direction}`}
              className={cn('workflow-socket', interactive && 'niuu:cursor-crosshair')}
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                activate(port);
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                event.stopPropagation();
                activate(port);
              }}
            >
              <title>{`${port.label} · ${port.eventType}${port.resolved ? '' : ' · unresolved'}`}</title>
            </circle>
          </g>
        );
      })}
    </g>
  );
}

/** Each outcome of each node, by how it is wired: a rework wire wins over a forward one. */
function outcomeWiring(
  nodes: readonly WorkflowNode[],
  edges: readonly WorkflowEdge[],
): Map<string, Map<string, OutcomeWiring>> {
  const owner = new Map<string, string>();
  for (const node of nodes) {
    for (const id of nodeMatchIds(node)) owner.set(id, node.id);
  }
  const wiring = new Map<string, Map<string, OutcomeWiring>>();
  for (const edge of edges) {
    const nodeId = owner.get(edge.source);
    const event = parseWorkflowEdgeLabel(edge.label)?.sourceEventType;
    if (!nodeId || !event) continue;
    const byEvent = wiring.get(nodeId) ?? new Map<string, OutcomeWiring>();
    if (byEvent.get(event) !== 'rework') {
      byEvent.set(event, isReentryEdge(edge) ? 'rework' : 'forward');
    }
    wiring.set(nodeId, byEvent);
  }
  return wiring;
}

// ---------------------------------------------------------------------------
// InsertFromPortMenu — "add a step from a port"
// ---------------------------------------------------------------------------

const FLOW_CONTROL_CANDIDATES: {
  kind: 'gate' | 'cond' | 'wait' | 'end';
  label: string;
  hint: string;
}[] = [
  { kind: 'gate', label: 'Human gate', hint: 'hold for approval, then continue' },
  { kind: 'cond', label: 'Condition', hint: 'route on a predicate' },
  { kind: 'wait', label: 'External wait', hint: 'resume on an outside observation' },
  { kind: 'end', label: 'End', hint: 'terminate the workflow here' },
];

/**
 * Candidate list shown while connecting from a typed output port: personas
 * whose `consumes` actually includes `eventType` (the port's type IS the
 * filter, not a search box the user has to use), plus flow-control blocks,
 * which accept any type by construction. Ported from
 * docs/mockups/workflow-builder's "add step from a port" panel.
 *
 * Personas insert via `addStageWithPersona`'s produces/consumes auto-wire;
 * flow-control blocks have no such profile to match, so they wire via
 * `addNodeFromPort`, which takes the exact source + event type instead.
 */
function InsertFromPortMenu({
  personas,
  eventType,
  anchor,
  onInsertPersona,
  onInsertFlowControl,
}: {
  personas: PersonaEntry[];
  eventType: string;
  anchor: { x: number; y: number };
  onInsertPersona: (personaId: string) => void;
  onInsertFlowControl?: (kind: 'gate' | 'cond' | 'wait' | 'end') => void;
}) {
  const compatible = personas.filter((p) => (p.consumes ?? []).includes(eventType));

  return (
    <div
      data-testid="insert-from-port-menu"
      className="workflow-canvas__picker niuu:absolute niuu:z-30 niuu:w-[300px] niuu:overflow-hidden niuu:rounded-xl niuu:backdrop-blur-md"
      style={{ left: anchor.x, top: anchor.y }}
    >
      <div className="niuu:border-b niuu:border-border-subtle niuu:px-3 niuu:py-2">
        <div className="niuu:font-mono niuu:text-[9px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint">
          Insert a step for
        </div>
        <div
          className="niuu:truncate niuu:font-mono niuu:text-[11px] niuu:text-brand"
          title={eventType}
        >
          {eventType}
        </div>
      </div>
      <div className="niuu:max-h-[280px] niuu:overflow-y-auto niuu:p-1.5">
        {compatible.length === 0 ? (
          <div className="niuu:px-2 niuu:py-2 niuu:text-[10.5px] niuu:text-text-muted">
            Nothing in the persona catalog consumes{' '}
            <span className="niuu:text-text-secondary">{eventType}</span>.
          </div>
        ) : (
          compatible.map((persona) => (
            <button
              key={persona.id}
              type="button"
              data-testid={`insert-from-port-${persona.id}`}
              onClick={() => onInsertPersona(persona.id)}
              className="niuu:flex niuu:w-full niuu:flex-col niuu:items-start niuu:gap-0.5 niuu:rounded-lg niuu:border niuu:border-transparent niuu:px-2.5 niuu:py-2 niuu:text-left niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary"
            >
              <span className="niuu:text-[11.5px] niuu:font-semibold niuu:text-text-primary">
                {persona.label}
              </span>
              {persona.produces && persona.produces.length > 0 && (
                <span className="niuu:font-mono niuu:text-[9px] niuu:text-text-faint">
                  → {persona.produces.join(', ')}
                </span>
              )}
            </button>
          ))
        )}

        {onInsertFlowControl && (
          <>
            <div className="niuu:mb-1 niuu:mt-2 niuu:px-2.5 niuu:font-mono niuu:text-[8.5px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint">
              Flow control · accepts any type
            </div>
            {FLOW_CONTROL_CANDIDATES.map((candidate) => (
              <button
                key={candidate.kind}
                type="button"
                data-testid={`insert-from-port-flow-${candidate.kind}`}
                onClick={() => onInsertFlowControl(candidate.kind)}
                className="niuu:flex niuu:w-full niuu:flex-col niuu:items-start niuu:gap-0.5 niuu:rounded-lg niuu:border niuu:border-transparent niuu:px-2.5 niuu:py-2 niuu:text-left niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary"
              >
                <span className="niuu:text-[11.5px] niuu:font-semibold niuu:text-text-primary">
                  {candidate.label}
                </span>
                <span className="niuu:font-mono niuu:text-[9px] niuu:text-text-faint">
                  {candidate.hint}
                </span>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GraphView
// ---------------------------------------------------------------------------

const RUN_STATE_LABELS: Record<RunNodeState, string> = {
  waiting: 'Needs you',
  paused: 'Paused',
  active: 'Working',
  failed: 'Failed',
  done: 'Done',
  pending: 'Not reached',
};

/** A verdict as a member's chip says it, or the member's state while it has none. */
function memberChipLabel(member: RunGraphNodeState['members'][number]): string {
  if (member.verdict) return `${member.member} · ${member.verdict}`;
  return `${member.member} · ${RUN_STATE_LABELS[member.state].toLowerCase()}`;
}

const RUN_CHIP_HEIGHT = 18;
const RUN_CHIP_GAP = 4;
/** How far a run's labels grow against zooming out: enough to read at a panel's fit. */
const RUN_LABEL_MAX_SCALE = 2.5;

/** The box of what is drawn, in the graph's own coordinates; none where nothing lays out. */
function drawnContentBox(
  content: SVGGElement | null,
): { x: number; y: number; width: number; height: number } | null {
  if (!content || typeof content.getBBox !== 'function') return null;
  const box = content.getBBox();
  if (box.width <= 0 || box.height <= 0) return null;
  return { x: box.x, y: box.y, width: box.width, height: box.height };
}

/**
 * Where a run is at one node, drawn over it: an outline in the state's colour, the state at
 * the node's top right, and each member of its newest round as a chip under it that opens
 * the member's session.
 */
function RunStateOverlay({
  bounds,
  state,
  labelScale,
  onOpenSession,
}: {
  bounds: { x: number; y: number; width: number; height: number };
  state: RunGraphNodeState;
  /** How much larger than the graph's scale the labels are drawn, to stay readable. */
  labelScale: number;
  onOpenSession?: (sessionId: string) => void;
}) {
  const label = RUN_STATE_LABELS[state.state];
  const labelWidth = label.length * 6.4 + 14;
  const pillHeight = 16;
  return (
    <g
      className="workflow-run-state"
      data-run-state={state.state}
      data-testid={`workflow-run-state-${state.nodeId}`}
    >
      <rect
        className="workflow-run-state__tint"
        x={bounds.x}
        y={bounds.y}
        width={bounds.width}
        height={bounds.height}
        rx={14}
        pointerEvents="none"
      />
      <rect
        className="workflow-run-state__outline"
        x={bounds.x - 3}
        y={bounds.y - 3}
        width={bounds.width + 6}
        height={bounds.height + 6}
        rx={17}
        fill="none"
        vectorEffect="non-scaling-stroke"
        pointerEvents="none"
      />
      {/* A step not reached says so by receding, with no label to crowd its neighbours. */}
      {state.state !== 'pending' && (
        <g
          transform={`translate(${bounds.x + bounds.width + 6},${bounds.y - 4}) scale(${labelScale}) translate(${-labelWidth},${-pillHeight + 6})`}
        >
          <rect
            className="workflow-run-state__pill"
            width={labelWidth}
            height={pillHeight}
            rx={8}
          />
          <text
            className="workflow-run-state__pill-text"
            x={labelWidth / 2}
            y={11.5}
            textAnchor="middle"
          >
            {label}
          </text>
        </g>
      )}
      <g
        transform={`translate(${bounds.x},${bounds.y + bounds.height + RUN_CHIP_GAP}) scale(${labelScale})`}
      >
        {state.members.map((member, index) => {
          const y = index * (RUN_CHIP_HEIGHT + RUN_CHIP_GAP);
          const text = memberChipLabel(member);
          const width = text.length * 6 + 18;
          const open = member.sessionId && onOpenSession ? onOpenSession : null;
          return (
            <g
              key={member.member}
              className="workflow-run-member"
              data-run-state={member.state}
              transform={`translate(0,${y})`}
              role={open ? 'button' : undefined}
              tabIndex={open ? 0 : undefined}
              aria-label={open ? `Open ${member.member}'s session` : undefined}
              data-testid={`workflow-run-member-${state.nodeId}-${member.member}`}
              onClick={open ? () => open(member.sessionId!) : undefined}
              onKeyDown={
                open
                  ? (event) => {
                      if (event.key === 'Enter') open(member.sessionId!);
                    }
                  : undefined
              }
            >
              <rect
                className="workflow-run-member__chip"
                width={width}
                height={RUN_CHIP_HEIGHT}
                rx={9}
              />
              <circle className="workflow-run-member__dot" cx={9} cy={RUN_CHIP_HEIGHT / 2} r={3} />
              <text className="workflow-run-member__text" x={16} y={12.5}>
                {text}
              </text>
            </g>
          );
        })}
      </g>
    </g>
  );
}

export interface GraphViewProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  personas?: PersonaEntry[];
  registryMounts?: WorkflowRegistryMount[];
  issues?: WorkflowIssue[];
  selectedNodeId: string | null;
  connectingFromId: string | null;
  connectingFromLabel?: string | null;
  onSelectNode: WorkflowBuilderActions['selectNode'];
  onInspectNode: WorkflowBuilderActions['inspectNode'];
  onAddNode: WorkflowBuilderActions['addNode'];
  onAddMimirResource?: WorkflowBuilderActions['addMimirResource'];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
  onDeleteEdge: WorkflowBuilderActions['deleteEdge'];
  onStartConnect: WorkflowBuilderActions['startConnect'];
  onCancelConnect: WorkflowBuilderActions['cancelConnect'];
  onCompleteConnect: WorkflowBuilderActions['completeConnect'];
  onAddPersonaToStage?: WorkflowBuilderActions['addPersonaToStage'];
  onAddStageWithPersona?: WorkflowBuilderActions['addStageWithPersona'];
  onAddStageFromPort?: WorkflowBuilderActions['addStageFromPort'];
  /** Powers the flow-control candidates in InsertFromPortMenu. Personas
   *  alone (onAddStageWithPersona) are enough to show the menu; this just
   *  adds the gate/cond/wait/end section to it. */
  onAddNodeFromPort?: WorkflowBuilderActions['addNodeFromPort'];
  /** System templates and other immutable workflows retain navigation and inspection. */
  readOnly?: boolean;
  /** A run drawn on its workflow: where the run is at each node, shown over the node. */
  runStates?: ReadonlyMap<string, RunGraphNodeState>;
  /** Open a member's session from its chip under a node of a run. */
  onOpenMemberSession?: (sessionId: string) => void;
  /** Fit the graph to the canvas once the canvas has a size, as a viewer that only shows it. */
  fitOnLoad?: boolean;
  /** Optional host action for making a read-only workflow editable. */
  onEdit?: () => void;
  readOnlyMessage?: string;
}

// Stable defaults: a fresh [] per render would lay the workflow out again on every render.
const NO_PERSONAS: PersonaEntry[] = [];
const NO_MOUNTS: WorkflowRegistryMount[] = [];
const NO_ISSUES: WorkflowIssue[] = [];

export function GraphView({
  nodes,
  edges,
  personas = NO_PERSONAS,
  registryMounts = NO_MOUNTS,
  issues = NO_ISSUES,
  selectedNodeId,
  connectingFromId,
  connectingFromLabel,
  onSelectNode,
  onInspectNode,
  onAddNode,
  onAddMimirResource,
  onDeleteNode,
  onDeleteEdge,
  onStartConnect,
  onCancelConnect,
  onCompleteConnect,
  onAddPersonaToStage,
  onAddStageWithPersona,
  onAddStageFromPort,
  onAddNodeFromPort,
  readOnly = false,
  onEdit,
  readOnlyMessage,
  runStates,
  onOpenMemberSession,
  fitOnLoad = false,
}: GraphViewProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const contentRef = useRef<SVGGElement>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  // A run's labels keep their size on screen as the graph zooms out, so its progress reads
  // at the scale a panel fits the whole workflow in; they never grow past it.
  const runLabelScale = Math.min(RUN_LABEL_MAX_SCALE, Math.max(1, 1 / transform.scale));
  const runPassed = (edge: WorkflowEdge): boolean => {
    if (!runStates || isReentryEdge(edge)) return false;
    const source = runStates.get(edge.source)?.state;
    const target = runStates.get(edge.target)?.state;
    return source === 'done' && target !== undefined && target !== 'pending';
  };
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const [canvasPicker, setCanvasPicker] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
    position: { x: number; y: number };
  } | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);
  const [showEvents, setShowEvents] = useState(true);

  const visiblePortCatalogs = useMemo(
    () =>
      new Map(
        nodes.map((node) => [
          node.id,
          visibleNodePortCatalog(
            node,
            personas,
            edges,
            node.id === selectedNodeId || connectingFromId !== null,
          ),
        ]),
      ),
    [connectingFromId, edges, nodes, personas, selectedNodeId],
  );
  // Every card is one recipe: a head, then a row per port on its busier side.
  const measurements = useMemo(() => {
    const sizes = new Map<string, WorkflowSize>();
    for (const node of nodes) {
      const ports = visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] };
      sizes.set(
        node.id,
        workflowCardSize(node.kind, Math.max(ports.inputs.length, ports.outputs.length)),
      );
    }
    return sizes;
  }, [nodes, visiblePortCatalogs]);
  // The canvas lays the workflow out itself, left to right: what follows a step stands to
  // its right, steps that run together stand above one another, and work sent back runs
  // underneath. Positions saved in the document are not used.
  const displayNodes = useMemo(() => {
    const owner = new Map<string, string>();
    for (const node of nodes) owner.set(node.id, node.id);
    for (const [localId, includeNode] of providedNodeIds({ nodes })) {
      if (!owner.has(localId)) owner.set(localId, includeNode.id);
    }
    const layoutEdges = edges
      .map((edge) => ({
        ...edge,
        source: owner.get(edge.source) ?? edge.source,
        target: owner.get(edge.target) ?? edge.target,
      }))
      .filter((edge) => edge.source !== edge.target);
    const { positions } = layoutWorkflow(
      nodes.map((node) => node.id),
      layoutEdges,
      { nodeSizes: measurements, originX: 40, originY: 40, columnGap: 120, rowGap: 56 },
    );
    return nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position }));
  }, [edges, measurements, nodes]);
  const wiring = useMemo(() => outcomeWiring(nodes, edges), [edges, nodes]);

  const nodeMap = useMemo(() => {
    const map = new Map<string, WorkflowNode>(displayNodes.map((n) => [n.id, n]));
    for (const [localId, includeNode] of providedNodeIds({ nodes: displayNodes })) {
      if (!map.has(localId)) map.set(localId, includeNode);
    }
    return map;
  }, [displayNodes]);
  const issueMap = useMemo(() => buildIssueLevelMap(issues), [issues]);
  const feedbackLanes = useMemo(() => feedbackLaneAssignments(edges), [edges]);
  const isConnectingMode = !readOnly && connectingFromId !== null;

  const zoomTo = useCallback((scale: number, clientPoint?: { x: number; y: number }) => {
    const svg = svgRef.current;
    setTransform((prev) => {
      const nextScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
      if (!svg || !clientPoint) return { ...prev, scale: nextScale };
      const rect = svg.getBoundingClientRect();
      const localX = clientPoint.x - rect.left;
      const localY = clientPoint.y - rect.top;
      const canvasX = (localX - prev.x) / prev.scale;
      const canvasY = (localY - prev.y) / prev.scale;
      return {
        x: localX - canvasX * nextScale,
        y: localY - canvasY * nextScale,
        scale: nextScale,
      };
    });
  }, []);

  const fitToNodes = useCallback(
    (scaleLimit = MAX_ZOOM) => {
      const svg = svgRef.current;
      if (!svg || nodes.length === 0) {
        setTransform({ x: 0, y: 0, scale: 1 });
        return;
      }
      const rect = svg.getBoundingClientRect();
      const drawn = drawnContentBox(contentRef.current);
      if (drawn) {
        // Frame what is drawn (rework lanes, wire labels, a run's chips) and centre it.
        const margin = 32;
        const scale = Math.min(
          scaleLimit,
          Math.max(
            fitOnLoad ? VIEWER_MIN_ZOOM : MIN_ZOOM,
            Math.min(
              rect.width / (drawn.width + margin * 2),
              rect.height / (drawn.height + margin * 2),
            ),
          ),
        );
        setTransform({
          x: (rect.width - drawn.width * scale) / 2 - drawn.x * scale,
          y: (rect.height - drawn.height * scale) / 2 - drawn.y * scale,
          scale,
        });
        return;
      }
      const bounds = displayNodes.map((node) => nodeBounds(node, { measurements }));
      const minX = Math.min(...bounds.map((box) => box.x)) - 48;
      const maxX = Math.max(...bounds.map((box) => box.x + box.width)) + 48;
      const minY = Math.min(...bounds.map((box) => box.y)) - 48;
      const cardBottom = Math.max(...bounds.map((box) => box.y + box.height));
      const feedbackCount = feedbackLaneAssignments(edges).size;
      const maxY =
        (feedbackCount > 0 ? cardBottom + 44 + (feedbackCount - 1) * 18 : cardBottom) + 48;
      const width = Math.max(1, maxX - minX);
      const height = Math.max(1, maxY - minY);
      const scale = Math.min(
        scaleLimit,
        Math.max(MIN_ZOOM, Math.min(rect.width / width, rect.height / height) * 0.88),
      );
      setTransform({
        x: (rect.width - width * scale) / 2 - minX * scale,
        y: (rect.height - height * scale) / 2 - minY * scale,
        scale,
      });
    },
    [displayNodes, edges, measurements, nodes.length, fitOnLoad],
  );

  // The canvas frames the workflow once it first has a size. A viewer frames it again whenever
  // its canvas changes size (it opens, a panel beside it comes or goes, the dock is resized):
  // it is not edited, so nothing else frames it. Zoom and pan stay the person's otherwise.
  // The size last framed, kept across refreshes of the graph so a refresh keeps the frame.
  const framedSizeRef = useRef('');
  useEffect(() => {
    if (nodes.length === 0) return;
    const svg = svgRef.current;
    if (!svg) return;
    let frame = 0;
    const fitWhenSized = () => {
      const rect = svg.getBoundingClientRect();
      const size = `${Math.round(rect.width)}x${Math.round(rect.height)}`;
      if (rect.width === 0 || rect.height === 0 || size === framedSizeRef.current) return;
      if (!fitOnLoad && framedSizeRef.current) return;
      framedSizeRef.current = size;
      fitToNodes(1);
      // A run's labels grow as the fit zooms out; frame again once they have.
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => fitToNodes(1));
    };
    fitWhenSized();
    if (typeof ResizeObserver === 'undefined') return () => cancelAnimationFrame(frame);
    const observer = new ResizeObserver(fitWhenSized);
    observer.observe(svg);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [fitOnLoad, fitToNodes, nodes.length]);

  const resetOneToOne = useCallback(() => {
    const svg = svgRef.current;
    if (!svg || nodes.length === 0) {
      setTransform({ x: 0, y: 0, scale: 1 });
      return;
    }
    const rect = svg.getBoundingClientRect();
    const bounds = displayNodes.map((node) => nodeBounds(node, { measurements }));
    const minX = Math.min(...bounds.map((box) => box.x));
    const maxX = Math.max(...bounds.map((box) => box.x + box.width));
    const minY = Math.min(...bounds.map((box) => box.y));
    const maxY = Math.max(...bounds.map((box) => box.y + box.height));
    setTransform({
      x: rect.width / 2 - (minX + maxX) / 2,
      y: rect.height / 2 - (minY + maxY) / 2,
      scale: 1,
    });
  }, [displayNodes, measurements, nodes.length]);

  // Wheel zoom
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const canvas = el;
    function handleWheel(e: WheelEvent) {
      e.preventDefault();
      const currentScale = Number(canvas.dataset.scale ?? '1');
      zoomTo(currentScale * (e.deltaY < 0 ? 1.1 : 0.9), { x: e.clientX, y: e.clientY });
    }
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', handleWheel);
  }, [zoomTo]);

  useEffect(() => {
    if (svgRef.current) svgRef.current.dataset.scale = String(transform.scale);
  }, [transform.scale]);

  const openCanvasPicker = useCallback(
    (point?: { x: number; y: number }) => {
      const bounds = rootRef.current!.getBoundingClientRect();
      const svgBounds = svgRef.current!.getBoundingClientRect();
      const anchor = point ??
        pointerRef.current ?? {
          x: bounds.left + bounds.width / 2,
          y: bounds.top + bounds.height / 2,
        };
      const width = Math.min(320, bounds.width - 24);
      const height = Math.min(readOnly ? 184 : 480, bounds.height - 24);
      setCanvasPicker({
        x: Math.max(12, Math.min(anchor.x - bounds.left, bounds.width - width - 12)),
        y: Math.max(12, Math.min(anchor.y - bounds.top, bounds.height - height - 12)),
        width,
        height,
        position: {
          x: (anchor.x - svgBounds.left - transform.x) / transform.scale,
          y: (anchor.y - svgBounds.top - transform.y) / transform.scale,
        },
      });
      onCancelConnect();
    },
    [onCancelConnect, readOnly, transform],
  );

  // Entering Edit gives the graph its keyboard shortcuts immediately.
  useEffect(() => {
    if (!readOnly) rootRef.current?.focus({ preventScroll: true });
  }, [readOnly]);

  // Key handler for Escape (cancel connect) and Delete (delete selected)
  useEffect(() => {
    if (!canvasPicker) return;
    function dismiss(event: PointerEvent) {
      if ((event.target as Element).closest('[data-testid="open-add-step"]')) return;
      if (!pickerRef.current?.contains(event.target as Node)) setCanvasPicker(null);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setCanvasPicker(null);
      rootRef.current?.focus();
    }
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [canvasPicker]);

  // "Next" adds a step after the selected one, on the outcome that goes nowhere yet (else its
  // first): the insert menu offers the steps that start on that outcome's event.
  const addNext = useCallback(
    (nodeId: string) => {
      const node = nodeMap.get(nodeId);
      const outputs = visiblePortCatalogs.get(nodeId)?.outputs ?? [];
      const byEvent = wiring.get(nodeId);
      const port =
        outputs.find((candidate) => !byEvent?.has(candidate.eventType)) ??
        outputs.find((candidate) => byEvent?.get(candidate.eventType) === 'forward') ??
        outputs[0];
      if (!node || !port) {
        openCanvasPicker();
        return;
      }
      const sourceId = resolveIncludeConnectionId(node, port.eventType, 'source', edges);
      if (sourceId) onStartConnect(sourceId, port.eventType);
    },
    [edges, nodeMap, onStartConnect, openCanvasPicker, visiblePortCatalogs, wiring],
  );
  // "Alongside" adds a step that starts on what the selected one starts on, from the same
  // step, so the two run at the same time.
  const alongsideOf = useCallback(
    (nodeId: string) => {
      const node = nodeMap.get(nodeId);
      if (!node) return null;
      const ids = nodeMatchIds(node);
      const incoming = edges.find((edge) => ids.has(edge.target) && !isReentryEdge(edge));
      const eventType = incoming ? parseWorkflowEdgeLabel(incoming.label)?.sourceEventType : null;
      return incoming && eventType ? { sourceId: incoming.source, eventType } : null;
    },
    [edges, nodeMap],
  );
  const addAlongside = useCallback(
    (nodeId: string) => {
      const from = alongsideOf(nodeId);
      if (from) onStartConnect(from.sourceId, from.eventType);
    },
    [alongsideOf, onStartConnect],
  );

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const editable =
        target?.isContentEditable ||
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.tagName === 'SELECT';
      if (editable || !rootRef.current?.contains(document.activeElement)) return;
      if (e.key === 'Escape') onCancelConnect();
      const onCanvas =
        target === rootRef.current ||
        (target instanceof SVGElement && svgRef.current?.contains(target));
      if (
        !readOnly &&
        e.key === 'Tab' &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !canvasPicker &&
        !connectingFromId &&
        selectedNodeId &&
        onCanvas
      ) {
        e.preventDefault();
        if (e.shiftKey) addAlongside(selectedNodeId);
        else addNext(selectedNodeId);
        return;
      }
      if (
        !readOnly &&
        e.key === 'Tab' &&
        !e.shiftKey &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !canvasPicker &&
        (target === rootRef.current ||
          (target instanceof SVGElement && svgRef.current?.contains(target)))
      ) {
        e.preventDefault();
        openCanvasPicker();
      }
      if (!readOnly && (e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
        e.preventDefault();
        onDeleteNode(selectedNodeId);
      }
      if (!readOnly && (e.key === 'Delete' || e.key === 'Backspace') && selectedEdgeId) {
        e.preventDefault();
        onDeleteEdge(selectedEdgeId);
        setSelectedEdgeId(null);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    onCancelConnect,
    onDeleteEdge,
    onDeleteNode,
    readOnly,
    selectedEdgeId,
    selectedNodeId,
    canvasPicker,
    openCanvasPicker,
    addAlongside,
    addNext,
    connectingFromId,
  ]);

  function handleSvgMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (e.button === 0 && e.ctrlKey) {
      handleCanvasContextMenu(e);
      return;
    }
    if (e.button === 2) return;
    if ((e.target as Element).closest('[data-testid^="workflow-node"]')) return;
    if ((e.target as Element).closest('[data-testid^="workflow-edge"]')) return;
    if (isConnectingMode) {
      onCancelConnect();
      return;
    }
    setSelectedEdgeId(null);
    onSelectNode(null);
    panRef.current = { startX: e.clientX, startY: e.clientY, tx: transform.x, ty: transform.y };
    setIsPanning(true);
  }

  function handleSvgMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const pan = panRef.current;
    if (!pan) return;
    const { clientX, clientY } = e;
    setTransform((prev) => ({
      ...prev,
      x: pan.tx + (clientX - pan.startX),
      y: pan.ty + (clientY - pan.startY),
    }));
  }

  function handleSvgMouseUp() {
    panRef.current = null;
    setIsPanning(false);
  }

  function eventToCanvasPosition(clientX: number, clientY: number) {
    const svg = svgRef.current;
    if (!svg) return { x: 120, y: 120 };
    const rect = svg.getBoundingClientRect();
    return {
      x: (clientX - rect.left - transform.x) / transform.scale,
      y: (clientY - rect.top - transform.y) / transform.scale,
    };
  }

  function handleCanvasContextMenu(event: React.MouseEvent<SVGSVGElement>) {
    if (
      (event.target as Element).closest(
        '[data-testid^="workflow-node-"], [data-testid^="workflow-edge-"], [data-workflow-socket]',
      )
    )
      return;
    event.preventDefault();
    openCanvasPicker({ x: event.clientX, y: event.clientY });
  }

  function closeCanvasPicker() {
    setCanvasPicker(null);
    rootRef.current?.focus();
  }

  function findStageAtPoint(x: number, y: number) {
    return displayNodes.find((node) => {
      if (node.kind !== 'stage') return false;
      const bounds = nodeBounds(node, { measurements });
      return (
        x >= bounds.x &&
        x <= bounds.x + bounds.width &&
        y >= bounds.y &&
        y <= bounds.y + bounds.height
      );
    });
  }

  function handleDrop(e: React.DragEvent<SVGSVGElement>) {
    e.preventDefault();
    if (readOnly) return;
    const position = eventToCanvasPosition(e.clientX, e.clientY);
    const personaId = e.dataTransfer.getData('application/niuu-persona-id');
    const nodeKind = e.dataTransfer.getData('application/niuu-node-kind');
    const mimirMount = parseWorkflowRegistryMount(e.dataTransfer.getData(MIMIR_MOUNT_MIME));

    if (personaId) {
      const targetStage = findStageAtPoint(position.x, position.y);
      if (targetStage?.kind === 'stage') {
        onAddPersonaToStage?.(targetStage.id, personaId);
        onSelectNode(targetStage.id);
        return;
      }

      onAddStageWithPersona?.(personaId, undefined, position);
      return;
    }

    if (mimirMount) {
      onAddMimirResource?.(mimirMount, position);
      return;
    }

    if (isGraphNodeKind(nodeKind)) {
      onAddNode(nodeKind, position);
    }
  }

  // A click on a card's body while a wire is drawn wires it to the card's own input: the
  // event itself where the step starts on it, else the card's one input. A stage that does
  // not start on the event, and an include (whose inlined steps need an explicit port), need
  // a click on the port instead.
  function bodyConnect(node: WorkflowNode) {
    if (readOnly) return;
    if (node.kind === 'include') return;
    if (node.kind === 'stage') {
      const inputs = nodePortCatalog(node, { personas, edges }).inputs;
      if (connectingFromLabel && inputs.some((port) => port.eventType === connectingFromLabel)) {
        onCompleteConnect(node.id, connectingFromLabel);
      }
      return;
    }
    onCompleteConnect(node.id);
  }

  const toolbarBtnClass =
    'niuu:bg-bg-elevated niuu:text-text-primary niuu:border niuu:border-border niuu:rounded niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:cursor-pointer niuu:font-sans';

  const connectingPickerAnchor = useMemo(() => {
    if (!connectingFromId || !connectingFromLabel) return { x: 24, y: 80 };
    const source = nodeMap.get(connectingFromId);
    if (!source) return { x: 24, y: 80 };
    const output = visiblePortCatalogs
      .get(source.id)
      ?.outputs.find((port) => port.eventType === connectingFromLabel);
    if (!output) return { x: 24, y: 80 };
    const anchor = portAnchor(source, output, { measurements });
    return {
      x: transform.x + anchor.x * transform.scale + 16,
      y: transform.y + anchor.y * transform.scale - 24,
    };
  }, [
    connectingFromId,
    connectingFromLabel,
    measurements,
    nodeMap,
    transform,
    visiblePortCatalogs,
  ]);

  return (
    <div
      ref={rootRef}
      data-testid="graph-view"
      className="workflow-canvas niuu:flex-1 niuu:relative niuu:overflow-hidden niuu:min-h-[400px]"
      tabIndex={0}
      aria-label="Workflow canvas"
      onMouseDownCapture={(event) => {
        if (event.button === 0 && svgRef.current?.contains(event.target as Node)) {
          rootRef.current?.focus({ preventScroll: true });
        }
      }}
      onMouseMove={(event) => {
        if (svgRef.current?.contains(event.target as Node)) {
          pointerRef.current = { x: event.clientX, y: event.clientY };
        }
      }}
      onMouseLeave={() => {
        pointerRef.current = null;
      }}
    >
      {!readOnly && (
        <div className="niuu:absolute niuu:bottom-4 niuu:left-4 niuu:z-20">
          <button
            type="button"
            data-testid="open-add-step"
            onClick={() => (canvasPicker ? closeCanvasPicker() : openCanvasPicker())}
            className={toolbarBtnClass}
            aria-expanded={canvasPicker !== null}
            aria-haspopup="dialog"
          >
            + Add node <span className="niuu:ml-2 niuu:font-mono niuu:text-text-faint">Tab</span>
          </button>
        </div>
      )}

      {canvasPicker && (
        <div
          ref={pickerRef}
          role="dialog"
          aria-label="Add workflow node"
          data-testid="canvas-node-picker"
          className="niuu:absolute niuu:z-30"
          style={{
            left: canvasPicker.x,
            top: canvasPicker.y,
            width: canvasPicker.width,
            height: canvasPicker.height,
          }}
        >
          {readOnly ? (
            <div className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-4 niuu:shadow-2xl niuu:text-sm niuu:text-text-primary">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <strong>Viewing saved workflow</strong>
                <button
                  type="button"
                  onClick={closeCanvasPicker}
                  aria-label="Close node picker"
                  className={toolbarBtnClass}
                >
                  ×
                </button>
              </div>
              <p className="niuu:my-3 niuu:text-xs niuu:text-text-secondary">
                {readOnlyMessage ??
                  (onEdit
                    ? 'Choose Edit to add or change nodes. Saving creates a new version.'
                    : 'You do not have permission to edit this workflow.')}
              </p>
              {onEdit && (
                <button
                  type="button"
                  className={toolbarBtnClass}
                  onClick={() => {
                    closeCanvasPicker();
                    onEdit();
                  }}
                >
                  Edit workflow
                </button>
              )}
            </div>
          ) : (
            <LibraryPanel
              embedded
              personas={onAddStageWithPersona ? personas : []}
              registryMounts={registryMounts}
              onClose={closeCanvasPicker}
              onAddNode={(kind) => onAddNode(kind, canvasPicker.position)}
              onAddPersona={
                onAddStageWithPersona
                  ? (id) => onAddStageWithPersona(id, undefined, canvasPicker.position)
                  : undefined
              }
              onAddMimirResource={
                onAddMimirResource
                  ? (mount) => onAddMimirResource(mount, canvasPicker.position)
                  : undefined
              }
            />
          )}
        </div>
      )}

      {/* The selected step's toolbar, above its card: add a step after it or beside it, or
          remove it. */}
      {!readOnly &&
        selectedNodeId &&
        !isConnectingMode &&
        (() => {
          const node = displayNodes.find((candidate) => candidate.id === selectedNodeId);
          if (!node) return null;
          const bounds = nodeBounds(node, { measurements });
          const hasOutputs = (visiblePortCatalogs.get(node.id)?.outputs.length ?? 0) > 0;
          const besideFrom = alongsideOf(node.id);
          return (
            <div
              className="workflow-step-toolbar"
              data-testid="step-toolbar"
              role="toolbar"
              aria-label={`${node.label} actions`}
              style={{
                left: transform.x + (bounds.x + bounds.width / 2) * transform.scale,
                top: transform.y + bounds.y * transform.scale - 46,
              }}
            >
              {hasOutputs && (
                <button
                  type="button"
                  data-testid="step-toolbar-next"
                  title="Add a step after this one (Tab)"
                  onClick={() => addNext(node.id)}
                >
                  <span aria-hidden="true">＋</span> Next
                </button>
              )}
              {besideFrom && (
                <button
                  type="button"
                  data-testid="step-toolbar-alongside"
                  title="Add a step that runs at the same time as this one (Shift+Tab)"
                  onClick={() => addAlongside(node.id)}
                >
                  <span aria-hidden="true">⇉</span> Alongside
                </button>
              )}
              {(hasOutputs || besideFrom) && <span className="workflow-step-toolbar__sep" />}
              <button
                type="button"
                data-testid="delete-selected"
                className="workflow-step-toolbar__danger"
                title="Remove this step (Delete)"
                aria-label={`Remove ${node.label}`}
                onClick={() => onDeleteNode(node.id)}
              >
                Remove
              </button>
            </div>
          );
        })()}

      {!readOnly && (selectedEdgeId || isConnectingMode) && (
        <div className="niuu:absolute niuu:bottom-4 niuu:left-1/2 niuu:z-20 niuu:flex niuu:-translate-x-1/2 niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-1.5">
          {selectedEdgeId && (
            <button
              data-testid="delete-selected-edge"
              onClick={() => {
                onDeleteEdge(selectedEdgeId);
                setSelectedEdgeId(null);
              }}
              className={cn(toolbarBtnClass, 'niuu:text-critical')}
            >
              Delete connection
            </button>
          )}
          {isConnectingMode && (
            <span className="niuu:px-2 niuu:text-xs niuu:text-text-secondary">
              Pick the step it goes to, or one below to add
            </span>
          )}
        </div>
      )}

      <div
        className="niuu:absolute niuu:right-4 niuu:bottom-4 niuu:z-20 niuu:flex niuu:overflow-hidden niuu:rounded-md niuu:shadow-lg"
        aria-label="Canvas zoom controls"
      >
        <button
          type="button"
          data-testid="toggle-events"
          className="workflow-canvas__control"
          aria-pressed={showEvents}
          title="Write the event each wire carries on it"
          onClick={() => setShowEvents((shown) => !shown)}
        >
          Events
        </button>
        <button
          type="button"
          data-testid="zoom-out"
          className="workflow-canvas__control"
          onClick={() => zoomTo(transform.scale / 1.15)}
          aria-label="Zoom out"
        >
          −
        </button>
        <output
          data-testid="zoom-level"
          className="workflow-canvas__control niuu:min-w-[54px] niuu:cursor-default niuu:border-x-0"
          aria-live="polite"
        >
          {Math.round(transform.scale * 100)}%
        </output>
        <button
          type="button"
          data-testid="zoom-in"
          className="workflow-canvas__control"
          onClick={() => zoomTo(transform.scale * 1.15)}
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          data-testid="zoom-one-to-one"
          className="workflow-canvas__control niuu:border-l-0"
          onClick={resetOneToOne}
        >
          1:1
        </button>
        <button
          type="button"
          data-testid="zoom-fit"
          className="workflow-canvas__control niuu:border-l-0"
          onClick={() => fitToNodes()}
        >
          Fit
        </button>
      </div>

      {/* Insert-from-port candidates — while connecting from a typed output,
          offer personas that actually consume it (or a flow-control block,
          which accepts any type) as an alternative to clicking an existing
          node's input. Picking a persona both creates the stage AND wires
          the edge back to the port we started from, via the same auto-wire
          logic LibraryPanel's persona drag already uses (addStageWithPersona
          matches produces/consumes across every node, including the one
          currently mid-connect). Flow-control candidates wire the same edge
          explicitly via addNodeFromPort, since they have no produces/consumes
          profile to auto-match against. */}
      {isConnectingMode && connectingFromLabel && onAddStageWithPersona && (
        <InsertFromPortMenu
          personas={personas}
          eventType={connectingFromLabel}
          anchor={connectingPickerAnchor}
          onInsertPersona={(personaId) => {
            const sourceNode = connectingFromId ? nodeMap.get(connectingFromId) : undefined;
            const position = sourceNode
              ? { x: sourceNode.position.x + CARD_WIDTH + 120, y: sourceNode.position.y }
              : undefined;
            if (onAddStageFromPort && connectingFromId) {
              onAddStageFromPort(connectingFromId, connectingFromLabel, personaId, position);
            } else {
              onAddStageWithPersona(personaId, undefined, position);
            }
            onCancelConnect();
          }}
          onInsertFlowControl={
            onAddNodeFromPort && connectingFromId
              ? (kind) => {
                  const sourceNode = nodeMap.get(connectingFromId);
                  const position = sourceNode
                    ? { x: sourceNode.position.x + CARD_WIDTH + 120, y: sourceNode.position.y }
                    : undefined;
                  onAddNodeFromPort(kind, connectingFromId, connectingFromLabel, position);
                  onCancelConnect();
                }
              : undefined
          }
        />
      )}

      {/* SVG Canvas */}
      <svg
        ref={svgRef}
        data-testid="graph-canvas"
        className="niuu:w-full niuu:h-full"
        style={{ cursor: isConnectingMode ? 'crosshair' : isPanning ? 'grabbing' : 'default' }}
        onMouseDown={handleSvgMouseDown}
        onMouseMove={handleSvgMouseMove}
        onMouseUp={handleSvgMouseUp}
        onMouseLeave={handleSvgMouseUp}
        onContextMenu={handleCanvasContextMenu}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
      >
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon className="workflow-arrow" points="0 0, 8 3, 0 6" />
          </marker>
          <marker
            id="arrowhead-rework"
            markerWidth="8"
            markerHeight="6"
            refX="8"
            refY="3"
            orient="auto"
          >
            <polygon className="workflow-arrow" data-tone="rework" points="0 0, 8 3, 0 6" />
          </marker>
        </defs>
        <g
          ref={contentRef}
          transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}
        >
          {/* Edges (drawn first, below nodes) */}
          {edges.map((edge) => (
            <WorkflowEdgePath
              key={edge.id}
              edge={edge}
              edges={edges}
              nodes={nodeMap}
              personas={personas}
              portCatalogs={visiblePortCatalogs}
              measurements={measurements}
              feedbackLane={feedbackLanes.get(edge.id) ?? 0}
              runPassed={runPassed(edge)}
              showEvents={showEvents}
              selected={edge.id === selectedEdgeId}
              onSelect={() => {
                setSelectedEdgeId(edge.id);
                onSelectNode(null);
              }}
            />
          ))}
          {/* Nodes */}
          {displayNodes.map((node) => {
            const bounds = nodeBounds(node, { measurements });
            const ports = visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] };
            const waitPorts = node.kind === 'wait' ? waitPortLists(node.id, edges) : null;
            return (
              <g key={node.id}>
                <WorkflowNodeCard
                  node={node}
                  bounds={bounds}
                  content={workflowCardContent(node, personas)}
                  hasPorts={ports.inputs.length + ports.outputs.length > 0}
                  selected={node.id === selectedNodeId}
                  issueLevel={issueMap.get(node.id) ?? null}
                  isConnectingMode={isConnectingMode}
                  onSelect={() => {
                    setSelectedEdgeId(null);
                    onSelectNode(node.id);
                  }}
                  onInspect={() => onInspectNode(node.id)}
                  onBodyConnect={() => bodyConnect(node)}
                />
                {node.kind === 'wait' && waitPorts && (
                  <WaitUnconfiguredSockets
                    node={node}
                    bounds={bounds}
                    incomingEvents={waitPorts.incomingEvents}
                    outgoingEvents={waitPorts.outgoingEvents}
                    isConnectingMode={isConnectingMode}
                    readOnly={readOnly}
                    connectingFromLabel={connectingFromLabel}
                    onStartConnect={(eventType) => onStartConnect(node.id, eventType)}
                    onCompleteConnect={(eventType) => onCompleteConnect(node.id, eventType)}
                  />
                )}
              </g>
            );
          })}
          {displayNodes.map((node) => (
            <NodeSocketLayer
              key={`sockets-${node.id}`}
              node={node}
              portCatalog={visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] }}
              measurements={measurements}
              selected={node.id === selectedNodeId}
              wiring={wiring.get(node.id) ?? new Map()}
              connecting={isConnectingMode}
              readOnly={readOnly}
              connectingFromLabel={connectingFromLabel}
              onStartConnect={(eventType) => {
                const sourceId = resolveIncludeConnectionId(node, eventType, 'source', edges);
                if (sourceId) onStartConnect(sourceId, eventType);
              }}
              onCompleteConnect={(eventType) => {
                const targetId = resolveIncludeConnectionId(node, eventType, 'target', edges);
                if (targetId) onCompleteConnect(targetId, eventType);
              }}
            />
          ))}
          {runStates &&
            displayNodes.map((node) => {
              const state = runStates.get(node.id);
              if (!state) return null;
              return (
                <RunStateOverlay
                  key={`run-${node.id}`}
                  bounds={nodeBounds(node, { measurements })}
                  state={state}
                  labelScale={runLabelScale}
                  onOpenSession={onOpenMemberSession}
                />
              );
            })}
        </g>
      </svg>
    </div>
  );
}
