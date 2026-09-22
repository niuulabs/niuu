import type { WorkflowEdge, WorkflowNode, WorkflowStageNode } from './workflow';
import { parseWorkflowEdgeLabel } from './workflowSemantics';

export type WorkflowPortDirection = 'input' | 'output';
export type WorkflowPortSide = 'left' | 'right';
export type WorkflowPortOrigin = 'node' | 'persona' | 'stage-binding' | 'edge';

export interface WorkflowPortPersona {
  id: string;
  consumes?: readonly string[];
  produces?: readonly string[];
  outcomeEvents?: Readonly<Record<string, string>>;
}

export interface WorkflowNodePort {
  /** Stable within a node while direction and event type remain unchanged. */
  id: string;
  eventType: string;
  label: string;
  direction: WorkflowPortDirection;
  side: WorkflowPortSide;
  origin: WorkflowPortOrigin;
  resolved: boolean;
  unresolvedReason?: string;
  /** Zero-based position among ports on the same side. */
  index: number;
  /** Total ports on the same side, used by shared geometry. */
  count: number;
}

export interface WorkflowNodePortCatalog {
  inputs: WorkflowNodePort[];
  outputs: WorkflowNodePort[];
}

export interface WorkflowPortCatalogContext {
  personas?: readonly WorkflowPortPersona[];
  edges?: readonly WorkflowEdge[];
}

export interface WorkflowEdgePortResolution {
  edgeId: string;
  sourcePort: WorkflowNodePort | null;
  targetPort: WorkflowNodePort | null;
  resolved: boolean;
  reason?: string;
}

interface PortSeed {
  eventType: string;
  label?: string;
  origin: WorkflowPortOrigin;
  resolved: boolean;
  unresolvedReason?: string;
}

function humanizeEvent(eventType: string): string {
  const segment = eventType.split('.').at(-1) ?? eventType;
  return segment.replace(/[_-]+/g, ' ');
}

export function workflowPortId(direction: WorkflowPortDirection, eventType: string): string {
  return `${direction}:${encodeURIComponent(eventType)}`;
}

function appendSeed(target: Map<string, PortSeed>, seed: PortSeed): void {
  const eventType = seed.eventType.trim();
  if (!eventType) return;
  const previous = target.get(eventType);
  if (!previous || (!previous.resolved && seed.resolved)) {
    target.set(eventType, { ...seed, eventType });
    return;
  }
  if (previous.resolved === seed.resolved && previous.origin === 'edge' && seed.origin !== 'edge') {
    target.set(eventType, { ...seed, eventType });
  }
}

function stageContract(
  node: WorkflowStageNode,
  personas: readonly WorkflowPortPersona[],
): { inputs: Map<string, PortSeed>; outputs: Map<string, PortSeed>; incomplete: boolean } {
  const inputs = new Map<string, PortSeed>();
  const outputs = new Map<string, PortSeed>();
  const personaById = new Map(personas.map((persona) => [persona.id, persona]));
  const members =
    node.stageMembers && node.stageMembers.length > 0
      ? node.stageMembers
      : (node.personaIds ?? []).map((personaId) => ({ personaId, consumesEventTypes: [] }));
  let incomplete = members.length === 0;

  for (const member of members) {
    const persona = personaById.get(member.personaId);
    const scopedConsumes = member.consumesEventTypes ?? [];
    const consumes = scopedConsumes.length > 0 ? scopedConsumes : (persona?.consumes ?? []);
    for (const eventType of consumes) {
      appendSeed(inputs, {
        eventType,
        origin: scopedConsumes.length > 0 ? 'stage-binding' : 'persona',
        resolved: true,
      });
    }

    if (!persona) {
      incomplete = true;
      continue;
    }
    for (const [outcome, eventType] of Object.entries(persona.outcomeEvents ?? {})) {
      appendSeed(outputs, {
        eventType,
        label: outcome.replace(/[_-]+/g, ' '),
        origin: 'persona',
        resolved: true,
      });
    }
    for (const eventType of persona.produces ?? []) {
      appendSeed(outputs, { eventType, origin: 'persona', resolved: true });
    }
  }

  return { inputs, outputs, incomplete };
}

function nodeContract(
  node: WorkflowNode,
  personas: readonly WorkflowPortPersona[],
): {
  inputs: Map<string, PortSeed>;
  outputs: Map<string, PortSeed>;
  openInputs: boolean;
  openOutputs: boolean;
  incomplete: boolean;
} {
  const inputs = new Map<string, PortSeed>();
  const outputs = new Map<string, PortSeed>();
  if (node.kind === 'stage') {
    const stage = stageContract(node, personas);
    return { ...stage, openInputs: false, openOutputs: false };
  }

  if (node.kind === 'trigger') {
    appendSeed(outputs, {
      eventType: node.dispatchEvent ?? 'code.requested',
      origin: 'node',
      resolved: true,
    });
    return { inputs, outputs, openInputs: false, openOutputs: false, incomplete: false };
  }
  if (node.kind === 'gate') {
    appendSeed(inputs, { eventType: 'approval.requested', origin: 'node', resolved: true });
    if (node.approvalEvent) {
      appendSeed(outputs, {
        eventType: node.approvalEvent,
        label: 'approved',
        origin: 'node',
        resolved: true,
      });
    }
    if (node.changesRequestedEvent) {
      appendSeed(outputs, {
        eventType: node.changesRequestedEvent,
        label: 'changes requested',
        origin: 'node',
        resolved: true,
      });
    }
    return { inputs, outputs, openInputs: true, openOutputs: true, incomplete: false };
  }
  if (node.kind === 'cond') {
    appendSeed(inputs, { eventType: 'condition.input', origin: 'node', resolved: true });
    return { inputs, outputs, openInputs: true, openOutputs: true, incomplete: false };
  }
  if (node.kind === 'subworkflow') {
    appendSeed(inputs, { eventType: 'children.requested', origin: 'node', resolved: true });
    return { inputs, outputs, openInputs: true, openOutputs: true, incomplete: false };
  }
  if (node.kind === 'wait') {
    return { inputs, outputs, openInputs: true, openOutputs: true, incomplete: false };
  }
  if (node.kind === 'include') {
    // Every port comes from the parent edges that touch a provided local
    // id — there is no fixed contract to seed, unlike gate/cond's one known
    // input.
    return { inputs, outputs, openInputs: true, openOutputs: true, incomplete: false };
  }
  if (node.kind === 'end') {
    return { inputs, outputs, openInputs: true, openOutputs: false, incomplete: false };
  }
  return { inputs, outputs, openInputs: false, openOutputs: false, incomplete: false };
}

/**
 * Ids an edge may reference to mean "this node" — a node's own id for every
 * kind except `include`, whose provided local ids (the ids a pinned
 * workflow's stages/gates take in this graph) are equally valid endpoints
 * even though they never appear in `nodes` themselves. Exported so canvas
 * code filtering edges "connected to this node" (e.g. which ports to show
 * collapsed) stays consistent with how ports are actually derived here.
 */
export function nodeMatchIds(node: WorkflowNode): ReadonlySet<string> {
  if (node.kind === 'include') {
    return new Set([node.id, ...Object.values(node.nodes ?? {})]);
  }
  return new Set([node.id]);
}

/** Asked only when an event could belong to several included nodes. */
export type IncludedNodeChooser = (
  eventType: string,
  providedIds: readonly string[],
) => string | null;

/**
 * The node id an edge should use when it connects `eventType` to `node`.
 *
 * Any other kind of node is its own endpoint. An include node is never an
 * endpoint itself: the edge attaches to one of the local ids it provides. The
 * id an existing edge already uses for that event is kept, a sole provided id
 * needs no question, and otherwise the author chooses.
 */
export function resolveIncludeEndpoint(
  node: WorkflowNode,
  eventType: string,
  direction: 'source' | 'target',
  edges: readonly WorkflowEdge[],
  choose: IncludedNodeChooser,
): string | null {
  if (node.kind !== 'include') return node.id;
  const providedIds = Object.values(node.nodes ?? {});
  if (providedIds.length === 0) return null;

  const reused = providedIds.find((candidate) =>
    edges.some((edge) => {
      const endpoint = direction === 'source' ? edge.source : edge.target;
      if (endpoint !== candidate) return false;
      const parsed = parseWorkflowEdgeLabel(edge.label);
      if (!parsed) return false;
      const edgeEvent = direction === 'source' ? parsed.sourceEventType : parsed.targetEventType;
      return edgeEvent === eventType;
    }),
  );
  if (reused) return reused;
  if (providedIds.length === 1) return providedIds[0]!;

  const choice = choose(eventType, providedIds)?.trim();
  return choice && providedIds.includes(choice) ? choice : null;
}

function addEdgePorts(
  node: WorkflowNode,
  edges: readonly WorkflowEdge[],
  inputs: Map<string, PortSeed>,
  outputs: Map<string, PortSeed>,
  contract: { openInputs: boolean; openOutputs: boolean; incomplete: boolean },
): void {
  const matchIds = nodeMatchIds(node);
  for (const edge of edges) {
    const isSource = matchIds.has(edge.source);
    const isTarget = matchIds.has(edge.target);
    if (!isSource && !isTarget) continue;
    const parsed = parseWorkflowEdgeLabel(edge.label);
    if (!parsed) {
      const eventType = `unresolved-edge:${edge.id}`;
      const seed: PortSeed = {
        eventType,
        label: 'unresolved edge',
        origin: 'edge',
        resolved: false,
        unresolvedReason: 'The saved edge has no valid source → target event contract.',
      };
      if (isSource) appendSeed(outputs, seed);
      if (isTarget) appendSeed(inputs, seed);
      continue;
    }

    if (isSource && !outputs.has(parsed.sourceEventType)) {
      const resolved = contract.openOutputs;
      appendSeed(outputs, {
        eventType: parsed.sourceEventType,
        origin: 'edge',
        resolved,
        unresolvedReason: resolved
          ? undefined
          : contract.incomplete
            ? 'The source contract is unavailable because a stage persona could not be resolved.'
            : 'The source event is no longer declared by this node.',
      });
    }
    if (isTarget && !inputs.has(parsed.targetEventType)) {
      const resolved = contract.openInputs;
      appendSeed(inputs, {
        eventType: parsed.targetEventType,
        origin: 'edge',
        resolved,
        unresolvedReason: resolved
          ? undefined
          : contract.incomplete
            ? 'The target contract is unavailable because a stage persona could not be resolved.'
            : 'The target event is no longer consumed by this node.',
      });
    }
  }
}

function finishPorts(
  seeds: Map<string, PortSeed>,
  direction: WorkflowPortDirection,
): WorkflowNodePort[] {
  const ordered = [...seeds.values()].sort((left, right) => {
    if (left.resolved !== right.resolved) return left.resolved ? -1 : 1;
    return left.eventType.localeCompare(right.eventType);
  });
  return ordered.map((seed, index) => ({
    id: workflowPortId(direction, seed.eventType),
    eventType: seed.eventType,
    label: seed.label ?? humanizeEvent(seed.eventType),
    direction,
    side: direction === 'input' ? 'left' : 'right',
    origin: seed.origin,
    resolved: seed.resolved,
    unresolvedReason: seed.unresolvedReason,
    index,
    count: ordered.length,
  }));
}

/** Resolve visible typed sockets for one workflow node. */
export function nodePortCatalog(
  node: WorkflowNode,
  context: WorkflowPortCatalogContext = {},
): WorkflowNodePortCatalog {
  const contract = nodeContract(node, context.personas ?? []);
  addEdgePorts(node, context.edges ?? [], contract.inputs, contract.outputs, contract);
  if (
    node.kind === 'subworkflow' &&
    !(context.edges ?? []).some(
      (edge) => edge.source === node.id && Boolean(parseWorkflowEdgeLabel(edge.label)),
    )
  ) {
    appendSeed(contract.outputs, {
      eventType: 'children.completed',
      label: 'completed',
      origin: 'node',
      resolved: true,
    });
  }
  return {
    inputs: finishPorts(contract.inputs, 'input'),
    outputs: finishPorts(contract.outputs, 'output'),
  };
}

/** Resolve the exact sockets used by a saved edge without a node-centre fallback. */
export function resolveWorkflowEdgePorts(
  edge: WorkflowEdge,
  nodes: ReadonlyMap<string, WorkflowNode>,
  context: WorkflowPortCatalogContext = {},
): WorkflowEdgePortResolution {
  const source = nodes.get(edge.source);
  const target = nodes.get(edge.target);
  if (!source || !target) {
    return {
      edgeId: edge.id,
      sourcePort: null,
      targetPort: null,
      resolved: false,
      reason: !source
        ? `Unknown source node: ${edge.source}`
        : `Unknown target node: ${edge.target}`,
    };
  }
  const parsed = parseWorkflowEdgeLabel(edge.label);
  const catalogEdges = context.edges?.some((candidate) => candidate.id === edge.id)
    ? context.edges
    : [...(context.edges ?? []), edge];
  const sourceCatalog = nodePortCatalog(source, { ...context, edges: catalogEdges });
  const targetCatalog = nodePortCatalog(target, { ...context, edges: catalogEdges });
  const sourceEvent = parsed?.sourceEventType ?? `unresolved-edge:${edge.id}`;
  const targetEvent = parsed?.targetEventType ?? `unresolved-edge:${edge.id}`;
  const sourcePort = sourceCatalog.outputs.find((port) => port.eventType === sourceEvent) ?? null;
  const targetPort = targetCatalog.inputs.find((port) => port.eventType === targetEvent) ?? null;
  const resolved = Boolean(sourcePort?.resolved && targetPort?.resolved);
  return {
    edgeId: edge.id,
    sourcePort,
    targetPort,
    resolved,
    reason: resolved
      ? undefined
      : (sourcePort?.unresolvedReason ??
        targetPort?.unresolvedReason ??
        'The edge contract is unresolved.'),
  };
}
