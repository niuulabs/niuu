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
  if (node.kind === 'end') {
    return { inputs, outputs, openInputs: true, openOutputs: false, incomplete: false };
  }
  return { inputs, outputs, openInputs: false, openOutputs: false, incomplete: false };
}

function addEdgePorts(
  node: WorkflowNode,
  edges: readonly WorkflowEdge[],
  inputs: Map<string, PortSeed>,
  outputs: Map<string, PortSeed>,
  contract: { openInputs: boolean; openOutputs: boolean; incomplete: boolean },
): void {
  for (const edge of edges) {
    if (edge.source !== node.id && edge.target !== node.id) continue;
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
      if (edge.source === node.id) appendSeed(outputs, seed);
      if (edge.target === node.id) appendSeed(inputs, seed);
      continue;
    }

    if (edge.source === node.id && !outputs.has(parsed.sourceEventType)) {
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
    if (edge.target === node.id && !inputs.has(parsed.targetEventType)) {
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
