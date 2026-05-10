import type { Workflow, WorkflowEdge, WorkflowStageNode } from './workflow';

export interface WorkflowModelCatalogEntry {
  vendor?: string;
}

export interface ParsedWorkflowEdgeLabel {
  sourceEventType: string;
  targetEventType: string;
}

export type WorkflowPersonaModelConflicts = Record<string, string[]>;

const REENTRY_EVENT_PATTERN =
  /(?:^|[._-])(changes[_-]?requested|retry|rework|requeue|rerun|reopen)(?:$|[._-])/i;

export function parseWorkflowEdgeLabel(label?: string | null): ParsedWorkflowEdgeLabel | null {
  if (!label) return null;
  const parts = label.split('->').map((part) => part.trim());
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return {
    sourceEventType: parts[0],
    targetEventType: parts[1],
  };
}

export function isReentryEventType(eventType?: string | null): boolean {
  if (!eventType) return false;
  return REENTRY_EVENT_PATTERN.test(eventType.trim().toLowerCase());
}

export function isReentryEdge(edge: Pick<WorkflowEdge, 'label'>): boolean {
  const parsed = parseWorkflowEdgeLabel(edge.label);
  if (!parsed) return false;
  return (
    isReentryEventType(parsed.sourceEventType) || isReentryEventType(parsed.targetEventType)
  );
}

export function stagePersonaIds(node: Pick<WorkflowStageNode, 'personaIds' | 'stageMembers'>) {
  const memberPersonaIds = (node.stageMembers ?? [])
    .map((member) => member.personaId)
    .filter((personaId): personaId is string => Boolean(personaId));

  if (memberPersonaIds.length > 0) {
    return [...new Set(memberPersonaIds)];
  }

  return [...new Set((node.personaIds ?? []).filter(Boolean))];
}

export function inferModelVendor(modelId: string): string {
  const normalized = modelId.trim().toLowerCase();
  if (!normalized) return '';
  if (normalized.startsWith('claude-')) return 'anthropic';
  if (
    normalized.startsWith('gpt-') ||
    normalized.startsWith('o1') ||
    normalized.startsWith('o3') ||
    normalized.startsWith('o4') ||
    normalized.startsWith('codex')
  ) {
    return 'openai';
  }
  if (normalized.includes(':')) return 'local';
  return '';
}

export function workflowStageModels(
  workflow: Pick<Workflow, 'nodes'>,
): string[] {
  const models: string[] = [];
  for (const node of workflow.nodes) {
    if (node.kind !== 'stage') continue;
    for (const member of node.stageMembers ?? []) {
      const model = member.model?.trim() ?? '';
      if (model) models.push(model);
    }
  }
  return models.filter(Boolean);
}

export function workflowModelVendors(
  workflow: Pick<Workflow, 'nodes'>,
  modelCatalog?: Record<string, WorkflowModelCatalogEntry>,
): Set<string> {
  const vendors = new Set<string>();
  for (const modelId of workflowStageModels(workflow)) {
    const vendor = modelCatalog?.[modelId]?.vendor || inferModelVendor(modelId);
    if (vendor) vendors.add(vendor);
  }
  return vendors;
}

export function workflowPersonaModelConflicts(
  workflow: Pick<Workflow, 'nodes'>,
): WorkflowPersonaModelConflicts {
  const conflicts = new Map<string, Set<string>>();
  for (const node of workflow.nodes) {
    if (node.kind !== 'stage') continue;
    for (const member of node.stageMembers ?? []) {
      const personaId = member.personaId?.trim();
      const model = member.model?.trim();
      if (!personaId || !model) continue;
      const models = conflicts.get(personaId) ?? new Set<string>();
      models.add(model);
      conflicts.set(personaId, models);
    }
  }

  return Object.fromEntries(
    [...conflicts.entries()]
      .filter(([, models]) => models.size > 1)
      .map(([personaId, models]) => [personaId, [...models].sort()]),
  );
}

export function normalizeStageNode(node: WorkflowStageNode): WorkflowStageNode {
  const nextPersonaIds = stagePersonaIds(node);
  const nextStageMembers = (node.stageMembers ?? []).map((member) => ({
    personaId: member.personaId,
    model: member.model ?? '',
    budget: member.budget ?? 40,
    consumesEventTypes: member.consumesEventTypes ?? [],
    eventFilters: member.eventFilters ?? {},
  }));
  const shouldFillExecutionDefaults =
    nextStageMembers.length > 0 ||
    node.executionMode !== undefined ||
    node.maxConcurrent !== undefined ||
    node.joinMode !== undefined;
  const nextExecutionMode = shouldFillExecutionDefaults
    ? (node.executionMode ?? 'parallel')
    : node.executionMode;
  const nextMaxConcurrent = shouldFillExecutionDefaults
    ? (node.maxConcurrent ?? 3)
    : node.maxConcurrent;
  const nextJoinMode = shouldFillExecutionDefaults ? (node.joinMode ?? 'all') : node.joinMode;

  const personaIdsChanged =
    nextPersonaIds.length !== (node.personaIds ?? []).length ||
    nextPersonaIds.some((personaId, index) => personaId !== (node.personaIds ?? [])[index]);

  if (
    !personaIdsChanged &&
    JSON.stringify(nextStageMembers) === JSON.stringify(node.stageMembers ?? []) &&
    node.executionMode === nextExecutionMode &&
    node.maxConcurrent === nextMaxConcurrent &&
    node.joinMode === nextJoinMode
  ) {
    return node;
  }

  return {
    ...node,
    stageMembers: nextStageMembers,
    personaIds: nextPersonaIds,
    executionMode: nextExecutionMode,
    maxConcurrent: nextMaxConcurrent,
    joinMode: nextJoinMode,
  };
}

export function normalizeWorkflowGraph(workflow: Workflow): Workflow {
  let changed = false;
  const nextNodes = workflow.nodes.map((node) => {
    if (node.kind !== 'stage') return node;
    const normalized = normalizeStageNode(node);
    if (normalized !== node) changed = true;
    return normalized;
  });

  if (!changed) return workflow;
  return { ...workflow, nodes: nextNodes };
}

export function structuralWorkflowEdges(edges: ReadonlyArray<WorkflowEdge>): WorkflowEdge[] {
  return edges.filter((edge) => !isReentryEdge(edge));
}
